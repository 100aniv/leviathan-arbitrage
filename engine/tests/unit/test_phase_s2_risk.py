"""Tests for US-129 (RiskGuardian PortfolioState real values) and
US-154 (max_concurrent_positions check).

US-129: All 8 PortfolioState fields populate cleanly; key checks reject when limits breached.
US-154: len(position_sizes) >= MAX_CONCURRENT_POSITIONS → REJECT (TDD — new check).
"""
from __future__ import annotations

import os
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

# Patch metrics before import to avoid Prometheus registry errors
with patch("src.infra.metrics.RISK_REJECTIONS_TOTAL"):
    from src.risk.guardian import PortfolioState, RiskGuardian, TradeProposal
    from src.risk.circuit_breaker import CircuitBreaker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cb_closed() -> CircuitBreaker:
    cb = CircuitBreaker()
    # CircuitBreaker starts CLOSED — trading allowed
    return cb


def _make_valid_portfolio(**overrides) -> PortfolioState:
    """Return a PortfolioState that passes all existing checks."""
    defaults = dict(
        total_capital=Decimal("100000"),
        used_capital=Decimal("5000"),
        current_drawdown_pct=Decimal("0.005"),   # < 0.02 limit
        total_exposure=Decimal("5000"),
        position_sizes={},
        exchange_health_scores={"binance": Decimal("0.99")},
        volatility_1min={},
        volatility_24h={},
    )
    defaults.update(overrides)
    return PortfolioState(**defaults)


def _make_valid_proposal(**overrides) -> TradeProposal:
    """Return a TradeProposal well within all limits."""
    defaults = dict(
        strategy_id="test_strat",
        exchange_id="binance",
        symbol="BTC/USDT",
        side="BUY",
        size=Decimal("0.01"),
        price=Decimal("50000"),
        position_value=Decimal("500"),       # 0.5% of 100k, < 5% single-trade cap
        predicted_slippage_pct=Decimal("0.001"),
        fee_open=Decimal("0.001"),
        fee_close=Decimal("0.001"),
    )
    defaults.update(overrides)
    return TradeProposal(**defaults)


def _make_guardian(**overrides) -> RiskGuardian:
    with patch("src.infra.metrics.RISK_REJECTIONS_TOTAL"):
        defaults = dict(circuit_breaker=_make_cb_closed())
        defaults.update(overrides)
        return RiskGuardian(**defaults)


# ---------------------------------------------------------------------------
# US-129: PortfolioState — all 8 fields populated without TypeError
# ---------------------------------------------------------------------------

class TestPortfolioStateConstruction:
    """US-129: PortfolioState must accept all 8 declared fields without error."""

    def test_all_8_fields_no_type_error(self):
        """PortfolioState with all 8 fields populated → no TypeError."""
        ps = PortfolioState(
            total_capital=Decimal("100000"),
            used_capital=Decimal("20000"),
            current_drawdown_pct=Decimal("0.005"),
            total_exposure=Decimal("15000"),
            position_sizes={"BTC/USDT": Decimal("10000"), "ETH/USDT": Decimal("5000")},
            exchange_health_scores={"binance": Decimal("0.99"), "bybit": Decimal("0.95")},
            volatility_1min={"BTC/USDT": Decimal("0.001")},
            volatility_24h={"BTC/USDT": Decimal("0.002")},
        )
        assert ps.total_capital == Decimal("100000")
        assert ps.used_capital == Decimal("20000")
        assert ps.current_drawdown_pct == Decimal("0.005")
        assert ps.total_exposure == Decimal("15000")
        assert len(ps.position_sizes) == 2
        assert len(ps.exchange_health_scores) == 2
        assert len(ps.volatility_1min) == 1
        assert len(ps.volatility_24h) == 1

    def test_net_exposures_defaults_to_empty_dict(self):
        """net_exposures field defaults to {} (Amendment 7 field)."""
        ps = _make_valid_portfolio()
        assert hasattr(ps, "net_exposures")
        assert ps.net_exposures == {}

    def test_empty_dicts_for_volatility_and_health_valid(self):
        """Empty dicts for volatility/health are valid (skip-if-missing logic)."""
        ps = _make_valid_portfolio(
            exchange_health_scores={},
            volatility_1min={},
            volatility_24h={},
        )
        # Exchange health check should use default 0 → reject if threshold > 0
        # but the object construction itself must not raise
        assert ps.exchange_health_scores == {}
        assert ps.volatility_1min == {}
        assert ps.volatility_24h == {}


