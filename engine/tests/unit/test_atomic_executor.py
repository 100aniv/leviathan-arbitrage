"""Tests for AtomicOrderExecutor (US-119) — IOC limit with market fallback."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from src.execution.atomic import AtomicOrderExecutor, FillQuality, OrderResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_exchange(ioc_result: OrderResult | None = None, market_result: OrderResult | None = None):
    """Create a mock exchange conforming to ExchangeOrderAPI."""
    exchange = AsyncMock()
    if ioc_result is not None:
        exchange.place_ioc_limit = AsyncMock(return_value=ioc_result)
    if market_result is not None:
        exchange.place_market = AsyncMock(return_value=market_result)
    return exchange


def _ioc_result(filled: str, price: str = "50000") -> OrderResult:
    return OrderResult(filled_size=Decimal(filled), avg_price=Decimal(price),
                       order_type="ioc_limit", latency_ms=5.0)


def _market_result(filled: str, price: str = "50010") -> OrderResult:
    return OrderResult(filled_size=Decimal(filled), avg_price=Decimal(price),
                       order_type="market", latency_ms=10.0)


# ---------------------------------------------------------------------------
# IOC full fill
# ---------------------------------------------------------------------------

class TestIOCFullFill:
    @pytest.mark.asyncio
    async def test_95pct_fill_no_market_fallback(self) -> None:
        exchange = _mock_exchange(ioc_result=_ioc_result("0.98", "50000"))
        with patch("src.execution.atomic.IOC_FILL_RATE"), \
             patch("src.execution.atomic.IOC_VS_MARKET"):
            executor = AtomicOrderExecutor(timeout_ms=500)
            result = await executor.execute(exchange, "BTC/USDT", "BUY",
                                            Decimal("50000"), Decimal("1.0"))
        assert result.order_type == "ioc_limit"
        exchange.place_market.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_full_fill_returns_filled_size(self) -> None:
        exchange = _mock_exchange(ioc_result=_ioc_result("1.0", "50000"))
        with patch("src.execution.atomic.IOC_FILL_RATE"), \
             patch("src.execution.atomic.IOC_VS_MARKET"):
            executor = AtomicOrderExecutor(timeout_ms=500)
            result = await executor.execute(exchange, "BTC/USDT", "BUY",
                                            Decimal("50000"), Decimal("1.0"))
        assert result.filled_size == Decimal("1.0")


# ---------------------------------------------------------------------------
# IOC partial fill → market fallback
# ---------------------------------------------------------------------------

class TestIOCPartialFill:
    @pytest.mark.asyncio
    async def test_50pct_triggers_market(self) -> None:
        exchange = _mock_exchange(
            ioc_result=_ioc_result("0.5", "50000"),
            market_result=_market_result("0.5", "50010"),
        )
        with patch("src.execution.atomic.IOC_FILL_RATE"), \
             patch("src.execution.atomic.IOC_VS_MARKET"):
            executor = AtomicOrderExecutor(timeout_ms=500)
            result = await executor.execute(exchange, "BTC/USDT", "BUY",
                                            Decimal("50000"), Decimal("1.0"))
        assert result.order_type == "market_fallback"
        exchange.place_market.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_total_filled_equals_requested(self) -> None:
        exchange = _mock_exchange(
            ioc_result=_ioc_result("0.6", "50000"),
            market_result=_market_result("0.4", "50010"),
        )
        with patch("src.execution.atomic.IOC_FILL_RATE"), \
             patch("src.execution.atomic.IOC_VS_MARKET"):
            executor = AtomicOrderExecutor(timeout_ms=500)
            result = await executor.execute(exchange, "BTC/USDT", "BUY",
                                            Decimal("50000"), Decimal("1.0"))
        assert result.filled_size == Decimal("1.0")


# ---------------------------------------------------------------------------
# IOC timeout → full market fallback
# ---------------------------------------------------------------------------

class TestIOCTimeout:
    @pytest.mark.asyncio
    async def test_timeout_full_market(self) -> None:
        exchange = AsyncMock()
        exchange.place_ioc_limit = AsyncMock(side_effect=Exception("timeout"))
        exchange.place_market = AsyncMock(return_value=_market_result("1.0", "50015"))
        with patch("src.execution.atomic.IOC_FILL_RATE"), \
             patch("src.execution.atomic.IOC_VS_MARKET"):
            executor = AtomicOrderExecutor(timeout_ms=500)
            result = await executor.execute(exchange, "BTC/USDT", "BUY",
                                            Decimal("50000"), Decimal("1.0"))
        assert result.order_type == "market_fallback"
        assert result.filled_size == Decimal("1.0")


# ---------------------------------------------------------------------------
# Fill quality metrics
# ---------------------------------------------------------------------------

class TestFillQualityMetrics:
    @pytest.mark.asyncio
    async def test_ioc_rate_3_ioc_1_market(self) -> None:
        with patch("src.execution.atomic.IOC_FILL_RATE"), \
             patch("src.execution.atomic.IOC_VS_MARKET"):
            executor = AtomicOrderExecutor(timeout_ms=500)
            # 3 full IOC fills
            for _ in range(3):
                exchange = _mock_exchange(ioc_result=_ioc_result("1.0", "50000"))
                await executor.execute(exchange, "BTC/USDT", "BUY",
                                       Decimal("50000"), Decimal("1.0"))
            # 1 market fallback
            exchange = AsyncMock()
            exchange.place_ioc_limit = AsyncMock(side_effect=Exception("fail"))
            exchange.place_market = AsyncMock(return_value=_market_result("1.0", "50010"))
            await executor.execute(exchange, "BTC/USDT", "BUY",
                                   Decimal("50000"), Decimal("1.0"))
        fq = executor.fill_quality
        assert fq.fill_rate == pytest.approx(0.75, abs=0.01)

    @pytest.mark.asyncio
    async def test_all_ioc_rate_one(self) -> None:
        with patch("src.execution.atomic.IOC_FILL_RATE"), \
             patch("src.execution.atomic.IOC_VS_MARKET"):
            executor = AtomicOrderExecutor(timeout_ms=500)
            for _ in range(5):
                exchange = _mock_exchange(ioc_result=_ioc_result("1.0", "50000"))
                await executor.execute(exchange, "BTC/USDT", "BUY",
                                       Decimal("50000"), Decimal("1.0"))
        assert executor.fill_quality.fill_rate == pytest.approx(1.0, abs=0.01)

    def test_fill_quality_dataclass_fields(self) -> None:
        fq = FillQuality(ioc_slippage_bps=1.5, market_slippage_bps=5.0, fill_rate=0.8)
        assert fq.ioc_slippage_bps == 1.5
        assert fq.market_slippage_bps == 5.0
        assert fq.fill_rate == 0.8
