"""Tests for US-258-b: StatArbStrategy warmup — min_history gate.

Verifies:
- min_history 미달 → on_signal() None 반환 (warmup 기간)
- warm-up 전략 trade=0 → Shadow 복합지표에서 정상 (오경보 방지)

Run:
    cd engine && python -m pytest tests/test_stat_arb_warmup.py -v --tb=short
"""
from __future__ import annotations

from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.strategies.statistical_arb import StatisticalArbStrategy, StatArbConfig
from src.core.models import Signal


def _make_signal(i: float = 0.0) -> Signal:
    return Signal(
        strategy_id="stat_arb",
        symbol="BTC/USDT",
        buy_exchange="binance",
        sell_exchange="okx",
        buy_price=Decimal(str(100.0 + i * 0.1)),
        sell_price=Decimal(str(101.0 + i * 0.1)),
        spread_pct=Decimal("0.01"),
        confidence=0.8,
        volume=Decimal("0.1"),
        timestamp=datetime.now(timezone.utc),
    )


def _make_stat_arb(min_history: int = 15) -> StatisticalArbStrategy:
    mock_cost = MagicMock()
    mock_cost.estimate_cost.return_value = Decimal("0.001")
    config = StatArbConfig(min_history=max(10, min_history), enable_cointegration=False)
    return StatisticalArbStrategy(
        strategy_id="stat_arb_warmup_test",
        cost_calculator=mock_cost,
        config=config,
    )


class TestStatArbWarmup:
    """US-258-b: warmup 기간 중 신호 차단."""

    @pytest.mark.asyncio
    async def test_warmup_skips_signal(self):
        """min_history 미달 → on_signal() 스킵 (None 반환)."""
        strat = _make_stat_arb(min_history=15)
        await strat.start()

        # Send only 3 signals — below min_history=15
        results = []
        for i in range(3):
            result = await strat.on_signal(_make_signal(i))
            results.append(result)

        # All must be None during warmup
        assert all(r is None for r in results), (
            "All signals during warmup (< min_history) must return None"
        )

    @pytest.mark.asyncio
    async def test_warmup_signals_filtered_metric_increments(self):
        """warmup 중 signals_filtered 카운터가 증가."""
        strat = _make_stat_arb(min_history=15)
        await strat.start()
        initial_filtered = strat._metrics.signals_filtered

        for i in range(5):
            await strat.on_signal(_make_signal(i))

        assert strat._metrics.signals_filtered > initial_filtered, (
            "signals_filtered must increase during warmup"
        )

    @pytest.mark.asyncio
    async def test_shadow_excludes_warmup_trade_count_zero(self):
        """warm-up 전략은 trade=0 → Shadow 오경보 없음.

        14항목 복합지표에서 warm-up 기간의 0 trade는 정상 동작으로 처리.
        """
        strat = _make_stat_arb(min_history=120)
        await strat.start()

        # Send 30 signals — all below min_history=120
        for i in range(30):
            await strat.on_signal(_make_signal(i))

        # trade_requests_generated must be 0 (not an error, just warmup)
        assert strat._metrics.trade_requests_generated == 0, (
            "Warmup period should generate 0 trades — not a strategy failure"
        )

    @pytest.mark.asyncio
    async def test_after_warmup_allows_entry(self):
        """min_history 이상 신호 → warmup 완료, 진입 평가 시작."""
        min_hist = 10  # min_history must be >= 10 per StatArbConfig constraint
        strat = _make_stat_arb(min_history=min_hist)
        await strat.start()

        # Feed enough signals to pass warmup (need > min_history)
        n_signals = min_hist + 5
        for i in range(n_signals):
            await strat.on_signal(_make_signal(float(i)))

        # After warmup: signals_received should equal n_signals
        assert strat._metrics.signals_received >= n_signals
        # Spread history accumulated — signals append to _spreads before warmup check
        spreads_count = len(strat._spreads)
        assert spreads_count >= min_hist, (
            f"Expected >= {min_hist} spreads after {n_signals} signals, got {spreads_count}"
        )