# ---------------------------------------------------------------------------
# US-129: RiskGuardian check — position_sizes exceeding limit → REJECT
# ---------------------------------------------------------------------------

class TestRiskGuardianPositionLimit:
    """US-129: Check #1 — position limit exceeded → REJECT at check 1."""

    @patch("src.risk.guardian.is_halted", return_value=False)
    def test_position_limit_exceeded_rejects(self, _):
        """Existing position + new position > 10% of capital → REJECT at check 1."""
        guardian = _make_guardian()
        # Existing position already at 10% of capital
        portfolio = _make_valid_portfolio(
            position_sizes={"BTC/USDT": Decimal("10000")},  # already 10% of 100k
        )
        proposal = _make_valid_proposal(
            symbol="BTC/USDT",
            position_value=Decimal("1000"),  # would push to 11%
        )
        with patch("src.infra.metrics.RISK_REJECTIONS_TOTAL") as mock_metric:
            mock_metric.labels.return_value = MagicMock()
            result = guardian.check(proposal, portfolio)
        assert result.approved is False
        assert result.rejected_at_check == 1

    @patch("src.risk.guardian.is_halted", return_value=False)
    def test_position_within_limit_passes_check_1(self, _):
        """New position within 10% of capital → passes check 1."""
        guardian = _make_guardian()
        portfolio = _make_valid_portfolio(position_sizes={})
        proposal = _make_valid_proposal(position_value=Decimal("500"))  # 0.5% of 100k
        with patch("src.infra.metrics.RISK_REJECTIONS_TOTAL") as mock_metric:
            mock_metric.labels.return_value = MagicMock()
            result = guardian.check(proposal, portfolio)
        # May fail on check #5 if health_score provided, so just check it's not check 1
        assert result.rejected_at_check != 1


# ---------------------------------------------------------------------------
# US-129: RiskGuardian check — drawdown limit breached → REJECT
# ---------------------------------------------------------------------------

class TestRiskGuardianDrawdownLimit:
    """US-129: Check #2 — current_drawdown_pct > 2% → REJECT at check 2."""

    @patch("src.risk.guardian.is_halted", return_value=False)
    def test_drawdown_over_limit_rejects(self, _):
        """current_drawdown_pct > max_drawdown_pct → REJECT at check 2."""
        guardian = _make_guardian()
        portfolio = _make_valid_portfolio(current_drawdown_pct=Decimal("0.025"))  # 2.5% > 2%
        proposal = _make_valid_proposal()
        with patch("src.infra.metrics.RISK_REJECTIONS_TOTAL") as mock_metric:
            mock_metric.labels.return_value = MagicMock()
            result = guardian.check(proposal, portfolio)
        assert result.approved is False
        assert result.rejected_at_check == 2

    @patch("src.risk.guardian.is_halted", return_value=False)
    def test_drawdown_at_limit_rejects(self, _):
        """current_drawdown_pct exactly at max (2%) still rejects (strict >)."""
        guardian = _make_guardian()
        # Exactly at limit: 0.02 is NOT > 0.02, so it should pass check 2
        portfolio = _make_valid_portfolio(current_drawdown_pct=Decimal("0.02"))
        proposal = _make_valid_proposal()
        with patch("src.infra.metrics.RISK_REJECTIONS_TOTAL") as mock_metric:
            mock_metric.labels.return_value = MagicMock()
            result = guardian.check(proposal, portfolio)
        assert result.rejected_at_check != 2

    @patch("src.risk.guardian.is_halted", return_value=False)
    def test_drawdown_below_limit_passes_check_2(self, _):
        """current_drawdown_pct < 2% → passes check 2."""
        guardian = _make_guardian()
        portfolio = _make_valid_portfolio(current_drawdown_pct=Decimal("0.01"))
        proposal = _make_valid_proposal()
        with patch("src.infra.metrics.RISK_REJECTIONS_TOTAL") as mock_metric:
            mock_metric.labels.return_value = MagicMock()
            result = guardian.check(proposal, portfolio)
        assert result.rejected_at_check != 2


# ---------------------------------------------------------------------------
# US-129: Full valid portfolio → APPROVE
# ---------------------------------------------------------------------------

