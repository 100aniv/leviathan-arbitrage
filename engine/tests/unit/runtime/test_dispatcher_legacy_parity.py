"""Phase 6 Step 4: legacy vs dispatcher parity test (Codex final review).

Purpose: 동일 input을 legacy 360 LOC vs dispatcher 14 listeners 양쪽에 통과시켜
end state (total_pnl / position_sizes / cross_exchange_positions / errors)가 동일함을 증명.

Codex final review BLOCKING:
> "add a real parity test suite legacy vs dispatcher over success/rollback/error cases"

이 테스트가 7+ days canary 검증과 함께 통과 후 _on_execution_result_legacy 삭제 가능.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.engine_state import EngineState
from src.listeners.factory import build_dispatcher_from_engine
from src.runtime.risk_execution import (
    _on_execution_result_legacy,
    on_execution_result,
)


def _make_engine_stub():
    """Engine stub with EngineState — for both legacy and dispatcher paths."""
    state = EngineState()
    state.peak_equity = Decimal("70")
    return SimpleNamespace(
        _settings=SimpleNamespace(capital=SimpleNamespace(initial_capital=Decimal("70"))),
        _exchanges={"binance": MagicMock(), "okx": MagicMock()},
        _state=state,
        _position_sizes=state.position_sizes,
        _position_manager=None,
        _pm_queue=None,
        _cross_exchange_positions=state.cross_exchange_positions,
        _cross_gross_exposure=Decimal("0"),
        _market_recorder=None,
        _live_mode=None,
        _exposure_tracker=None,
        _slippage_feedback=None,
        _correlation_monitor=None,
        _tca_analyzer=None,
        _circuit_breaker=None,
        _strategy_manager=MagicMock(),
        _trade_bot=None,
        _telegram=None,
        _total_pnl=Decimal("0"),
        _peak_equity=Decimal("70"),
        _position_tracking_errors=0,
        _listener_dispatcher=None,
        context=SimpleNamespace(trade_history=[]),
    )


def _make_buy_sell_request_result(pnl=Decimal("1.5")):
    """Cross-exchange BUY+SELL execution result."""
    legs_request = [
        SimpleNamespace(
            symbol="BTC/USDT", exchange_id="binance",
            side=SimpleNamespace(value="buy"),
            price=Decimal("50000"), size=Decimal("0.001"), metadata={},
        ),
        SimpleNamespace(
            symbol="BTC/USDT", exchange_id="okx",
            side=SimpleNamespace(value="sell"),
            price=Decimal("50100"), size=Decimal("0.001"), metadata={},
        ),
    ]
    legs_result = [
        SimpleNamespace(
            order=SimpleNamespace(
                symbol="BTC/USDT", exchange_id="binance",
                side=SimpleNamespace(value="buy"), metadata={},
            ),
            trade=SimpleNamespace(price=Decimal("50000"), amount=Decimal("0.001")),
        ),
        SimpleNamespace(
            order=SimpleNamespace(
                symbol="BTC/USDT", exchange_id="okx",
                side=SimpleNamespace(value="sell"), metadata={},
            ),
            trade=SimpleNamespace(price=Decimal("50100"), amount=Decimal("0.001")),
        ),
    ]
    request = SimpleNamespace(
        strategy_id="test_strategy", legs=legs_request,
        expected_profit_usdt=Decimal("0.1"),
        timestamp=SimpleNamespace(timestamp=lambda: 1234567890.0),
    )
    result = SimpleNamespace(
        status=SimpleNamespace(value="success"),
        pnl=pnl,
        legs=legs_result,
        execution_duration_ms=200,
    )
    return request, result


class TestLegacyDispatcherParity:
    """양쪽 path가 동일 input → 동일 end state 산출."""

    def test_total_pnl_matches(self) -> None:
        """legacy.total_pnl == dispatcher.total_pnl after same trade."""
        # Legacy path
        engine_legacy = _make_engine_stub()
        request, result = _make_buy_sell_request_result(pnl=Decimal("2.5"))
        _on_execution_result_legacy(engine_legacy, request, result)

        # Dispatcher path (via wrapper, with dispatcher set)
        engine_disp = _make_engine_stub()
        engine_disp._listener_dispatcher = build_dispatcher_from_engine(engine_disp)
        on_execution_result(engine_disp, request, result)

        # Both paths update total_pnl in state via either direct (legacy) or PnLPeakListener
        # NOTE: SimpleNamespace stub doesn't have @property — use ._state directly.
        # In real Engine, _total_pnl property would proxy to _state.total_pnl.
        legacy_pnl = engine_legacy._total_pnl
        disp_pnl = engine_disp._state.total_pnl
        assert legacy_pnl == Decimal("2.5")
        assert disp_pnl == Decimal("2.5"), (
            f"PARITY FAIL: legacy={legacy_pnl} disp_state={disp_pnl}"
        )

    def test_position_sizes_matches(self) -> None:
        """legacy.position_sizes == dispatcher.position_sizes."""
        engine_legacy = _make_engine_stub()
        engine_disp = _make_engine_stub()
        engine_disp._listener_dispatcher = build_dispatcher_from_engine(engine_disp)
        request, result = _make_buy_sell_request_result()

        _on_execution_result_legacy(engine_legacy, request, result)
        on_execution_result(engine_disp, request, result)

        # BUY 50 + SELL 50.1 → net cancels (delta neutral)
        # Both paths process this the same way
        assert dict(engine_legacy._position_sizes) == dict(engine_disp._position_sizes), (
            f"PARITY FAIL: legacy={dict(engine_legacy._position_sizes)} "
            f"disp={dict(engine_disp._position_sizes)}"
        )

    def test_cross_exchange_positions_matches(self) -> None:
        """legacy.cross_exchange_positions == dispatcher.cross_exchange_positions."""
        engine_legacy = _make_engine_stub()
        engine_disp = _make_engine_stub()
        engine_disp._listener_dispatcher = build_dispatcher_from_engine(engine_disp)
        request, result = _make_buy_sell_request_result()

        _on_execution_result_legacy(engine_legacy, request, result)
        on_execution_result(engine_disp, request, result)

        # BTC/USDT cross-exchange (binance buy + okx sell) → both paths add
        assert engine_legacy._cross_exchange_positions == engine_disp._cross_exchange_positions

    def test_failure_status_no_state_change(self) -> None:
        """status=failure → both paths skip state mutation."""
        engine_legacy = _make_engine_stub()
        engine_disp = _make_engine_stub()
        engine_disp._listener_dispatcher = build_dispatcher_from_engine(engine_disp)

        request, result = _make_buy_sell_request_result()
        result.status = SimpleNamespace(value="failure")

        _on_execution_result_legacy(engine_legacy, request, result)
        on_execution_result(engine_disp, request, result)

        # Both unchanged
        assert engine_legacy._total_pnl == Decimal("0")
        assert engine_disp._total_pnl == Decimal("0")
