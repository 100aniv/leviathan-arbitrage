"""Tests for AtomicOrderExecutor — US-275 (partial fill env vars) + US-275-a (depth sizing)."""
from __future__ import annotations

import os
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.core.depth_analyzer import DepthAnalyzer
from src.core.order_book import OrderBook
from src.execution.atomic import AtomicOrderExecutor, OrderResult


def _make_book() -> OrderBook:
    book = MagicMock(spec=OrderBook)
    return book


def _make_depth_analyzer(available: Decimal) -> DepthAnalyzer:
    da = MagicMock(spec=DepthAnalyzer)
    da.liquidity_at_pct_depth.return_value = available
    return da


@pytest.mark.asyncio
async def test_depth_sizing_scales_down():
    """available < requested size → size is scaled down to available * 0.8."""
    available = Decimal("0.5")
    original_size = Decimal("1.0")
    da = _make_depth_analyzer(available)
    executor = AtomicOrderExecutor(depth_analyzer=da)
    executor.enable_depth_sizing = True

    # Mock exchange that captures the actual size passed to place_ioc_limit
    placed_sizes: list[Decimal] = []

    class FakeExchange:
        async def place_ioc_limit(self, symbol, side, price, size):
            placed_sizes.append(size)
            return OrderResult(filled_size=size, avg_price=price, order_type="ioc_limit", latency_ms=1.0)

        async def place_market(self, symbol, side, size):
            return OrderResult(filled_size=size, avg_price=Decimal("50000"), order_type="market", latency_ms=1.0)

    book = _make_book()
    await executor.execute(FakeExchange(), "BTC/USDT", "buy", Decimal("50000"), original_size, book=book)

    assert placed_sizes, "place_ioc_limit was never called"
    expected_max = available * Decimal("0.8")
    assert placed_sizes[0] <= expected_max + Decimal("0.001")


@pytest.mark.asyncio
async def test_depth_sizing_no_book():
    """book=None → depth sizing skipped, original size is used unchanged."""
    da = _make_depth_analyzer(Decimal("0.1"))  # small available, but should NOT scale
    executor = AtomicOrderExecutor(depth_analyzer=da)
    executor.enable_depth_sizing = True

    original_size = Decimal("1.0")
    placed_sizes: list[Decimal] = []

    class FakeExchange:
        async def place_ioc_limit(self, symbol, side, price, size):
            placed_sizes.append(size)
            return OrderResult(filled_size=size, avg_price=price, order_type="ioc_limit", latency_ms=1.0)

        async def place_market(self, symbol, side, size):
            return OrderResult(filled_size=size, avg_price=Decimal("50000"), order_type="market", latency_ms=1.0)

    # book=None → no depth sizing
    await executor.execute(FakeExchange(), "BTC/USDT", "buy", Decimal("50000"), original_size, book=None)

    assert placed_sizes, "place_ioc_limit was never called"
    assert placed_sizes[0] == original_size


def test_partial_fill_env_vars():
    """ENABLE_PARTIAL_FILL_STOP and MAX_LOSS_PCT are read from environment variables."""
    env_patch = {
        "ENABLE_PARTIAL_FILL_STOP": "false",
        "MAX_LOSS_PCT": "5.0",
        "PARTIAL_FILL_TIMEOUT_S": "60",
    }
    original = {k: os.environ.get(k) for k in env_patch}
    try:
        os.environ.update(env_patch)
        executor = AtomicOrderExecutor()
        assert executor.enable_partial_fill_stop is False
        assert executor.max_loss_pct == pytest.approx(5.0)
        assert executor.partial_fill_timeout_s == pytest.approx(60.0)
    finally:
        for k, v in original.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
