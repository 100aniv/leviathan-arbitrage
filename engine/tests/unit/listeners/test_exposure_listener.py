"""Phase 5.2.4.3 ExposureListener 검증."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.models import OrderSide
from src.listeners.exposure_listener import ExposureListener
from src.ports.listener_port import ExecutionResultListener


class TestExposureListener:
    def test_implements_listener_port(self) -> None:
        l = ExposureListener(exposure_tracker=AsyncMock())
        assert isinstance(l, ExecutionResultListener)
        assert l.name == "exposure"

    def test_skips_when_tracker_none(self) -> None:
        l = ExposureListener(exposure_tracker=None)
        result = SimpleNamespace(status=SimpleNamespace(value="success"), legs=[])
        l.on_execution_result(SimpleNamespace(strategy_id="x"), result)  # must not raise

    def test_skips_when_status_not_success(self) -> None:
        tracker = AsyncMock()
        l = ExposureListener(exposure_tracker=tracker)
        result = SimpleNamespace(status=SimpleNamespace(value="failure"), legs=[])
        l.on_execution_result(SimpleNamespace(strategy_id="x"), result)
        tracker.update_exposure.assert_not_called()

    @pytest.mark.asyncio
    async def test_creates_task_for_each_filled_leg(self) -> None:
        tracker = AsyncMock()
        side_buy = SimpleNamespace(value="BUY")
        side_sell = SimpleNamespace(value="SELL")
        order_a = SimpleNamespace(symbol="BTC/USDT", side=side_buy, exchange_id="binance")
        order_b = SimpleNamespace(symbol="BTC/USDT", side=side_sell, exchange_id="okx")
        trade_a = SimpleNamespace(amount=Decimal("0.1"))
        trade_b = SimpleNamespace(amount=Decimal("0.1"))
        legs = [
            SimpleNamespace(order=order_a, trade=trade_a),
            SimpleNamespace(order=order_b, trade=trade_b),
        ]
        result = SimpleNamespace(status=SimpleNamespace(value="success"), legs=legs)
        l = ExposureListener(exposure_tracker=tracker)
        l.on_execution_result(SimpleNamespace(strategy_id="x"), result)
        await asyncio.sleep(0)  # allow create_task to run
        assert tracker.update_exposure.call_count == 2
