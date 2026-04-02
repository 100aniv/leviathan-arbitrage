"""Tests for engine/src/risk/guardian.py

Tests all 9 checks, especially:
- Check #0 (halt) cannot be bypassed (Amendment 1E)
- Check #4 enhanced (correlation check, Amendment 7 Scenario 5)
- Check #8 (max rollback cost gate, Amendment 3C)
- Prometheus RISK_REJECTIONS_TOTAL counter increments
"""
from __future__ import annotations

from decimal import Decimal

import pytest

import src.risk.kill_switch as ks_module
from src.infra.metrics import RISK_REJECTIONS_TOTAL
from src.risk.circuit_breaker import CircuitBreaker, CircuitBreakerState
from src.risk.guardian import PortfolioState, RiskGuardian, TradeProposal
from src.risk.kill_switch import _HALT_FLAG


@pytest.fixture(autouse=True)
def reset_halt_flag():
    """Reset global halt flag before and after each test."""
    _HALT_FLAG.clear()
    yield
    _HALT_FLAG.clear()


def make_guardian(**kwargs) -> RiskGuardian:
    cb = CircuitBreaker()
    return RiskGuardian(circuit_breaker=cb, **kwargs)


def make_proposal(**kwargs) -> TradeProposal:
    defaults = dict(
        strategy_id="test_strategy",
        exchange_id="binance",
        symbol="BTC/USDT",
        side="BUY",
        size=Decimal("0.1"),
        price=Decimal("50000"),
        position_value=Decimal("5000"),
        predicted_slippage_pct=Decimal("0.001"),
        fee_open=Decimal("0.0005"),
        fee_close=Decimal("0.0005"),
    )
    defaults.update(kwargs)
    return TradeProposal(**defaults)


def make_portfolio(**kwargs) -> PortfolioState:
    defaults = dict(
        total_capital=Decimal("100000"),
        used_capital=Decimal("0"),
        current_drawdown_pct=Decimal("0"),
        total_exposure=Decimal("0"),
        position_sizes={},
        exchange_health_scores={"binance": Decimal("1.0"), "okx": Decimal("1.0")},
        volatility_1min={},
        volatility_24h={},
    )
    defaults.update(kwargs)
    return PortfolioState(**defaults)


# ---------------------------------------------------------------------------
# Check #0 — Halt (Amendment 1E) — CANNOT be bypassed
# ---------------------------------------------------------------------------


class TestCheck0Halt:
    def test_halted_engine_rejects_any_trade(self):
        """Check #0: halt check cannot be bypassed under any circumstances."""
        ks_module._HALT_FLAG.set()
        guardian = make_guardian()
        result = guardian.check(make_proposal(), make_portfolio())
        assert not result.approved
        assert result.rejected_at_check == 0
        assert "halted" in result.reason.lower()

    def test_halt_check_runs_before_all_other_checks(self):
        """Even with perfect portfolio, halt overrides everything."""
        ks_module._HALT_FLAG.set()
        guardian = make_guardian()
        # Perfect portfolio — all other checks would pass
        result = guardian.check(make_proposal(), make_portfolio())
        assert result.rejected_at_check == 0

    def test_not_halted_does_not_trigger_check_0(self):
        """When not halted, check #0 passes and later checks run."""
        assert not ks_module.is_halted()
        guardian = make_guardian()
        result = guardian.check(make_proposal(), make_portfolio())
        # May pass or fail at later check, but NOT at check #0
        assert result.rejected_at_check != 0 or result.approved


# ---------------------------------------------------------------------------
# Check #1 — Position Limit
# ---------------------------------------------------------------------------


