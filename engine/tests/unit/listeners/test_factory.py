"""Phase 5.2.6 ListenerFactory 검증."""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.core.engine_state import EngineState
from src.listeners.factory import build_dispatcher_from_engine


class TestListenerFactory:
    def _make_engine_stub(self) -> SimpleNamespace:
        """Phase 5.2.2 EngineState 통합된 Engine 미러."""
        return SimpleNamespace(
            _settings=SimpleNamespace(
                capital=SimpleNamespace(initial_capital=Decimal("70")),
            ),
            _exchanges={"binance": MagicMock(), "okx": MagicMock()},
            _state=EngineState(),
            _position_sizes={},
            _position_manager=MagicMock(),
            _pm_queue=None,
            _cross_exchange_positions=set(),
            _cross_gross_exposure=Decimal("0"),
            _market_recorder=MagicMock(),
            _live_mode=None,
            _exposure_tracker=MagicMock(),
            _slippage_feedback=MagicMock(),
            _correlation_monitor=MagicMock(),
            _tca_analyzer=MagicMock(),
            _circuit_breaker=MagicMock(),
            _strategy_manager=MagicMock(),
            _trade_bot=MagicMock(),
            _total_pnl=Decimal("0"),
            context=SimpleNamespace(trade_history=[]),
        )

    def test_builds_14_listeners(self) -> None:
        engine = self._make_engine_stub()
        dispatcher = build_dispatcher_from_engine(engine)
        assert dispatcher.listener_count == 14

    def test_listener_names_match_design(self) -> None:
        engine = self._make_engine_stub()
        dispatcher = build_dispatcher_from_engine(engine)
        expected = [
            "log",
            "position_size_leak",
            "position_manager",
            "cross_hedge",
            "pnl_peak",
            "market_recorder",
            "exposure",
            "slippage",
            "correlation",
            "tca",
            "trade_history",
            "circuit_breaker",
            "rollback",
            "telegram",
        ]
        assert dispatcher.listener_names == expected
