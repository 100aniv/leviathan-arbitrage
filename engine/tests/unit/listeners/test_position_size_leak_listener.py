"""Phase 5.2.4 PositionSizeLeakListener 검증."""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.listeners.position_size_leak_listener import PositionSizeLeakListener
from src.ports.listener_port import ExecutionResultListener


def _make_leg(side_value: str, symbol: str, price: Decimal, amount: Decimal) -> SimpleNamespace:
    return SimpleNamespace(
        order=SimpleNamespace(
            side=SimpleNamespace(value=side_value),
            symbol=symbol,
        ),
        trade=SimpleNamespace(price=price, amount=amount),
    )


class TestPositionSizeLeakListener:
    def test_protocol(self) -> None:
        assert isinstance(PositionSizeLeakListener({}), ExecutionResultListener)

    def test_alert_escalation_on_repeated_errors(self) -> None:
        """Codex final review parity: error_count > 5 → telegram alert."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        bot = SimpleNamespace(send_alert_kr=AsyncMock())
        state = SimpleNamespace(position_tracking_errors=5)
        listener = PositionSizeLeakListener({}, state=state, alert_bot=bot)
        # Trigger exception by passing legs missing trade.price
        result = SimpleNamespace(
            status=SimpleNamespace(value="success"),
            legs=[SimpleNamespace(
                trade=SimpleNamespace(),  # no price → exception in pos_value calc
                order=SimpleNamespace(symbol="X", side=SimpleNamespace(value="buy")),
            )],
        )
        listener.on_execution_result(SimpleNamespace(strategy_id="x"), result)
        # Counter incremented to 6 > threshold 5 → alert called
        assert state.position_tracking_errors == 6
        bot.send_alert_kr.assert_called_once()
        args, _ = bot.send_alert_kr.call_args
        assert args[0] == "position_tracking_fail"
        assert args[1] == {"error_count": 6}

    def test_skips_when_status_not_success(self) -> None:
        sizes = {}
        result = SimpleNamespace(status=SimpleNamespace(value="failure"), legs=[])
        PositionSizeLeakListener(sizes).on_execution_result(SimpleNamespace(), result)
        assert sizes == {}

    def test_buy_increments(self) -> None:
        sizes = {}
        legs = [_make_leg("buy", "BTC/USDT", Decimal("50000"), Decimal("0.1"))]
        result = SimpleNamespace(status=SimpleNamespace(value="success"), legs=legs)
        PositionSizeLeakListener(sizes).on_execution_result(SimpleNamespace(), result)
        assert sizes["BTC/USDT"] == Decimal("5000")

    def test_sell_decrements(self) -> None:
        sizes = {"BTC/USDT": Decimal("5000")}
        legs = [_make_leg("sell", "BTC/USDT", Decimal("50000"), Decimal("0.05"))]
        result = SimpleNamespace(status=SimpleNamespace(value="success"), legs=legs)
        PositionSizeLeakListener(sizes).on_execution_result(SimpleNamespace(), result)
        assert sizes["BTC/USDT"] == Decimal("2500")

    def test_sell_to_zero_pops_key(self) -> None:
        sizes = {"BTC/USDT": Decimal("5000")}
        legs = [_make_leg("sell", "BTC/USDT", Decimal("50000"), Decimal("0.1"))]
        result = SimpleNamespace(status=SimpleNamespace(value="success"), legs=legs)
        PositionSizeLeakListener(sizes).on_execution_result(SimpleNamespace(), result)
        assert "BTC/USDT" not in sizes

    def test_sell_below_zero_clamped(self) -> None:
        sizes = {"BTC/USDT": Decimal("1000")}
        legs = [_make_leg("sell", "BTC/USDT", Decimal("50000"), Decimal("0.5"))]
        result = SimpleNamespace(status=SimpleNamespace(value="success"), legs=legs)
        PositionSizeLeakListener(sizes).on_execution_result(SimpleNamespace(), result)
        # max(0, 1000 - 25000) = 0 → pop
        assert "BTC/USDT" not in sizes