class TestCheck1PositionLimit:
    def test_exceeds_position_limit_rejected(self):
        guardian = make_guardian(max_position_pct=Decimal("0.10"))
        # 100k capital, 10% = 10k max. Proposal is 11k.
        proposal = make_proposal(position_value=Decimal("11000"))
        result = guardian.check(proposal, make_portfolio())
        assert not result.approved
        assert result.rejected_at_check == 1

    def test_within_position_limit_passes(self):
        guardian = make_guardian(max_position_pct=Decimal("0.10"))
        proposal = make_proposal(position_value=Decimal("9000"))
        result = guardian.check(proposal, make_portfolio())
        assert result.rejected_at_check != 1

    def test_existing_position_adds_to_total(self):
        """Existing position in same symbol is accumulated."""
        guardian = make_guardian(max_position_pct=Decimal("0.10"))
        proposal = make_proposal(position_value=Decimal("6000"))
        portfolio = make_portfolio(
            position_sizes={"BTC/USDT": Decimal("5000")}  # already 5k
        )
        # 5k + 6k = 11k > 10k limit
        result = guardian.check(proposal, portfolio)
        assert not result.approved
        assert result.rejected_at_check == 1

    def test_different_symbol_not_counted(self):
        guardian = make_guardian(max_position_pct=Decimal("0.10"))
        proposal = make_proposal(symbol="BTC/USDT", position_value=Decimal("8000"))
        portfolio = make_portfolio(
            position_sizes={"ETH/USDT": Decimal("5000")}  # different symbol
        )
        result = guardian.check(proposal, portfolio)
        assert result.rejected_at_check != 1  # 8k < 10k limit


# ---------------------------------------------------------------------------
# Check #2 — Drawdown Limit
# ---------------------------------------------------------------------------


class TestCheck2DrawdownLimit:
    def test_drawdown_exceeded_rejected(self):
        guardian = make_guardian(max_drawdown_pct=Decimal("0.02"))
        portfolio = make_portfolio(current_drawdown_pct=Decimal("0.025"))
        result = guardian.check(make_proposal(), portfolio)
        assert not result.approved
        assert result.rejected_at_check == 2

    def test_drawdown_within_limit_passes(self):
        guardian = make_guardian(max_drawdown_pct=Decimal("0.02"))
        portfolio = make_portfolio(current_drawdown_pct=Decimal("0.01"))
        result = guardian.check(make_proposal(), portfolio)
        assert result.rejected_at_check != 2

    def test_zero_drawdown_passes(self):
        guardian = make_guardian(max_drawdown_pct=Decimal("0.02"))
        portfolio = make_portfolio(current_drawdown_pct=Decimal("0"))
        result = guardian.check(make_proposal(), portfolio)
        assert result.rejected_at_check != 2


# ---------------------------------------------------------------------------
# Check #3 — Exposure Limit
# ---------------------------------------------------------------------------


class TestCheck3ExposureLimit:
    def test_exposure_exceeded_rejected(self):
        guardian = make_guardian(max_exposure_pct=Decimal("0.30"))
        # 100k * 30% = 30k limit. Existing 28k + proposed 5k = 33k.
        portfolio = make_portfolio(total_exposure=Decimal("28000"))
        result = guardian.check(make_proposal(position_value=Decimal("5000")), portfolio)
        assert not result.approved
        assert result.rejected_at_check == 3

    def test_exposure_within_limit_passes(self):
        guardian = make_guardian(max_exposure_pct=Decimal("0.30"))
        portfolio = make_portfolio(total_exposure=Decimal("10000"))
        result = guardian.check(make_proposal(position_value=Decimal("5000")), portfolio)
        assert result.rejected_at_check != 3


# ---------------------------------------------------------------------------
# Check #4 — Circuit Breaker State
# ---------------------------------------------------------------------------


class TestCheck4CircuitBreaker:
    async def test_open_circuit_breaker_rejected(self):
        cb = CircuitBreaker(consecutive_loss_limit=1)
        await cb.record_loss()
        assert cb.state == CircuitBreakerState.OPEN

        guardian = RiskGuardian(circuit_breaker=cb)
        result = guardian.check(make_proposal(), make_portfolio())
        assert not result.approved
        assert result.rejected_at_check == 4

    def test_closed_circuit_breaker_passes(self):
        cb = CircuitBreaker()
        guardian = RiskGuardian(circuit_breaker=cb)
        result = guardian.check(make_proposal(), make_portfolio())
        assert result.rejected_at_check != 4


# ---------------------------------------------------------------------------
# Check #5 — Exchange Health Score
# ---------------------------------------------------------------------------


