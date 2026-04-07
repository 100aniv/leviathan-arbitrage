"""Trade request consumer — bridges strategy signals to atomic execution.

Subscribes to the "leviathan:trade_requests" stream, deserializes TradeRequest
events, performs risk checks, converts TradeLeg into Order objects, and routes
to AtomicExecutor for same-exchange or cross-exchange execution.

This is the CRITICAL missing pipeline component that wires strategies to execution.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from decimal import Decimal
from typing import Any, Callable, Optional, Protocol

from src.core.models import Order, OrderSide, OrderType, Trade
from src.execution.executor import AtomicExecutor, ExecutionResult, ExecutionStatus
from src.risk.kill_switch import is_halted
from src.strategies.base import TradeLeg, TradeRequest

logger = logging.getLogger(__name__)

# Stream and consumer group constants
TRADE_REQUEST_STREAM = "leviathan:trade_requests"
CONSUMER_GROUP = "trade_consumer_group"
CONSUMER_NAME = "trade_consumer_0"

# Polling interval when no messages are available
_POLL_INTERVAL_MS = 100
# Max messages per subscribe batch
_BATCH_SIZE = 5


class EventBusProtocol(Protocol):
    """Minimal event bus interface required by TradeRequestConsumer."""

    async def create_consumer_group(
        self, stream: str, group: str, start_id: str = "0"
    ) -> None: ...

    async def subscribe(
        self,
        stream: str,
        group: str,
        consumer: str,
        count: int = 10,
        block_ms: Optional[int] = None,
        raw: bool = False,
    ) -> list[dict]: ...

    async def ack_message(
        self, stream: str, group: str, msg_id: bytes | str
    ) -> None: ...


class RiskCheckProtocol(Protocol):
    """Callable risk check — returns (approved: bool, reason: str)."""

    def __call__(self, trade_request: TradeRequest) -> tuple[bool, str]: ...


# Default permissive risk check when no guardian is provided
def _default_risk_check(trade_request: TradeRequest) -> tuple[bool, str]:
    """Default risk check: always approve."""
    return True, ""


def _leg_to_order(leg: TradeLeg, strategy_id: str) -> Order:
    """Convert a TradeLeg into an Order object."""
    return Order(
        order_id=str(uuid.uuid4()),
        client_order_id=f"{strategy_id}_{uuid.uuid4().hex[:8]}",
        exchange_id=leg.exchange_id,
        symbol=leg.symbol,
        side=leg.side,
        order_type=leg.order_type,
        price=leg.price,
        amount=leg.size,
        metadata=leg.metadata,
    )


class TradeRequestConsumer:
    """
    Consumes TradeRequest events from the event bus and routes them
    to the AtomicExecutor for execution.

    Lifecycle:
        consumer = TradeRequestConsumer(event_bus, executor)
        await consumer.start()
        ...
        await consumer.stop()
    """

    def __init__(
        self,
        event_bus: EventBusProtocol,
        executor: AtomicExecutor,
        risk_check: RiskCheckProtocol | None = None,
        on_result: Callable[[TradeRequest, ExecutionResult], None] | None = None,
        min_edge: Decimal = Decimal("0.0001"),
    ) -> None:
        """
        Args:
            event_bus:   EventBus (Redis or InMemory) to subscribe to.
            executor:    AtomicExecutor for order placement.
            risk_check:  Optional callable (TradeRequest) -> (approved, reason).
                         Defaults to always-approve.
            on_result:   Optional callback invoked after each execution with
                         the original TradeRequest and the ExecutionResult.
            min_edge:    Minimum edge for cross-exchange execution.
        """
        self._event_bus = event_bus
        self._executor = executor
        self._risk_check = risk_check or _default_risk_check
        self._on_result = on_result
        self._min_edge = min_edge
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._recent_trades: dict[tuple, float] = {}

        # Counters for observability
        self.processed_count: int = 0
        self.risk_rejected_count: int = 0
        self.execution_success_count: int = 0
        self.execution_failure_count: int = 0
        self.error_count: int = 0

    async def start(self) -> None:
        """Start the consumer loop."""
        if self._running:
            logger.warning("TradeRequestConsumer already running")
            return

        # Ensure consumer group exists
        await self._event_bus.create_consumer_group(
            TRADE_REQUEST_STREAM, CONSUMER_GROUP
        )

        self._running = True
        self._task = asyncio.create_task(self._consume_loop())
        logger.info(
            "TradeRequestConsumer started on stream '%s'", TRADE_REQUEST_STREAM
        )

    async def stop(self) -> None:
        """Stop the consumer loop gracefully."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info(
            "TradeRequestConsumer stopped. processed=%d success=%d failed=%d rejected=%d errors=%d",
            self.processed_count,
            self.execution_success_count,
            self.execution_failure_count,
            self.risk_rejected_count,
            self.error_count,
        )

    async def _consume_loop(self) -> None:
        """Main consumption loop — poll for messages and process them."""
        while self._running:
            try:
                # Check kill switch before polling
                if is_halted():
                    logger.warning(
                        "TradeRequestConsumer: engine halted, pausing consumption"
                    )
                    await asyncio.sleep(_POLL_INTERVAL_MS / 1000.0)
                    continue

                messages = await self._event_bus.subscribe(
                    stream=TRADE_REQUEST_STREAM,
                    group=CONSUMER_GROUP,
                    consumer=CONSUMER_NAME,
                    count=_BATCH_SIZE,
                    block_ms=_POLL_INTERVAL_MS,
                )

                for msg in messages:
                    await self._process_message(msg)

            except asyncio.CancelledError:
                break
            except Exception:
                self.error_count += 1
                logger.exception("TradeRequestConsumer: unexpected error in consume loop")
                await asyncio.sleep(_POLL_INTERVAL_MS / 1000.0)

    async def _process_message(self, msg: dict[str, Any]) -> None:
        """Process a single trade request message."""
        try:
            # Deserialize TradeRequest from event data
            trade_request = TradeRequest.model_validate(msg)
        except (ValueError, TypeError):
            self.error_count += 1
            logger.exception(
                "TradeRequestConsumer: failed to deserialize TradeRequest"
            )
            return

        self.processed_count += 1

        # Position collision check: block duplicate (symbol, exchange_pair) within 10s window
        if trade_request.legs:
            trade_key = (
                frozenset(leg.symbol for leg in trade_request.legs),
                frozenset(leg.exchange_id for leg in trade_request.legs),
            )
            now = time.monotonic()
            self._recent_trades = {
                k: v for k, v in self._recent_trades.items() if now - v < 10.0
            }
            if trade_key in self._recent_trades:
                from src.infra.metrics import STRATEGY_OVERLAP_TOTAL

                STRATEGY_OVERLAP_TOTAL.labels(
                    symbol=",".join(sorted(trade_key[0])),
                    strategy=trade_request.strategy_id,
                ).inc()
                logger.warning(
                    "Position collision blocked: symbol=%s exchanges=%s strategy=%s seconds_since_last=%.2f",
                    trade_key[0],
                    trade_key[1],
                    trade_request.strategy_id,
                    now - self._recent_trades[trade_key],
                )
                return
            self._recent_trades[trade_key] = now

        # Check kill switch before each trade
        if is_halted():
            logger.warning(
                "TradeRequestConsumer: engine halted, skipping trade_request strategy=%s",
                trade_request.strategy_id,
            )
            return

        # Risk check
        try:
            approved, reason = self._risk_check(trade_request)
        except Exception:
            self.error_count += 1
            logger.exception(
                "TradeRequestConsumer: risk check raised exception for strategy=%s",
                trade_request.strategy_id,
            )
            return

        if not approved:
            self.risk_rejected_count += 1
            logger.info(
                "TradeRequestConsumer: risk rejected strategy=%s reason=%s",
                trade_request.strategy_id,
                reason,
            )
            return

        # PHOENIX: Filter trades where any leg notional < $10 (exchange min $5 + buffer)
        # Prevents imbalanced positions from per-adapter min_notional boosts.
        _MIN_TRADE_NOTIONAL = Decimal("10")
        _small_legs = [
            leg for leg in trade_request.legs
            if leg.price and leg.price > 0 and leg.size * leg.price < _MIN_TRADE_NOTIONAL
        ]
        if _small_legs:
            logger.info(
                "trade_consumer.min_notional_filtered strategy=%s legs=%d max_notional_usd=%.2f",
                trade_request.strategy_id,
                len(_small_legs),
                float(max(l.size * l.price for l in _small_legs if l.price)),
            )
            return

        # Convert legs to orders
        legs = trade_request.legs
        if len(legs) < 2:
            self.error_count += 1
            logger.error(
                "TradeRequestConsumer: trade_request has fewer than 2 legs strategy=%s",
                trade_request.strategy_id,
            )
            return

        orders = [_leg_to_order(leg, trade_request.strategy_id) for leg in legs]

        # Route to same-exchange or cross-exchange execution
        result = await self._execute(trade_request, orders)

        # Record result
        if result.status == ExecutionStatus.SUCCESS:
            self.execution_success_count += 1
        else:
            self.execution_failure_count += 1

        # Invoke result callback (for PositionManager integration)
        if self._on_result is not None:
            try:
                self._on_result(trade_request, result)
            except Exception:
                logger.exception(
                    "TradeRequestConsumer: on_result callback raised exception"
                )

    async def _execute(
        self, trade_request: TradeRequest, orders: list[Order]
    ) -> ExecutionResult:
        """Route execution based on whether legs share an exchange."""
        try:
            # Determine routing: multi-leg same-exchange, same-exchange 2-leg, or cross-exchange
            exchange_ids = {o.exchange_id for o in orders}
            if len(orders) > 2 and len(exchange_ids) == 1:
                result = await self._executor.execute_multi_leg(
                    exchange_id=orders[0].exchange_id,
                    orders=orders,
                    strategy_id=trade_request.strategy_id,
                )
            elif len(exchange_ids) == 1:
                result = await self._executor.execute_same_exchange(
                    exchange_id=orders[0].exchange_id,
                    leg1_order=orders[0],
                    leg2_order=orders[1],
                    strategy_id=trade_request.strategy_id,
                )
            else:
                result = await self._executor.execute_cross_exchange(
                    leg1_order=orders[0],
                    leg2_order=orders[1],
                    strategy_id=trade_request.strategy_id,
                    min_edge=self._min_edge,
                )

            logger.info(
                "TradeRequestConsumer: execution complete strategy=%s status=%s",
                trade_request.strategy_id,
                result.status.value,
            )
            return result

        except Exception as exc:
            self.error_count += 1
            logger.exception(
                "TradeRequestConsumer: execution raised exception strategy=%s",
                trade_request.strategy_id,
            )
            # Return a rejected result so the caller always gets an ExecutionResult
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                legs=[],
                error=f"Execution exception: {exc}",
                strategy_id=trade_request.strategy_id,
            )
