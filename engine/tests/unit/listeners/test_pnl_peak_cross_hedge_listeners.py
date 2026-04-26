"""Phase 5.2.4 PnLPeakListener + CrossHedgeListener 검증."""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.core.engine_state import EngineState
from src.listeners.cross_hedge_listener import CrossHedgeListener
from src.listeners.pnl_peak_listener import PnLPeakListener
from src.ports.listener_port import ExecutionResultListener


class TestPnLPeakListener:
    def test_protocol(self) -> None:
        assert isinstance(PnLPeakListener(EngineState()), ExecutionResultListener)

    def test_skips_when_status_not_success(self) -> None:
        state = EngineState()
        result = SimpleNamespace(status=SimpleNamespace(value="failure"), legs=[], pnl=None)
        PnLPeakListener(state).on_execution_result(SimpleNamespace(), result)
        assert state.total_pnl == Decimal("0")

    def test_pnl_increments(self) -> None:
        state = EngineState()
        result = SimpleNamespace(
            status=SimpleNamespace(value="success"), legs=[],
            pnl=Decimal("3.5"),
        )
        PnLPeakListener(state).on_execution_result(SimpleNamespace(), result)
        assert state.total_pnl == Decimal("3.5")

    def test_peak_equity_updates(self) -> None:
        state = EngineState()
        state.peak_equity = Decimal("100")
        result = SimpleNamespace(
            status=SimpleNamespace(value="success"), legs=[],
            pnl=Decimal("10"),
        )
        listener = PnLPeakListener(
            state,
            capital_total_supplier=lambda: Decimal("100"),
        )
        listener.on_execution_result(SimpleNamespace(), result)
        # current_equity = 100 + 10 = 110 > peak 100 → updated
        assert state.peak_equity == Decimal("110")


class TestCrossHedgeListener:
    def _make_leg(self, side: str, symbol: str, exchange: str,
                   price: Decimal, amount: Decimal,
                   metadata=None) -> SimpleNamespace:
        return SimpleNamespace(
            order=SimpleNamespace(
                side=SimpleNamespace(value=side),
                symbol=symbol,
                exchange_id=exchange,
                metadata=metadata,
            ),
            trade=SimpleNamespace(price=price, amount=amount),
        )

    def test_protocol(self) -> None:
        assert isinstance(CrossHedgeListener(EngineState()), ExecutionResultListener)

    def test_cross_exchange_buy_sell_tracked(self) -> None:
        state = EngineState()
        legs = [
            self._make_leg("BUY", "BTC/USDT", "binance", Decimal("50000"), Decimal("0.1")),
            self._make_leg("SELL", "BTC/USDT", "okx", Decimal("50100"), Decimal("0.1")),
        ]
        result = SimpleNamespace(status=SimpleNamespace(value="success"), legs=legs)
        CrossHedgeListener(state).on_execution_result(SimpleNamespace(), result)
        assert "BTC/USDT" in state.cross_exchange_positions
        # gross = 50000*0.1 + 50100*0.1 = 10010
        assert state.cross_gross_exposure == Decimal("10010")

    def test_close_decrements(self) -> None:
        state = EngineState()
        state.cross_exchange_positions.add("BTC/USDT")
        state.cross_gross_exposure = Decimal("10010")
        legs = [
            self._make_leg("SELL", "BTC/USDT", "binance", Decimal("50000"), Decimal("0.1"),
                           metadata={"reduceOnly": True}),
            self._make_leg("BUY", "BTC/USDT", "okx", Decimal("50000"), Decimal("0.1"),
                           metadata={"reduceOnly": True}),
        ]
        result = SimpleNamespace(status=SimpleNamespace(value="success"), legs=legs)
        CrossHedgeListener(state).on_execution_result(SimpleNamespace(), result)
        assert "BTC/USDT" not in state.cross_exchange_positions
        # gross = 10010 - (50000*0.1 + 50000*0.1) = 10010 - 10000 = 10 (clamped 0 if neg)
        assert state.cross_gross_exposure == Decimal("10")
