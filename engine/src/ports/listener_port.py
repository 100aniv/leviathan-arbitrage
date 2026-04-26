"""ExecutionResultListener Port — Phase 5.2.4 (2026-04-26).

on_execution_result 360 LOC god-function → 14 single-responsibility listeners.
각 listener implements this Protocol.

설계 원칙:
- Listeners run sequentially in registration order.
- Failure in one listener MUST NOT prevent later listeners (dispatcher decorates try/except).
- Listeners SHOULD be idempotent (journal recovery may replay).
- Listeners MUST NOT mutate TradeRequest or ExecutionResult.
- async listeners use ``async def on_execution_result``; dispatcher detects via
  ``inspect.iscoroutinefunction`` and either awaits or schedules with ``asyncio.ensure_future``.

산업 표준 비교:
- Nautilus EventBus + handlers (handle on_filled / on_order_filled / ...)
- LEAN OnOrderEvent callback chain
- Hummingbot OrderTracker observer pattern
"""
from __future__ import annotations

from typing import Any, Awaitable, Protocol, Union, runtime_checkable


@runtime_checkable
class ExecutionResultListener(Protocol):
    """Single-responsibility post-trade callback.

    Implementations:
    - LogListener (trivial header trace)
    - PositionSizeLeakListener (BUY/SELL net aggregation)
    - PositionManagerListener (async open/close dispatch via PM queue)
    - CrossHedgeListener (delta-neutral position tracking)
    - PnLPeakListener (total_pnl + peak_equity update)
    - MarketRecorderListener (TimescaleDB record)
    - ExposureListener (Redis exposure update)
    - SlippageListener (slippage feedback)
    - CorrelationListener (per-strategy correlation matrix)
    - TCAListener (transaction cost analysis)
    - TradeHistoryListener (in-memory + Redis history append)
    - CircuitBreakerListener (consecutive_loss tracking)
    - RollbackListener (rollback path notification)
    - TelegramListener (alert notification)
    """

    name: str
    """Listener identifier for logging + ordering."""

    def on_execution_result(
        self,
        request: Any,  # TradeRequest
        result: Any,   # ExecutionResult
    ) -> Union[None, Awaitable[None]]:
        """Run listener's post-trade logic.

        Implementations may return None (sync) or Awaitable[None] (async).
        Dispatcher handles both via inspect.iscoroutinefunction.

        MUST NOT raise — wrap any failure-prone calls in try/except + log.
        Dispatcher's safety net catches uncaught exceptions but listener's
        own try/except keeps the listener's contract clean.
        """
        ...