class TestCheck5ExchangeHealth:
    def test_unhealthy_exchange_rejected(self):
        # warmup_seconds=0 to disable cold-start grace period in this test
        guardian = make_guardian(exchange_health_threshold=Decimal("0.90"), warmup_seconds=0)
        portfolio = make_portfolio(
            exchange_health_scores={"binance": Decimal("0.85")}
        )
        result = guardian.check(make_proposal(), portfolio)
        assert not result.approved
        assert result.rejected_at_check == 5

    def test_healthy_exchange_passes(self):
        guardian = make_guardian(exchange_health_threshold=Decimal("0.90"), warmup_seconds=0)
        portfolio = make_portfolio(
            exchange_health_scores={"binance": Decimal("0.95")}
        )
        result = guardian.check(make_proposal(), portfolio)
        assert result.rejected_at_check != 5

    def test_missing_exchange_score_treated_as_zero(self):
        # warmup_seconds=0 to disable cold-start grace period in this test
        guardian = make_guardian(exchange_health_threshold=Decimal("0.90"), warmup_seconds=0)
        portfolio = make_portfolio(exchange_health_scores={})  # no binance score
        result = guardian.check(make_proposal(exchange_id="binance"), portfolio)
        assert not result.approved
        assert result.rejected_at_check == 5

    def test_warmup_period_bypasses_health_check(self):
        """During warm-up, unhealthy exchange score does NOT block trades."""
        # warmup_seconds=9999 simulates being inside warm-up window
        guardian = make_guardian(
            exchange_health_threshold=Decimal("0.90"),
            warmup_seconds=9999,
        )
        portfolio = make_portfolio(
            exchange_health_scores={"binance": Decimal("0.0")}  # worst possible score
        )
        result = guardian.check(make_proposal(), portfolio)
        # Check#5 must NOT fire during warm-up
        assert result.rejected_at_check != 5

    def test_after_warmup_health_check_enforced(self):
        """After warm-up expires, health check is enforced normally."""
        # warmup_seconds=0 means warm-up is already over
        guardian = make_guardian(
            exchange_health_threshold=Decimal("0.90"),
            warmup_seconds=0,
        )
        portfolio = make_portfolio(
            exchange_health_scores={"binance": Decimal("0.85")}
        )
        result = guardian.check(make_proposal(), portfolio)
        assert not result.approved
        assert result.rejected_at_check == 5


# ---------------------------------------------------------------------------
# Check #6 — Max Single Trade Size
# ---------------------------------------------------------------------------


class TestCheck6MaxSingleTrade:
    def test_oversized_trade_rejected(self):
        guardian = make_guardian(max_single_trade_pct=Decimal("0.05"))
        # 100k * 5% = 5k max. Proposal is 6k.
        proposal = make_proposal(position_value=Decimal("6000"))
        result = guardian.check(proposal, make_portfolio())
        assert not result.approved
        assert result.rejected_at_check == 6

    def test_within_trade_limit_passes(self):
        guardian = make_guardian(max_single_trade_pct=Decimal("0.05"))
        proposal = make_proposal(position_value=Decimal("4000"))
        result = guardian.check(proposal, make_portfolio())
        assert result.rejected_at_check != 6


# ---------------------------------------------------------------------------
# Check #7 — Volatility Check
# ---------------------------------------------------------------------------


class TestCheck7Volatility:
    def test_high_volatility_ratio_rejected(self):
        guardian = make_guardian(max_volatility_multiple=Decimal("2.0"))
        portfolio = make_portfolio(
            volatility_1min={"BTC/USDT": Decimal("0.05")},
            volatility_24h={"BTC/USDT": Decimal("0.02")},  # ratio = 2.5 > 2.0
        )
        result = guardian.check(make_proposal(), portfolio)
        assert not result.approved
        assert result.rejected_at_check == 7

    def test_acceptable_volatility_passes(self):
        guardian = make_guardian(max_volatility_multiple=Decimal("2.0"))
        portfolio = make_portfolio(
            volatility_1min={"BTC/USDT": Decimal("0.02")},
            volatility_24h={"BTC/USDT": Decimal("0.02")},  # ratio = 1.0 < 2.0
        )
        result = guardian.check(make_proposal(), portfolio)
        assert result.rejected_at_check != 7

    def test_missing_vol_data_skips_check(self):
        """No volatility data → check #7 is skipped."""
        guardian = make_guardian(max_volatility_multiple=Decimal("2.0"))
        result = guardian.check(make_proposal(), make_portfolio())
        assert result.rejected_at_check != 7

    def test_zero_vol_24h_skips_check(self):
        guardian = make_guardian(max_volatility_multiple=Decimal("2.0"))
        portfolio = make_portfolio(
            volatility_1min={"BTC/USDT": Decimal("0.05")},
            volatility_24h={"BTC/USDT": Decimal("0")},  # division by zero guard
        )
        result = guardian.check(make_proposal(), portfolio)
        assert result.rejected_at_check != 7


