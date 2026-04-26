"""Phase 6 ExecutionResultDispatcher integration test — 14 listeners 동시 dispatch 검증."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.engine_state import EngineState
from src.listeners.factory import build_dispatcher_from_engine


class TestDispatcherIntegration:
    """14 listeners dispatch flow 통합 검증."""

    def _stub_engine(self) -> SimpleNamespace:
        return SimpleNamespace(
            _settings=SimpleNamespace(capital=SimpleNamespace(initial_capital=Decimal("70"))),
            _exchanges={"binance": MagicMock(), "okx": MagicMock()},
            _state=EngineState(),
            _position_sizes={},
            _position_manager=MagicMock(),
            _pm_queue=None,
            _cross_exchange_positions=set(),
            _cross_gross_exposure=Decimal("0"),
            _market_recorder=MagicMock(),
            _live_mode=None,
            _exposure_tracker=AsyncMock(),
            _slippage_feedback=MagicMock(),
            _correlation_monitor=MagicMock(),
            _tca_analyzer=MagicMock(),
            _circuit_breaker=MagicMock(),
            _strategy_manager=MagicMock(),
            _trade_bot=MagicMock(),
            _total_pnl=Decimal("0"),
            context=SimpleNamespace(trade_history=[]),
        )

    @pytest.mark.asyncio
    async def test_dispatch_does_not_raise(self) -> None:
        """14 listeners 모두 dispatch 시 예외 0건 (failure isolation)."""
        engine = self._stub_engine()
        dispatcher = build_dispatcher_from_engine(engine)
        request = SimpleNamespace(
            strategy_id="test",
            legs=[],
            expected_profit_usdt=Decimal("0"),
        )
        result = SimpleNamespace(
            status=SimpleNamespace(value="success"),
            pnl=Decimal("1"),
            legs=[],
        )
        # listener 14개 모두 호출되지만 legs 없어도 graceful — 예외 없어야
        await dispatcher.dispatch(request, result)

    @pytest.mark.asyncio
    async def test_state_mutation_via_pnl_listener(self) -> None:
        """PnLPeakListener가 EngineState.total_pnl mutate 검증."""
        engine = self._stub_engine()
        engine._state.peak_equity = Decimal("100")  # initialized
        dispatcher = build_dispatcher_from_engine(engine)
        request = SimpleNamespace(strategy_id="x", legs=[],
                                   expected_profit_usdt=Decimal("0"))
        result = SimpleNamespace(
            status=SimpleNamespace(value="success"),
            pnl=Decimal("5.5"),
            legs=[],
        )
        await dispatcher.dispatch(request, result)
        assert engine._state.total_pnl == Decimal("5.5")
