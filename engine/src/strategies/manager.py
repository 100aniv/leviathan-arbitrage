"""Strategy lifecycle manager.

Manages start/stop/reconfigure of strategies at runtime without restart.
Subscribes to signals from Redis Streams consumer group and routes them
to registered strategies.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from src.core.events import SignalEvent
from src.core.models import Signal
from src.infra.redis.event_bus import EventBus
from src.strategies.base import BaseStrategy, StrategyMetrics, TradeRequest

logger = logging.getLogger(__name__)

SIGNAL_STREAM = "leviathan:signals"
CONSUMER_GROUP = "strategy-manager"


class StrategyManager:
    """
    Manages the lifecycle of all registered arbitrage strategies.

    - Register/deregister strategies at runtime
    - Start/stop individual strategies without full restart
    - Subscribe to Redis Streams signals and route to appropriate strategies
    - Collect and expose aggregated metrics
    """

    def __init__(
        self,
        event_bus: EventBus,
        consumer_name: str = "manager-0",
        poll_interval_ms: int = 100,
    ) -> None:
        self._event_bus = event_bus
        self._consumer_name = consumer_name
        self._poll_interval_ms = poll_interval_ms
        self._strategies: dict[str, BaseStrategy] = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None  # type: ignore[type-arg]

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, strategy: BaseStrategy) -> None:
        """Register a strategy. Replaces existing if same ID."""
        self._strategies[strategy.strategy_id] = strategy
        logger.info("Registered strategy %s", strategy.strategy_id)

    def deregister(self, strategy_id: str) -> None:
        """Remove a strategy (stops it first if active)."""
        strategy = self._strategies.pop(strategy_id, None)
        if strategy is not None:
            logger.info("Deregistered strategy %s", strategy_id)

    def get_strategy(self, strategy_id: str) -> Optional[BaseStrategy]:
        return self._strategies.get(strategy_id)

    def list_strategies(self) -> list[str]:
        return list(self._strategies.keys())

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start_strategy(self, strategy_id: str) -> None:
        """Start a specific registered strategy."""
        strategy = self._strategies.get(strategy_id)
        if strategy is None:
            raise KeyError(f"Strategy {strategy_id!r} not registered")
        await strategy.start()
        logger.info("Started strategy %s", strategy_id)

    async def stop_strategy(self, strategy_id: str) -> None:
        """Stop a specific registered strategy."""
        strategy = self._strategies.get(strategy_id)
        if strategy is None:
            raise KeyError(f"Strategy {strategy_id!r} not registered")
        await strategy.stop()
        logger.info("Stopped strategy %s", strategy_id)

    async def reconfigure(self, strategy_id: str, config: Any) -> None:
        """
        Reconfigure a strategy at runtime.

        The strategy must expose a `.config` attribute (Pydantic model).
        Stops and restarts the strategy around the config swap.
        """
        strategy = self._strategies.get(strategy_id)
        if strategy is None:
            raise KeyError(f"Strategy {strategy_id!r} not registered")

        was_active = strategy.is_active
        if was_active:
            await strategy.stop()

        strategy.config = config  # type: ignore[attr-defined]

        if was_active:
            await strategy.start()
        logger.info("Reconfigured strategy %s", strategy_id)

    # ------------------------------------------------------------------
    # Signal consumption loop
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the signal consumption loop."""
        if self._running:
            return
        self._running = True
        await self._event_bus.create_consumer_group(
            stream=SIGNAL_STREAM,
            group=CONSUMER_GROUP,
            start_id="$",  # only new messages
        )
        self._task = asyncio.create_task(self._consume_loop())
        logger.info("StrategyManager started — consuming %s", SIGNAL_STREAM)

    async def stop(self) -> None:
        """Stop the signal consumption loop gracefully."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Stop all active strategies
        for strategy in self._strategies.values():
            if strategy.is_active:
                await strategy.stop()
        logger.info("StrategyManager stopped")

    async def _consume_loop(self) -> None:
        """Main loop: poll Redis Streams and route signals to strategies."""
        while self._running:
            try:
                messages = await self._event_bus.subscribe(
                    stream=SIGNAL_STREAM,
                    group=CONSUMER_GROUP,
                    consumer=self._consumer_name,
                    count=20,
                    block_ms=self._poll_interval_ms,
                )
                for msg in messages:
                    await self._dispatch(msg)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Error in consume loop: %s", exc, exc_info=True)
                await asyncio.sleep(1)

    async def _dispatch(self, raw: dict[str, Any]) -> None:
        """Parse a raw event dict and route the signal to matching strategies."""
        try:
            event = SignalEvent.model_validate(raw)
            signal: Signal = event.signal
        except Exception as exc:
            logger.warning("Failed to parse signal event: %s", exc)
            return

        # Route to all active strategies that match the signal's strategy_id prefix
        # or broadcast to all if signal.strategy_id is empty
        matched = False
        for strategy in self._strategies.values():
            if not strategy.is_active:
                continue
            # Match by strategy type prefix embedded in signal.strategy_id
            # e.g. signal.strategy_id = "cross_exchange_spot_v1" matches
            # any registered cross_exchange strategy
            if self._should_route(strategy, signal):
                matched = True
                try:
                    result: Optional[TradeRequest] = await strategy.on_signal(signal)
                    if result is not None:
                        if getattr(strategy, "shadow_mode", False):
                            logger.debug(
                                "Shadow strategy %s generated TradeRequest (not emitting)",
                                strategy.strategy_id,
                            )
                        else:
                            await self._emit_trade_request(result)
                except Exception as exc:
                    logger.error(
                        "Strategy %s raised on signal: %s",
                        strategy.strategy_id,
                        exc,
                        exc_info=True,
                    )

        if not matched:
            logger.debug("No active strategy matched signal %s", signal.strategy_id)

    # Strategies that also consume cross-exchange price signals for derived analysis
    _CROSS_EXCHANGE_CONSUMERS: frozenset[str] = frozenset({
        "statistical_arb",  # accumulates spread history → z-score entry
        "latency_arb",      # compares update timing across exchanges
    })

    def _should_route(self, strategy: BaseStrategy, signal: Signal) -> bool:
        """Return True if this strategy should handle the signal."""
        # Broadcast: empty or wildcard strategy_id routes to ALL active strategies
        if not signal.strategy_id or signal.strategy_id == "*":
            return True

        # Exact match on strategy instance ID
        if signal.strategy_id == strategy.strategy_id:
            return True

        # Match by STRATEGY_TYPE: either direction substring match
        strategy_type = getattr(strategy, "STRATEGY_TYPE", None)
        if strategy_type:
            # "cross_exchange_spot" in "cross_exchange_spot_v1" OR vice versa
            if strategy_type in signal.strategy_id or signal.strategy_id in strategy_type:
                return True

            # Derived strategies that consume cross-exchange signals
            if "cross_exchange" in signal.strategy_id and strategy_type in self._CROSS_EXCHANGE_CONSUMERS:
                return True

        return False

    async def _emit_trade_request(self, request: TradeRequest) -> None:
        """Publish TradeRequest to the execution stream."""
        payload = request.model_dump(mode="json")
        await self._event_bus.publish("leviathan:trade_requests", payload)
        logger.debug("Emitted TradeRequest for strategy %s", request.strategy_id)

    async def route_signal(self, signal: Signal) -> list[TradeRequest]:
        """Route signal directly to matching active strategies (no Redis).

        Used by ShadowMode for in-process signal routing.
        Returns list of TradeRequests from strategies that accepted the signal.
        Reuses _should_route() for consistent matching with _dispatch().
        """
        results: list[TradeRequest] = []
        for strategy in self._strategies.values():
            if not strategy.is_active:
                continue
            if not self._should_route(strategy, signal):
                logger.debug(
                    "route_signal: skip %s for signal %s",
                    strategy.strategy_id, signal.strategy_id,
                )
                continue
            try:
                request = await strategy.on_signal(signal)
                if request is not None:
                    results.append(request)
            except Exception as exc:
                logger.error(
                    "Strategy %s raised on route_signal: %s",
                    strategy.strategy_id, exc, exc_info=True,
                )
        if not results:
            logger.debug("route_signal: no strategy accepted signal %s", signal.strategy_id)
        return results

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def get_metrics(self) -> dict[str, StrategyMetrics]:
        """Return metrics snapshot for all registered strategies."""
        return {sid: s.metrics for sid, s in self._strategies.items()}

    def get_all_metrics_summary(self) -> dict[str, Any]:
        """Return aggregated metrics across all strategies."""
        total_signals = 0
        total_requests = 0
        total_fills = 0
        per_strategy = {}

        for sid, strategy in self._strategies.items():
            m = strategy.metrics
            total_signals += m.signals_received
            total_requests += m.trade_requests_generated
            total_fills += m.fills_received
            per_strategy[sid] = m.model_dump()

        return {
            "total_signals_received": total_signals,
            "total_trade_requests": total_requests,
            "total_fills": total_fills,
            "strategies": per_strategy,
        }