# ---------------------------------------------------------------------------
# Check #8 — Max Rollback Cost Gate (Amendment 3C)
# ---------------------------------------------------------------------------


class TestCheck8RollbackCostGate:
    def test_high_rollback_cost_rejected(self):
        """Amendment 3C: reject if max_rollback_cost > 2% of position value."""
        guardian = make_guardian(max_rollback_threshold=Decimal("0.02"))
        # position_value=1000, predicted_slippage=0.02
        # worst_case_slippage = 3 * 0.02 = 0.06
        # round_trip_fees = 0.01 + 0.01 = 0.02 (percentage rates)
        # max_rollback_cost = 1000 * (0.06 + 0.02) = 80
        # threshold = 1000 * 0.02 = 20
        # 80 > 20 → REJECT
        proposal = make_proposal(
            position_value=Decimal("1000"),
            predicted_slippage_pct=Decimal("0.02"),
            fee_open=Decimal("0.01"),
            fee_close=Decimal("0.01"),
        )
        result = guardian.check(proposal, make_portfolio())
        assert not result.approved
        assert result.rejected_at_check == 8

    def test_acceptable_rollback_cost_passes(self):
        """Low slippage + high position value → rollback cost within 2% threshold."""
        guardian = make_guardian(max_rollback_threshold=Decimal("0.02"))
        # position_value=10000, predicted_slippage=0.001
        # worst_case_slippage = 3 * 0.001 = 0.003
        # round_trip_fees = 0.0005 + 0.0005 = 0.001 (percentage rates)
        # max_rollback_cost = 10000 * (0.003 + 0.001) = 40
        # threshold = 10000 * 0.02 = 200
        # 40 < 200 → APPROVE
        # Use total_capital=500k so check #6 (5% = 25k) passes for 10k trade
        proposal = make_proposal(
            position_value=Decimal("10000"),
            predicted_slippage_pct=Decimal("0.001"),
            fee_open=Decimal("0.0005"),
            fee_close=Decimal("0.0005"),
        )
        portfolio = make_portfolio(total_capital=Decimal("500000"))
        result = guardian.check(proposal, portfolio)
        assert result.approved

    def test_rollback_threshold_is_configurable(self):
        """Production uses 1% threshold instead of 2%."""
        guardian = make_guardian(max_rollback_threshold=Decimal("0.01"))
        # position_value=10000, predicted_slippage=0.002
        # worst_case_slippage = 3 * 0.002 = 0.006
        # round_trip_fees = 0.0005 + 0.0005 = 0.001 (percentage rates)
        # max_rollback_cost = 10000 * (0.006 + 0.001) = 70
        # threshold at 1% = 10000 * 0.01 = 100. 70 < 100 → passes
        # Use total_capital=500k so check #6 passes
        proposal = make_proposal(
            position_value=Decimal("10000"),
            predicted_slippage_pct=Decimal("0.002"),
            fee_open=Decimal("0.0005"),
            fee_close=Decimal("0.0005"),
        )
        portfolio = make_portfolio(total_capital=Decimal("500000"))
        result = guardian.check(proposal, portfolio)
        assert result.approved


# ---------------------------------------------------------------------------
# All checks passing
# ---------------------------------------------------------------------------


