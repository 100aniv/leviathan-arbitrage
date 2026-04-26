"""Phase 6 Step 3 — on_execution_result dispatcher delegation 검증.

When engine._listener_dispatcher is set, on_execution_result should:
1. Delegate to dispatcher.dispatch(request, result)
2. Return early (skip legacy 360 LOC body)
3. Fall back to legacy on dispatcher exception (resilience)

When dispatcher is None, legacy path runs unchanged (backward compat).
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.runtime.risk_execution import on_execution_result


def _make_engine(dispatcher=None):
    """Minimal engine stub for on_execution_result."""
    return SimpleNamespace(
        _listener_dispatcher=dispatcher,
        _settings=SimpleNamespace(capital=SimpleNamespace(initial_capital=Decimal("70"))),
        _exchanges={"binance": MagicMock()},
        _position_sizes={},
        _position_manager=None,
        _pm_queue=None,
        _cross_exchange_positions=set(),
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
        context=SimpleNamespace(trade_history=[]),
    )


def _make_request():
    return SimpleNamespace(
        strategy_id="test_strategy",
        legs=[],
        expected_profit_usdt=Decimal("0"),
    )


def _make_result():
    return SimpleNamespace(
        status=SimpleNamespace(value="success"),
        pnl=Decimal("1.5"),
        legs=[],
    )


class TestOnExecutionResultDelegation:
    def test_legacy_path_when_dispatcher_none(self) -> None:
        """No dispatcher → legacy code runs (smoke test, no exception)."""
        engine = _make_engine(dispatcher=None)
        on_execution_result(engine, _make_request(), _make_result())
        # Legacy path: total_pnl incremented from result.pnl
        assert engine._total_pnl == Decimal("1.5")

    @pytest.mark.asyncio
    async def test_delegates_to_dispatcher_when_set(self) -> None:
        """Dispatcher set → dispatch() called, legacy SKIPPED."""
        dispatcher = MagicMock()
        dispatcher.dispatch = AsyncMock()
        engine = _make_engine(dispatcher=dispatcher)
        request = _make_request()
        result = _make_result()

        on_execution_result(engine, request, result)
        # Wait for ensure_future to complete
        await asyncio.sleep(0.05)

        # dispatcher.dispatch was called
        dispatcher.dispatch.assert_called_once_with(request, result)
        # Legacy path SKIPPED → total_pnl untouched (legacy would increment)
        assert engine._total_pnl == Decimal("0")

    @pytest.mark.asyncio
    async def test_falls_back_to_legacy_on_dispatcher_exception(self) -> None:
        """Dispatcher.dispatch raising → legacy code runs as fallback."""
        dispatcher = MagicMock()
        # Make ensure_future scheduling itself raise (sync exception)
        dispatcher.dispatch = MagicMock(side_effect=RuntimeError("dispatcher exploded"))
        engine = _make_engine(dispatcher=dispatcher)
        request = _make_request()
        result = _make_result()

        on_execution_result(engine, request, result)

        # Legacy path executed → total_pnl incremented
        assert engine._total_pnl == Decimal("1.5")
