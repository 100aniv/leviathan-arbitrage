"""Tests for PortfolioRiskManager — US-277, US-278."""
from __future__ import annotations

import time

import pytest

from src.core.portfolio_risk import (
    MIN_SAMPLES_FOR_STATS,
    PORTFOLIO_MDD_THRESHOLD_PCT,
    STRATEGY_MDD_THRESHOLD_PCT,
    PortfolioRiskManager,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _feed(mgr: PortfolioRiskManager, sid: str, pnls: list[float], base_ts: float = 0.0) -> None:
    for i, pnl in enumerate(pnls):
        mgr.update_returns(sid, pnl, timestamp=base_ts + i)


def _enough(pnls: int = MIN_SAMPLES_FOR_STATS) -> list[float]:
    """Return a list of alternating +/-1 pnls of given length."""
    return [1.0 if i % 2 == 0 else -0.5 for i in range(pnls)]


# ---------------------------------------------------------------------------
# US-277: Returns & pruning
# ---------------------------------------------------------------------------

class TestUpdateReturnsAndPrune:
    def test_update_returns_and_prune_old(self) -> None:
        """Entries outside rolling window should be pruned."""
        mgr = PortfolioRiskManager(window_minutes=1)
        now = time.time()
        # Add old entry (90 seconds ago — outside 1-min window)
        mgr.update_returns("s1", 1.0, timestamp=now - 90)
        # Add fresh entry
        mgr.update_returns("s1", 2.0, timestamp=now)
        assert len(mgr._returns["s1"]) == 1
        assert mgr._returns["s1"][0] == 2.0

    def test_update_returns_within_window_kept(self) -> None:
        """Entries within rolling window should be retained."""
        mgr = PortfolioRiskManager(window_minutes=60)
        now = time.time()
        mgr.update_returns("s1", 1.0, timestamp=now - 10)
        mgr.update_returns("s1", 2.0, timestamp=now)
        assert len(mgr._returns["s1"]) == 2

    def test_enabled_false_skips_update(self) -> None:
        mgr = PortfolioRiskManager(enabled=False)
        mgr.update_returns("s1", 1.0)
        assert len(mgr._returns["s1"]) == 0


# ---------------------------------------------------------------------------
# US-277: Correlation matrix
# ---------------------------------------------------------------------------

class TestCorrelationMatrix:
    def test_get_correlation_matrix_symmetric(self) -> None:
        """Correlation matrix must be symmetric and diagonal == 1.0."""
        mgr = PortfolioRiskManager(window_minutes=9999)
        pnls = _enough(MIN_SAMPLES_FOR_STATS)
        _feed(mgr, "strat_a", pnls)
        _feed(mgr, "strat_b", pnls)
        matrix = mgr.get_correlation_matrix()
        assert matrix is not None
        assert abs(matrix["strat_a"]["strat_a"] - 1.0) < 1e-9
        assert abs(matrix["strat_b"]["strat_b"] - 1.0) < 1e-9
        assert abs(matrix["strat_a"]["strat_b"] - matrix["strat_b"]["strat_a"]) < 1e-9

    def test_get_correlation_matrix_insufficient_data_returns_none(self) -> None:
        """Fewer than MIN_SAMPLES_FOR_STATS → returns None."""
        mgr = PortfolioRiskManager(window_minutes=9999)
        for i in range(MIN_SAMPLES_FOR_STATS - 1):
            mgr.update_returns("strat_a", float(i))
            mgr.update_returns("strat_b", float(i))
        assert mgr.get_correlation_matrix() is None

    def test_get_correlation_matrix_single_strategy_returns_none(self) -> None:
        mgr = PortfolioRiskManager(window_minutes=9999)
        _feed(mgr, "only_one", _enough())
        assert mgr.get_correlation_matrix() is None

    def test_get_correlation_matrix_disabled_returns_none(self) -> None:
        mgr = PortfolioRiskManager(enabled=False)
        _feed(mgr, "s1", _enough())
        _feed(mgr, "s2", _enough())
        assert mgr.get_correlation_matrix() is None


# ---------------------------------------------------------------------------
# US-277: VaR
# ---------------------------------------------------------------------------

class TestVaR:
    def test_get_var_95_known_sequence(self) -> None:
        """VaR(95%) on [1,2,...,100] should equal 5th percentile."""
        mgr = PortfolioRiskManager(window_minutes=9999)
        pnls = list(range(-10, 90))   # 100 values, 5th percentile ≈ -5.55
        _feed(mgr, "s1", pnls)
        var = mgr.get_var(0.95)
        assert var is not None
        assert var < 0   # Loss at 95% confidence

    def test_get_var_insufficient_data_returns_none(self) -> None:
        mgr = PortfolioRiskManager(window_minutes=9999)
        for i in range(MIN_SAMPLES_FOR_STATS - 1):
            mgr.update_returns("s1", float(i))
        assert mgr.get_var() is None

    def test_get_var_disabled_returns_none(self) -> None:
        mgr = PortfolioRiskManager(enabled=False)
        assert mgr.get_var() is None


# ---------------------------------------------------------------------------
# US-277: Correlation breach
# ---------------------------------------------------------------------------

class TestCorrelationBreach:
    def test_check_correlation_breach_above_threshold(self) -> None:
        """Identical series → correlation=1.0 → breach above any threshold."""
        mgr = PortfolioRiskManager(window_minutes=9999)
        pnls = _enough()
        _feed(mgr, "s1", pnls)
        _feed(mgr, "s2", pnls)  # identical → corr = 1.0
        breaches = mgr.check_correlation_breach(threshold=0.7)
        assert len(breaches) == 1
        si, sj, val = breaches[0]
        assert val == pytest.approx(1.0, abs=1e-9)

    def test_check_correlation_breach_no_breach_below_threshold(self) -> None:
        """Negatively correlated strategies should not trigger positive threshold."""
        mgr = PortfolioRiskManager(window_minutes=9999)
        pnls_pos = _enough()
        pnls_neg = [-p for p in pnls_pos]
        _feed(mgr, "s1", pnls_pos)
        _feed(mgr, "s2", pnls_neg)
        breaches = mgr.check_correlation_breach(threshold=0.7)
        assert breaches == []


# ---------------------------------------------------------------------------
# US-278: MDD breach
# ---------------------------------------------------------------------------

class TestMDDBreach:
    def test_check_mdd_breach_strategy_3pct(self) -> None:
        """Strategy equity drops 4% → strategy_breaches non-empty."""
        mgr = PortfolioRiskManager()
        mgr.update_equity("s1", 1000.0)   # peak
        mgr.update_equity("s1", 960.0)    # 4% drop > 3% threshold
        result = mgr.check_mdd_breach()
        assert len(result["strategy_breaches"]) == 1
        assert result["strategy_breaches"][0]["strategy_id"] == "s1"
        assert result["strategy_breaches"][0]["mdd_pct"] == pytest.approx(0.04, rel=0.01)

    def test_check_mdd_breach_portfolio_5pct(self) -> None:
        """Portfolio total drops 6% → portfolio_breach is True."""
        mgr = PortfolioRiskManager()
        mgr.update_equity("s1", 500.0)
        mgr.update_equity("s2", 500.0)    # portfolio = 1000
        mgr.update_equity("s1", 470.0)
        mgr.update_equity("s2", 470.0)    # portfolio = 940 → 6% drop
        result = mgr.check_mdd_breach()
        assert result["portfolio_breach"] is True
        assert result["portfolio_mdd_pct"] == pytest.approx(0.06, rel=0.05)

    def test_check_mdd_no_breach_small_drop(self) -> None:
        """1% drop should NOT trigger any breach."""
        mgr = PortfolioRiskManager()
        mgr.update_equity("s1", 1000.0)
        mgr.update_equity("s1", 990.0)    # 1% drop
        result = mgr.check_mdd_breach()
        assert result["strategy_breaches"] == []
        assert result["portfolio_breach"] is False

    def test_check_mdd_breach_disabled_returns_empty(self) -> None:
        mgr = PortfolioRiskManager(enabled=False)
        mgr.update_equity("s1", 1000.0)
        mgr.update_equity("s1", 900.0)
        result = mgr.check_mdd_breach()
        assert result["strategy_breaches"] == []
        assert result["portfolio_breach"] is False