class TestAllChecksPassing:
    def test_all_checks_pass_returns_approved(self):
        guardian = make_guardian()
        proposal = make_proposal(
            position_value=Decimal("5000"),
            predicted_slippage_pct=Decimal("0.001"),
            fee_open=Decimal("0.0005"),
            fee_close=Decimal("0.0005"),
        )
        result = guardian.check(proposal, make_portfolio())
        assert result.approved
        assert result.rejected_at_check is None
        assert result.reason == ""


# ---------------------------------------------------------------------------
# Check #4 enhanced — Net Exposure Correlation (Amendment 7 Scenario 5)
# ---------------------------------------------------------------------------


class TestCheck4NetExposureCorrelation:
    def test_net_exposure_exceeded_rejected_at_check_4(self):
        """Amendment 7: excessive net exposure rejects at check #4."""
        guardian = make_guardian(max_net_exposure_per_asset=Decimal("1.0"))
        proposal = make_proposal(side="BUY", size=Decimal("1.5"))
        # Current net exposure is already 0.8 BTC; adding 1.5 = 2.3 > 1.0
        portfolio = make_portfolio(
            net_exposures={("binance", "BTC"): Decimal("0.8")}
        )
        result = guardian.check(proposal, portfolio)
        assert not result.approved
        assert result.rejected_at_check == 4
        assert "Amendment 7" in result.reason

    def test_sell_reducing_net_exposure_passes(self):
        """SELL that brings net exposure within limits should pass."""
        guardian = make_guardian(max_net_exposure_per_asset=Decimal("1.0"))
        # Current net: 2.0 (over limit). SELL 1.5 → hypothetical = 0.5 ≤ 1.0
        proposal = make_proposal(side="SELL", size=Decimal("1.5"))
        portfolio = make_portfolio(
            net_exposures={("binance", "BTC"): Decimal("2.0")}
        )
        result = guardian.check(proposal, portfolio)
        # Should not be rejected at check #4 for correlation
        assert result.rejected_at_check != 4 or result.reason == (
            f"Circuit breaker is {result.reason}"  # only CB can trigger #4 here
        )

    def test_zero_max_net_exposure_disables_check(self):
        """max_net_exposure_per_asset=0 disables the correlation check."""
        guardian = make_guardian(max_net_exposure_per_asset=Decimal("0"))
        proposal = make_proposal(side="BUY", size=Decimal("999"))
        portfolio = make_portfolio(
            net_exposures={("binance", "BTC"): Decimal("999")}
        )
        # Even with huge hypothetical exposure, check is disabled
        result = guardian.check(proposal, portfolio)
        # Should NOT be rejected for net exposure correlation
        if not result.approved:
            assert "Amendment 7" not in result.reason

    def test_zero_existing_exposure_within_limit_passes(self):
        """No existing exposure + small trade passes correlation check."""
        guardian = make_guardian(max_net_exposure_per_asset=Decimal("1.0"))
        proposal = make_proposal(side="BUY", size=Decimal("0.5"))
        result = guardian.check(proposal, make_portfolio())
        # Should pass correlation check (0 + 0.5 = 0.5 ≤ 1.0)
        if not result.approved:
            assert "Amendment 7" not in result.reason

    def test_net_short_also_checked(self):
        """Negative net exposure exceeding limit is also rejected."""
        guardian = make_guardian(max_net_exposure_per_asset=Decimal("1.0"))
        proposal = make_proposal(side="SELL", size=Decimal("0.5"))
        portfolio = make_portfolio(
            net_exposures={("binance", "BTC"): Decimal("-0.8")}
        )
        # |-0.8 - 0.5| = 1.3 > 1.0 → reject
        result = guardian.check(proposal, portfolio)
        assert not result.approved
        assert result.rejected_at_check == 4
        assert "Amendment 7" in result.reason

    def test_correlation_check_uses_proposal_exchange(self):
        """Net exposure on a different exchange should not block the trade."""
        guardian = make_guardian(max_net_exposure_per_asset=Decimal("1.0"))
        # Small 0.4 BUY on Binance — fine on its own (0 + 0.4 = 0.4 ≤ 1.0)
        proposal = make_proposal(side="BUY", size=Decimal("0.4"), exchange_id="binance")
        # OKX has huge exposure but that should NOT block the Binance trade
        portfolio = make_portfolio(
            net_exposures={("okx", "BTC"): Decimal("5.0")}
        )
        result = guardian.check(proposal, portfolio)
        # Should not be rejected for Amendment 7 correlation (Binance has 0 + 0.4 = 0.4)
        assert "Amendment 7" not in result.reason

    def test_cb_open_rejected_before_correlation_check(self):
        """Circuit breaker OPEN is checked before correlation (both at check #4)."""
        cb = CircuitBreaker(consecutive_loss_limit=1)

        async def _setup():
            await cb.record_loss()

        import asyncio
        asyncio.run(_setup())

        guardian = RiskGuardian(
            circuit_breaker=cb,
            max_net_exposure_per_asset=Decimal("1.0"),
        )
        proposal = make_proposal(side="BUY", size=Decimal("0.5"))
        result = guardian.check(proposal, make_portfolio())
        assert not result.approved
        assert result.rejected_at_check == 4
        assert "Circuit breaker" in result.reason


