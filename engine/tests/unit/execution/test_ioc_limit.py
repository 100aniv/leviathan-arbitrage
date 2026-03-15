"""US-178: IOC Limit Order — AtomicOrderExecutor and exchange adapter contracts."""
from __future__ import annotations

from decimal import Decimal

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.execution.atomic import AtomicOrderExecutor, ExchangeOrderAPI, OrderResult


# ---------------------------------------------------------------------------
# ExchangeOrderAPI protocol compliance
# ---------------------------------------------------------------------------


class TestExchangeOrderAPIProtocol:
    def test_place_ioc_limit_exists_on_protocol(self):
        """ExchangeOrderAPI protocol defines place_ioc_limit method."""
        import inspect
        members = {name for name, _ in inspect.getmembers(ExchangeOrderAPI)}
        assert "place_ioc_limit" in members or hasattr(ExchangeOrderAPI, "place_ioc_limit")

    def test_order_result_has_filled_size(self):
        """OrderResult dataclass has filled_size field."""
        result = OrderResult(
            filled_size=Decimal("0.5"),
            avg_price=Decimal("50000"),
            order_type="ioc_limit",
            latency_ms=12.5,
        )
        assert result.filled_size == Decimal("0.5")

    def test_order_result_order_type_ioc_limit(self):
        """OrderResult stores 'ioc_limit' as order_type for IOC orders."""
        result = OrderResult(
            filled_size=Decimal("1.0"),
            avg_price=Decimal("60000"),
            order_type="ioc_limit",
            latency_ms=8.0,
        )
        assert result.order_type == "ioc_limit"


# ---------------------------------------------------------------------------
# AtomicOrderExecutor — IOC happy path
# ---------------------------------------------------------------------------


class TestAtomicOrderExecutorIOC:
    @pytest.mark.asyncio
    async def test_full_ioc_fill_does_not_call_market_fallback(self):
        """When IOC fills >= 95%, no market fallback is triggered."""
        mock_exchange = MagicMock()
        mock_exchange.place_ioc_limit = AsyncMock(return_value=OrderResult(
            filled_size=Decimal("1.0"),
            avg_price=Decimal("50000"),
            order_type="ioc_limit",
            latency_ms=5.0,
        ))
        mock_exchange.place_market = AsyncMock()

        executor = AtomicOrderExecutor()
        await executor.execute(
            exchange=mock_exchange,
            symbol="BTC/USDT",
            side="buy",
            price=Decimal("50000"),
            size=Decimal("1.0"),
        )

        mock_exchange.place_ioc_limit.assert_awaited_once()
        mock_exchange.place_market.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_partial_ioc_fill_triggers_market_fallback(self):
        """When IOC fills < 95%, market order is called for remainder."""
        mock_exchange = MagicMock()
        mock_exchange.place_ioc_limit = AsyncMock(return_value=OrderResult(
            filled_size=Decimal("0.5"),   # only 50% filled
            avg_price=Decimal("50000"),
            order_type="ioc_limit",
            latency_ms=5.0,
        ))
        mock_exchange.place_market = AsyncMock(return_value=OrderResult(
            filled_size=Decimal("0.5"),
            avg_price=Decimal("50050"),
            order_type="market_fallback",
            latency_ms=3.0,
        ))

        executor = AtomicOrderExecutor()
        await executor.execute(
            exchange=mock_exchange,
            symbol="BTC/USDT",
            side="buy",
            price=Decimal("50000"),
            size=Decimal("1.0"),
        )

        mock_exchange.place_market.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ioc_timeout_falls_back_to_market(self):
        """When IOC times out (asyncio.TimeoutError), market order is executed."""
        import asyncio
        mock_exchange = MagicMock()
        mock_exchange.place_ioc_limit = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_exchange.place_market = AsyncMock(return_value=OrderResult(
            filled_size=Decimal("1.0"),
            avg_price=Decimal("50050"),
            order_type="market_fallback",
            latency_ms=4.0,
        ))

        executor = AtomicOrderExecutor()
        await executor.execute(
            exchange=mock_exchange,
            symbol="BTC/USDT",
            side="buy",
            price=Decimal("50000"),
            size=Decimal("1.0"),
        )

        mock_exchange.place_market.assert_awaited_once()


# ---------------------------------------------------------------------------
# IOC fill rate tracking
# ---------------------------------------------------------------------------


class TestIOCFillRateTracking:
    @pytest.mark.asyncio
    async def test_ioc_fill_increments_counter(self):
        """Successful IOC fill increments internal _ioc_fills counter."""
        mock_exchange = MagicMock()
        mock_exchange.place_ioc_limit = AsyncMock(return_value=OrderResult(
            filled_size=Decimal("1.0"),
            avg_price=Decimal("50000"),
            order_type="ioc_limit",
            latency_ms=5.0,
        ))
        mock_exchange.place_market = AsyncMock()

        executor = AtomicOrderExecutor()
        before = executor._ioc_fills
        await executor.execute(
            exchange=mock_exchange,
            symbol="BTC/USDT",
            side="buy",
            price=Decimal("50000"),
            size=Decimal("1.0"),
        )
        assert executor._ioc_fills == before + 1


# ---------------------------------------------------------------------------
# Native adapter IOC method existence
# ---------------------------------------------------------------------------


class TestNativeAdapterIOCMethod:
    def _check_adapter_has_ioc_limit(self, module_path: str, class_name: str):
        """Helper to verify place_ioc_limit exists on a native adapter."""
        try:
            import importlib
            mod = importlib.import_module(module_path)
            cls = getattr(mod, class_name)
            assert hasattr(cls, "place_ioc_limit"), (
                f"{class_name} missing place_ioc_limit method"
            )
        except ImportError:
            pytest.skip(f"{module_path} not available")

    def test_binance_adapter_has_place_ioc_limit(self):
        """Binance native adapter exposes place_ioc_limit."""
        self._check_adapter_has_ioc_limit(
            "src.collectors.native_binance", "NativeBinanceAdapter"
        )

    def test_bybit_adapter_has_place_ioc_limit(self):
        """Bybit native adapter exposes place_ioc_limit."""
        self._check_adapter_has_ioc_limit(
            "src.collectors.native_bybit", "NativeBybitAdapter"
        )

    def test_okx_adapter_has_place_ioc_limit(self):
        """OKX native adapter exposes place_ioc_limit."""
        self._check_adapter_has_ioc_limit(
            "src.collectors.native_okx", "NativeOKXAdapter"
        )