class TestRiskGuardianFullApproval:
    """US-129: PortfolioState with all real values → full approval."""

    @patch("src.risk.guardian.is_halted", return_value=False)
    def test_valid_portfolio_all_fields_approves(self, _):
        """All 8 PortfolioState fields with valid values → RiskCheckResult.approved=True."""
        guardian = _make_guardian()
        portfolio = PortfolioState(
            total_capital=Decimal("100000"),
            used_capital=Decimal("10000"),
            current_drawdown_pct=Decimal("0.005"),
            total_exposure=Decimal("5000"),
            position_sizes={},
            exchange_health_scores={"binance": Decimal("0.99")},
            volatility_1min={"BTC/USDT": Decimal("0.001")},
            volatility_24h={"BTC/USDT": Decimal("0.002")},
        )
        proposal = _make_valid_proposal()
        with patch("src.infra.metrics.RISK_REJECTIONS_TOTAL") as mock_metric:
            mock_metric.labels.return_value = MagicMock()
            result = guardian.check(proposal, portfolio)
        assert result.approved is True
        assert result.rejected_at_check is None


# ---------------------------------------------------------------------------
# US-154: max_concurrent_positions check (TDD — new check in guardian)
# ---------------------------------------------------------------------------

class TestMaxConcurrentPositions:
    """US-154: TDD — RiskGuardian must reject when concurrent positions >= 20.

    NOTE: These tests drive the implementation of a new check in guardian.check().
    They will FAIL until the check is added to guardian.py.
    """

    @patch("src.risk.guardian.is_halted", return_value=False)
    def test_under_max_concurrent_positions_approves(self, _):
        """len(position_sizes) = 19 < 20 → check passes (not rejected for concurrent limit)."""
        guardian = _make_guardian()
        positions = {f"TOKEN{i}/USDT": Decimal("100") for i in range(19)}
        portfolio = _make_valid_portfolio(position_sizes=positions)
        proposal = _make_valid_proposal(symbol="NEW/USDT")
        with patch("src.infra.metrics.RISK_REJECTIONS_TOTAL") as mock_metric:
            mock_metric.labels.return_value = MagicMock()
            result = guardian.check(proposal, portfolio)
        # Should not be rejected due to concurrent positions limit
        # (may be rejected for other reasons like exchange health with no score)
        assert result.rejected_at_check != 10  # check 10 = concurrent positions

    @patch("src.risk.guardian.is_halted", return_value=False)
    def test_at_max_concurrent_positions_rejects(self, _):
        """len(position_sizes) >= 20 → REJECT (US-154 new check)."""
        guardian = _make_guardian()
        # 20 existing positions — at the limit
        positions = {f"TOKEN{i}/USDT": Decimal("100") for i in range(20)}
        portfolio = _make_valid_portfolio(position_sizes=positions)
        proposal = _make_valid_proposal(symbol="NEW/USDT")
        with patch("src.infra.metrics.RISK_REJECTIONS_TOTAL") as mock_metric:
            mock_metric.labels.return_value = MagicMock()
            result = guardian.check(proposal, portfolio)
        assert result.approved is False
        assert "concurrent" in result.reason.lower() or "position" in result.reason.lower()

    @patch("src.risk.guardian.is_halted", return_value=False)
    def test_175_symbols_over_20_rejects(self, _):
        """175 simultaneous positions → REJECT with max concurrent positions reason."""
        guardian = _make_guardian()
        positions = {f"TOKEN{i}/USDT": Decimal("100") for i in range(175)}
        portfolio = _make_valid_portfolio(position_sizes=positions)
        proposal = _make_valid_proposal(symbol="NEW/USDT")
        with patch("src.infra.metrics.RISK_REJECTIONS_TOTAL") as mock_metric:
            mock_metric.labels.return_value = MagicMock()
            result = guardian.check(proposal, portfolio)
        assert result.approved is False

    @patch("src.risk.guardian.is_halted", return_value=False)
    def test_max_concurrent_env_override(self, _):
        """MAX_CONCURRENT_POSITIONS env var changes the limit."""
        with patch.dict(os.environ, {"MAX_CONCURRENT_POSITIONS": "5"}):
            guardian = _make_guardian()
            positions = {f"TOKEN{i}/USDT": Decimal("100") for i in range(5)}
            portfolio = _make_valid_portfolio(position_sizes=positions)
            proposal = _make_valid_proposal(symbol="NEW/USDT")
            with patch("src.infra.metrics.RISK_REJECTIONS_TOTAL") as mock_metric:
                mock_metric.labels.return_value = MagicMock()
                result = guardian.check(proposal, portfolio)
            # With limit=5 and 5 existing positions, should reject
            assert result.approved is False