# ---------------------------------------------------------------------------
# Prometheus counter — RISK_REJECTIONS_TOTAL
# ---------------------------------------------------------------------------


class TestPrometheusCounters:
    def _get_count(self, check_number: str, reason: str) -> float:
        """Read current counter value for a specific label set."""
        try:
            return RISK_REJECTIONS_TOTAL.labels(
                check_number=check_number, reason=reason
            )._value.get()
        except Exception:
            return 0.0

    def test_check_0_halt_increments_counter(self):
        ks_module._HALT_FLAG.set()
        before = self._get_count("0", "engine_halted")
        guardian = make_guardian()
        guardian.check(make_proposal(), make_portfolio())
        after = self._get_count("0", "engine_halted")
        assert after == before + 1

    def test_check_1_position_limit_increments_counter(self):
        guardian = make_guardian(max_position_pct=Decimal("0.10"))
        proposal = make_proposal(position_value=Decimal("11000"))
        before = self._get_count("1", "position_limit")
        guardian.check(proposal, make_portfolio())
        after = self._get_count("1", "position_limit")
        assert after == before + 1

    def test_check_8_rollback_increments_counter(self):
        guardian = make_guardian(max_rollback_threshold=Decimal("0.02"))
        proposal = make_proposal(
            position_value=Decimal("1000"),
            predicted_slippage_pct=Decimal("0.02"),
            fee_open=Decimal("0.01"),
            fee_close=Decimal("0.01"),
        )
        before = self._get_count("8", "rollback_cost_exceeded")
        guardian.check(proposal, make_portfolio())
        after = self._get_count("8", "rollback_cost_exceeded")
        assert after == before + 1

    def test_check_4_correlation_increments_counter(self):
        guardian = make_guardian(max_net_exposure_per_asset=Decimal("1.0"))
        proposal = make_proposal(side="BUY", size=Decimal("1.5"))
        portfolio = make_portfolio(
            net_exposures={("binance", "BTC"): Decimal("0.8")}
        )
        before = self._get_count("4", "net_exposure_exceeded")
        guardian.check(proposal, portfolio)
        after = self._get_count("4", "net_exposure_exceeded")
        assert after == before + 1

    def test_approved_trade_does_not_increment_any_counter(self):
        guardian = make_guardian()
        proposal = make_proposal(
            position_value=Decimal("5000"),
            predicted_slippage_pct=Decimal("0.001"),
        )
        # Record all relevant counters before
        checks = [
            ("0", "engine_halted"),
            ("1", "position_limit"),
            ("2", "drawdown_limit"),
            ("3", "exposure_limit"),
            ("4", "circuit_breaker_open"),
            ("4", "net_exposure_exceeded"),
            ("5", "exchange_health_low"),
            ("6", "trade_size_exceeded"),
            ("7", "volatility_too_high"),
            ("8", "rollback_cost_exceeded"),
        ]
        before = {lbl: self._get_count(*lbl) for lbl in checks}
        result = guardian.check(proposal, make_portfolio())
        assert result.approved
        after = {lbl: self._get_count(*lbl) for lbl in checks}
        assert before == after
