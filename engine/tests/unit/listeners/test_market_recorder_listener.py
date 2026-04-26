"""Phase 5.2.4.2 MarketRecorderListener 검증."""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.core.models import OrderSide
from src.listeners.market_recorder_listener import MarketRecorderListener
from src.ports.listener_port import ExecutionResultListener


def _make_request(legs: list) -> SimpleNamespace:
    return SimpleNamespace(strategy_id="test_strategy", legs=legs)


def _make_leg(side, exchange_id: str, symbol: str, price: Decimal, size: Decimal) -> SimpleNamespace:
    return SimpleNamespace(side=side, exchange_id=exchange_id, symbol=symbol,
                           price=price, size=size)


def _make_result(status_value: str, pnl: Decimal | None = None,
                 fill_legs: list | None = None) -> SimpleNamespace:
    status = SimpleNamespace(value=status_value)
    return SimpleNamespace(status=status, pnl=pnl, legs=fill_legs or [])


class TestMarketRecorderListener:
    def test_implements_listener_port(self) -> None:
        l = MarketRecorderListener(market_recorder=MagicMock())
        assert isinstance(l, ExecutionResultListener)
        assert l.name == "market_recorder"

    def test_skips_when_recorder_none(self) -> None:
        l = MarketRecorderListener(market_recorder=None)
        l.on_execution_result(_make_request([]), _make_result("success"))  # must not raise

    def test_skips_when_status_not_success(self) -> None:
        recorder = MagicMock()
        l = MarketRecorderListener(market_recorder=recorder)
        l.on_execution_result(_make_request([]), _make_result("failure"))
        recorder.record_execution.assert_not_called()

    def test_records_on_success_with_buy_sell_pair(self) -> None:
        recorder = MagicMock()
        legs = [
            _make_leg(OrderSide.BUY, "binance", "BTC/USDT", Decimal("50000"), Decimal("0.1")),
            _make_leg(OrderSide.SELL, "okx", "BTC/USDT", Decimal("50100"), Decimal("0.1")),
        ]
        l = MarketRecorderListener(market_recorder=recorder)
        l.on_execution_result(_make_request(legs), _make_result("success", pnl=Decimal("10")))
        recorder.record_execution.assert_called_once()
        kwargs = recorder.record_execution.call_args.kwargs
        assert kwargs["strategy_id"] == "test_strategy"
        assert kwargs["buy_exchange"] == "binance"
        assert kwargs["sell_exchange"] == "okx"
        assert kwargs["symbol"] == "BTC/USDT"
        assert kwargs["status"] == "filled"

    def test_skips_when_only_buy_legs(self) -> None:
        recorder = MagicMock()
        legs = [_make_leg(OrderSide.BUY, "binance", "BTC/USDT", Decimal("50000"), Decimal("0.1"))]
        l = MarketRecorderListener(market_recorder=recorder)
        l.on_execution_result(_make_request(legs), _make_result("success"))
        recorder.record_execution.assert_not_called()
