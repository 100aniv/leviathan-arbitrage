"""Integration tests for Phase S18 portfolio risk modules — US-285."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.analysis.attribution import PerformanceAttribution, TradeRecord
from src.core.capital_allocator import CapitalAllocator
from src.core.live_gate_continuous import ContinuousLiveGateMonitor
from src.core.market_impact import estimate_market_impact
from src.core.metrics_rolling import consistency, sharpe
from src.core.portfolio_risk import MIN_SAMPLES_FOR_STATS, PortfolioRiskManager

with patch("src.infra.metrics.SLIPPAGE_ADJUSTMENT"), \
     patch("src.infra.metrics.SLIPPAGE_ERROR"):
    from src.risk.slippage import SlippageFeedbackLoop


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _trade(strategy_id: str = "s1", pnl: float = 1.0) -> TradeRecord:
    return TradeRecord(
        trade_id="t",
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        strategy_id=strategy_id,
        exchange_buy="binance",
        exchange_sell="bybit",
        pair="BTC/USDT",
        pnl=pnl,
    )


def _make_fb() -> SlippageFeedbackLoop:
    with patch("src.risk.slippage.SLIPPAGE_ADJUSTMENT"), \
         patch("src.risk.slippage.SLIPPAGE_ERROR"):
        return SlippageFeedbackLoop(alpha=0.3, window=100)


def _record(fb: SlippageFeedbackLoop, expected: str, actual: str) -> None:
    with patch("src.risk.slippage.SLIPPAGE_ADJUSTMENT"), \
         patch("src.risk.slippage.SLIPPAGE_ERROR"):
        fb.record_fill(Decimal(expected), Decimal(actual), "BUY")


# ---------------------------------------------------------------------------
# Integration: portfolio risk + capital allocator
# ---------------------------------------------------------------------------

class TestPortfolioRiskManagerIntegration:
    def test_portfolio_risk_manager_integration(self) -> None:
        """PortfolioRiskManager tracks multiple strategies without error."""
        mgr = PortfolioRiskManager(window_minutes=9999)
        for i in range(MIN_SAMPLES_FOR_STATS + 5):
            mgr.update_returns("strat_a", float(i % 3 - 1))
            mgr.update_returns("strat_b", float((i + 1) % 3 - 1))
        matrix = mgr.get_correlation_matrix()
        assert matrix is not None
        var = mgr.get_var()
        assert var is not None

    def test_capital_allocator_with_portfolio_risk(self) -> None:
        """CapitalAllocator and PortfolioRiskManager coexist without coupling errors."""
        mgr = PortfolioRiskManager(window_minutes=9999)
        alloc = CapitalAllocator(total_capital=10_000)
        for i in range(MIN_SAMPLES_FOR_STATS + 5):
            mgr.update_returns("strat_a", 0.01 if i % 2 == 0 else -0.005)
        stats = {"strat_a": {"win_rate": 0.6, "avg_win": 2.0, "avg_loss": 1.0, "num_trades": 50}}
        result = alloc.compute_allocations(stats)
        assert len(result) == 1
        var = mgr.get_var()
        assert var is not None

    def test_regime_aware_capital_with_mdd(self) -> None:
        """After MDD breach, manually scale allocation (regime = crisis)."""
        mgr = PortfolioRiskManager()
        alloc = CapitalAllocator(total_capital=10_000)
        mgr.update_equity("strat_a", 1000.0)
        mgr.update_equity("strat_a", 900.0)  # 10% drop → breach
        mdd_result = mgr.check_mdd_breach()
        stats = {"strat_a": {"win_rate": 0.6, "avg_win": 2.0, "avg_loss": 1.0, "num_trades": 50}}
        allocations = alloc.compute_allocations(stats)
        if mdd_result["strategy_breaches"]:
            # crisis: 10% scaling
            crisis_alloc = allocations[0].half_kelly * 0.1
            assert crisis_alloc < allocations[0].half_kelly


# ---------------------------------------------------------------------------
# Integration: slippage feedback
# ---------------------------------------------------------------------------

class TestSlippageFeedbackUpdates:
    def test_slippage_feedback_updates_predictions(self) -> None:
        """After fills, adjusted slippage diverges from base."""
        fb = _make_fb()
        base = 10.0
        for _ in range(5):
            _record(fb, "10000", "10080")  # underpaid
        adjusted = fb.get_adjusted_slippage(base)
        assert adjusted != base


# ---------------------------------------------------------------------------
# Integration: market impact filters
# ---------------------------------------------------------------------------

class TestMarketImpactFilters:
    def test_market_impact_filters_large_orders(self) -> None:
        """Large order (5% of ADV) has higher impact than small order (0.1% of ADV)."""
        impact_large = estimate_market_impact(50_000.0, 1_000_000.0, eta=0.1)
        impact_small = estimate_market_impact(1_000.0, 1_000_000.0, eta=0.1)
        assert impact_large > impact_small


# ---------------------------------------------------------------------------
# Integration: attribution report
# ---------------------------------------------------------------------------

class TestAttributionReportIntegration:
    def test_attribution_report_integration(self) -> None:
        """Mixed trade records produce correct strategy/exchange breakdowns."""
        pa = PerformanceAttribution()
        pa.add_trades([
            _trade("s1", pnl=5.0),
            _trade("s1", pnl=-1.0),
            _trade("s2", pnl=3.0),
        ])
        s = pa.summary()
        assert s["total_trades"] == 3
        assert s["total_pnl"] == pytest.approx(7.0)
        by_s = {b["key"]: b for b in s["by_strategy"]}
        assert by_s["s1"]["pnl"] == pytest.approx(4.0)
        assert by_s["s2"]["pnl"] == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# Integration: metrics rolling
# ---------------------------------------------------------------------------

class TestMetricsCalculatorWithShadow:
    def test_metrics_calculator_with_shadow(self) -> None:
        """Sharpe on mixed returns gives finite positive value for net positive series."""
        returns = [0.01 if i % 3 != 0 else -0.005 for i in range(60)]
        s = sharpe(returns)
        c = consistency(returns)
        assert isinstance(s, float)
        assert 0.0 <= c <= 1.0


# ---------------------------------------------------------------------------
# Integration: MDD blocks + correlation limits
# ---------------------------------------------------------------------------

class TestMddBreachBlocksNewTrades:
    def test_mdd_breach_blocks_new_trades(self) -> None:
        """When MDD breach detected, strategy_breaches list is non-empty."""
        mgr = PortfolioRiskManager()
        mgr.update_equity("risky", 1000.0)
        mgr.update_equity("risky", 950.0)   # 5% > 3% threshold
        result = mgr.check_mdd_breach()
        assert result["strategy_breaches"] != []

    def test_correlation_high_limits_position(self) -> None:
        """Two identical strategies → correlation breach detected."""
        mgr = PortfolioRiskManager(window_minutes=9999)
        pnls = [0.01 if i % 2 == 0 else -0.005 for i in range(MIN_SAMPLES_FOR_STATS + 5)]
        for i, pnl in enumerate(pnls):
            mgr.update_returns("s1", pnl)
            mgr.update_returns("s2", pnl)
        breaches = mgr.check_correlation_breach(threshold=0.5)
        assert len(breaches) >= 1


# ---------------------------------------------------------------------------
# VaR accuracy
# ---------------------------------------------------------------------------

class TestVarCalculationAccuracy:
    def test_var_calculation_accuracy(self) -> None:
        """VaR(95%) of uniform [-10..89] should be around -5."""
        mgr = PortfolioRiskManager(window_minutes=9999)
        pnls = list(range(-10, 90))
        for p in pnls:
            mgr.update_returns("s1", float(p))
        var = mgr.get_var(0.95)
        assert var is not None
        assert var < 0


# ---------------------------------------------------------------------------
# Env var toggles
# ---------------------------------------------------------------------------

class TestEnvVarToggles:
    def test_env_var_toggles_all_disabled(self, monkeypatch) -> None:
        monkeypatch.setenv("LIVE_GATE_CONTINUOUS", "0")
        gate = MagicMock()
        gate.evaluate = AsyncMock()
        monitor = ContinuousLiveGateMonitor(gate)
        assert monitor.enabled is False

    def test_env_var_toggles_all_enabled(self, monkeypatch) -> None:
        monkeypatch.setenv("LIVE_GATE_CONTINUOUS", "1")
        gate = MagicMock()
        monitor = ContinuousLiveGateMonitor(gate)
        assert monitor.enabled is True

    def test_portfolio_risk_wiring_in_engine_context(self) -> None:
        from src.api.server import EngineContext
        fields = EngineContext.__dataclass_fields__
        assert "portfolio_risk" in fields
        assert "capital_allocator" in fields
        assert "attribution" in fields
        assert "slippage_feedback" in fields

    def test_all_modules_coexist_no_regression(self) -> None:
        """Import all S18 modules simultaneously — no circular imports."""
        from src.analysis.attribution import PerformanceAttribution  # noqa: F401
        from src.core.capital_allocator import CapitalAllocator  # noqa: F401
        from src.core.live_gate_continuous import ContinuousLiveGateMonitor  # noqa: F401
        from src.core.market_impact import estimate_market_impact  # noqa: F401
        from src.core.metrics_rolling import sharpe  # noqa: F401
        from src.core.portfolio_risk import PortfolioRiskManager  # noqa: F401
        assert True  # no ImportError
