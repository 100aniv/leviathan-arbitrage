"""Phase 5.2.4.4 SlippageListener + CorrelationListener + TCAListener 검증."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.listeners.correlation_listener import CorrelationListener
from src.listeners.slippage_listener import SlippageListener
from src.listeners.tca_listener import TCAListener
from src.ports.listener_port import ExecutionResultListener


class TestSlippageListener:
    def test_protocol(self) -> None:
        assert isinstance(SlippageListener(MagicMock()), ExecutionResultListener)

    def test_skips_when_none(self) -> None:
        SlippageListener(None).on_execution_result(SimpleNamespace(), SimpleNamespace())

    def test_records_when_leg_has_prices(self) -> None:
        fb = MagicMock()
        order = SimpleNamespace(side=SimpleNamespace(value="buy"))
        leg = SimpleNamespace(
            expected_price=Decimal("100"),
            fill_price=Decimal("100.5"),
            order=order,
        )
        result = SimpleNamespace(legs=[leg])
        SlippageListener(fb).on_execution_result(SimpleNamespace(), result)
        fb.record_fill.assert_called_once()


class TestCorrelationListener:
    def test_protocol(self) -> None:
        assert isinstance(CorrelationListener(MagicMock()), ExecutionResultListener)

    def test_skips_when_none(self) -> None:
        CorrelationListener(None).on_execution_result(SimpleNamespace(), SimpleNamespace())

    def test_records_pnl(self) -> None:
        mon = MagicMock()
        request = SimpleNamespace(strategy_id="x", expected_profit_usdt=Decimal("5"))
        result = SimpleNamespace(pnl=Decimal("4.5"))
        CorrelationListener(mon).on_execution_result(request, result)
        mon.record_trade_pnl.assert_called_once_with("x", 4.5)


class TestTCAListener:
    def test_protocol(self) -> None:
        assert isinstance(TCAListener(MagicMock()), ExecutionResultListener)

    def test_skips_when_none(self) -> None:
        TCAListener(None).on_execution_result(SimpleNamespace(), SimpleNamespace())

    def test_records_each_leg(self) -> None:
        tca = MagicMock()
        leg_in_req = SimpleNamespace(price=Decimal("100"))
        request = SimpleNamespace(
            strategy_id="x",
            legs=[leg_in_req],
            timestamp=datetime.now(timezone.utc),
        )
        trade = SimpleNamespace(price=Decimal("100.5"))
        leg_in_result = SimpleNamespace(trade=trade, filled_ratio=1.0,
                                         order=SimpleNamespace(price=Decimal("100")))
        result = SimpleNamespace(legs=[leg_in_result], execution_duration_ms=10.0)
        TCAListener(tca).on_execution_result(request, result)
        tca.record_execution.assert_called_once()
