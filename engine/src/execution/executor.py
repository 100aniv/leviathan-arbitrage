"""
Atomic execution engine — same-exchange (parallel) and cross-exchange (sequential).

Amendment 4: Cross-Exchange Atomic Execution Protocol (14-step, sequential).
Amendment 5: Same-Exchange Race Conditions (11 scenarios).
Amendment 3C: Max Rollback Cost Gate (pre-trade bound).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

from src.core.config import get_settings
from src.core.models import Order, OrderSide, OrderType, Trade
from src.infra.exchange.base import ExchangeAdapter
from src.risk.kill_switch import halt_local, is_halted

logger = logging.getLogger(__name__)


def _async_log_info(msg: str, *args: Any) -> None:
    """PHOENIX §8.3 Tier1 patch 3-1: defer non-critical INFO logging off hot path.

    Schedules logger.info via call_soon so the executor can return faster.
    Does NOT apply to ERROR/CRITICAL — those remain synchronous for safety.
    """
    try:
        loop = asyncio.get_event_loop()
        loop.call_soon(lambda: logger.info(msg, *args))
    except RuntimeError:
        # Fallback if no running loop (shouldn't happen in async context)
        logger.info(msg, *args)

# Health score threshold (Amendment 4 step 1)
_HEALTH_THRESHOLD = 0.6  # PHOENIX: REST-only adapters score 0.65 (stale after 20s but before API call)
# Partial fill acceptance threshold
_PARTIAL_FILL_THRESHOLD = Decimal("0.80")
# Blueprint compliance: LEG_TIMEOUT_MS from settings singleton
_LEG_TIMEOUT_MS = get_settings().execution.leg_timeout_ms
_ROLLBACK_TIMEOUT_MS = get_settings().execution.rollback_timeout_ms
_RECONCILIATION_INTERVAL_S = get_settings().execution.reconciliation_interval_s


class ExecutionStatus(StrEnum):
    SUCCESS = "success"
    REJECTED = "rejected"          # pre-validation failed, no orders placed
    ROLLED_BACK = "rolled_back"    # orders placed then cancelled successfully
    ROLLBACK_FAILED = "rollback_failed"  # rollback attempt failed (stranded)
    TIMEOUT = "timeout"


@dataclass
class LegResult:
    order: Order
    trade: Trade | None = None
    error: str | None = None
    expected_price: Decimal | None = None   # US-132: price from order request
    fill_price: Decimal | None = None        # US-132: actual simulated/live fill price

    @property
    def filled_amount(self) -> Decimal:
        if self.trade is None:
            return Decimal("0")
        return self.trade.amount

    @property
    def filled_ratio(self) -> float:
        """Fraction of requested order filled (0.0–1.0). Used by TCA (US-134)."""
        return float(self.fill_ratio(self.order.amount))

    def fill_ratio(self, requested: Decimal) -> Decimal:
        if requested <= 0:
            return Decimal("0")
        return self.filled_amount / requested


@dataclass(init=False)
class ExecutionResult:
    status: ExecutionStatus
    legs: list[LegResult]
    rollback_cost: Decimal
    error: str
    strategy_id: str

    def __init__(
        self,
        status: ExecutionStatus,
        legs: list[LegResult] | None = None,
        leg1: LegResult | None = None,
        leg2: LegResult | None = None,
        rollback_cost: Decimal = Decimal("0"),
        error: str = "",
        strategy_id: str = "",
    ) -> None:
        self.status = status
        if legs is not None:
            self.legs = legs
        else:
            self.legs = [l for l in [leg1, leg2] if l is not None]
        self.rollback_cost = rollback_cost
        self.error = error
        self.strategy_id = strategy_id

    @property
    def leg1(self) -> LegResult | None:
        return self.legs[0] if len(self.legs) > 0 else None

    @property
    def leg2(self) -> LegResult | None:
        return self.legs[1] if len(self.legs) > 1 else None


@dataclass
class ExecutionConfig:
    timeout_ms: int = _LEG_TIMEOUT_MS
    partial_fill_threshold: Decimal = Decimal("0.80")
    post_reconcile_delay_s: float = float(_RECONCILIATION_INTERVAL_S)
    health_threshold: float = _HEALTH_THRESHOLD


class AtomicExecutor:
    """
    Atomic multi-leg execution engine.

    Same-exchange: both legs submitted in parallel via asyncio.gather.
    Cross-exchange: sequential submission per Amendment 4 (14-step protocol).
    """

    def __init__(
        self,
        exchanges: dict[str, ExchangeAdapter],
        config: ExecutionConfig | None = None,
    ) -> None:
        self._exchanges = exchanges
        self._config = config or ExecutionConfig()
        # Per-exchange capital locks (asyncio.Lock prevents concurrent executions)
        self._locks: dict[str, asyncio.Lock] = {}
        from src.execution.stranded import StrandedPositionTracker
        self._stranded_tracker = StrandedPositionTracker()
        # PHOENIX v18: Rollback dedup — order_id → "success"|"failed"
        self._rollback_attempted: dict[str, str] = {}
        # PHOENIX v32: DeduplicationGate — Bug 26 fix (race-condition duplicate orders)
        from src.execution.dedup import DeduplicationGate
        self._dedup_gate = DeduplicationGate(window_s=10.0)
        # PHOENIX v18: MarginTracker — in-flight reservation prevents concurrent signals
        # from all passing guardian with same stale balance snapshot (BUG-19/29 fix).
        from src.execution.margin_tracker import MarginTracker
        self._margin_tracker = MarginTracker()

    def _get_lock(self, exchange_id: str) -> asyncio.Lock:
        return self._locks.setdefault(exchange_id, asyncio.Lock())

    def is_locked(self, exchange_id: str) -> bool:
        """Return True if capital lock is currently held for this exchange."""
        lock = self._locks.get(exchange_id)
        return lock is not None and lock.locked()

    async def _acquire_lock(self, exchange_id: str) -> None:
        """Acquire the asyncio.Lock for mutual exclusion on this exchange."""
        lock = self._get_lock(exchange_id)
        await lock.acquire()

    def _release_lock(self, exchange_id: str) -> None:
        """Release the asyncio.Lock for this exchange."""
        lock = self._locks.get(exchange_id)
        if lock is not None and lock.locked():
            lock.release()

    def _check_halt(self) -> bool:
        """Return True if engine is halted."""
        return is_halted()

    def _check_health(self, exchange_id: str) -> bool:
        """Return True if exchange health_score >= threshold.

        BUG-48: minimum score for idle-but-healthy REST-only adapter is exactly 0.6
        (latency=1.0 + ws=1.0 + fill=1.0 with stale connection). Using >= so that
        a perfectly-healthy adapter that hasn't made REST calls recently still passes.
        """
        adapter = self._exchanges.get(exchange_id)
        if adapter is None:
            return False
        return adapter.health_score >= self._config.health_threshold

    async def _place_with_timeout(self, adapter: ExchangeAdapter, order: Order) -> Trade:
        """Place order with timeout. Raises asyncio.TimeoutError on timeout."""
        # Bug 13-B (PHOENIX §8.2): output configured timeout for live latency forensics
        logger.debug("executor_timeout_ms_config timeout_ms=%s", self._config.timeout_ms)
        timeout_s = self._config.timeout_ms / 1000.0
        return await asyncio.wait_for(adapter.place_order(order), timeout=timeout_s)

    async def _rollback_order(
        self, exchange_id: str, order: Order, order_id: str | None = None,
        filled: bool = False, filled_amount: Decimal | None = None,
    ) -> tuple[bool, str]:
        """
        Attempt to cancel/close an order as part of rollback.

        For unfilled/partially-filled orders: cancel via cancel_order().
        For filled orders (filled=True): place an opposing market order to unwind.
        When filled_amount is provided, unwinds only the actual filled quantity
        (not the original order amount) to avoid over-unwinding on partial fills.
        Returns True if rollback succeeded.
        """
        # Bug 13-D (PHOENIX §8.2): rollback counter for live forensics
        logger.info(
            "rollback_triggered exchange=%s symbol=%s side=%s filled=%s amount=%s",
            exchange_id, order.symbol, order.side, filled, filled_amount,
        )
        # PHOENIX v18: 중복 rollback 방지
        # BUG-38: only dedup on success — failed attempts must allow filled=True retry
        if order.order_id and self._rollback_attempted.get(order.order_id) == "success":
            logger.info(
                "rollback_dedup_skipped order_id=%s prev=success",
                order.order_id,
            )
            return True, "dedup_skipped"
        adapter = self._exchanges.get(exchange_id)
        if adapter is None:
            return False, "no adapter"

        try:
            if filled:
                # Filled orders can't be cancelled — place opposing market order
                opposite_side = OrderSide.SELL if order.side == OrderSide.BUY else OrderSide.BUY
                unwind_qty = filled_amount if filled_amount is not None else order.amount
                unwind_order = Order(
                    order_id=f"unwind-{order.order_id}",
                    exchange_id=exchange_id,
                    symbol=order.symbol,
                    side=opposite_side,
                    order_type=OrderType.MARKET,
                    price=None,
                    amount=unwind_qty,
                    # Bug 25b: tell futures adapters (Bitget, Binance, etc.) this is a
                    # position-close, not a new open.  Without reduceOnly, Bitget hedge
                    # mode sends tradeSide="open" and gets error 40762 (balance exceeded).
                    metadata={"reduceOnly": True},
                )
                logger.info(
                    "rollback_unwind exchange=%s side=%s amount=%s symbol=%s",
                    exchange_id, opposite_side, order.amount, order.symbol,
                )
                await self._place_with_timeout(adapter, unwind_order)
            else:
                effective_id = order_id or order.order_id
                if effective_id:
                    # Pass symbol for native adapters that require it (Binance)
                    try:
                        await adapter.cancel_order(effective_id, symbol=order.symbol)
                    except TypeError:
                        # Fallback for adapters that don't accept symbol kwarg
                        await adapter.cancel_order(effective_id)
                # else: order was never submitted — nothing to cancel
            if order.order_id:
                # BUG-03: prune to prevent unbounded growth (cap at 5000 entries).
                # Evict oldest 1000 entries instead of .clear() to avoid nuking
                # dedup state for concurrent in-flight rollbacks (BUG-03 MAJOR fix).
                if len(self._rollback_attempted) >= 5000:
                    for _k in list(self._rollback_attempted.keys())[:1000]:
                        del self._rollback_attempted[_k]
                self._rollback_attempted[order.order_id] = "success"
            return True, ""
        except Exception as exc:
            logger.error("rollback_failed exchange=%s error=%s", exchange_id, exc)
            if order.order_id:
                if len(self._rollback_attempted) >= 5000:
                    for _k in list(self._rollback_attempted.keys())[:1000]:
                        del self._rollback_attempted[_k]
                self._rollback_attempted[order.order_id] = "failed"
            return False, str(exc)

    # -----------------------------------------------------------------------
    # SAME-EXCHANGE ATOMIC EXECUTION
    # -----------------------------------------------------------------------

    async def execute_same_exchange(
        self,
        exchange_id: str,
        leg1_order: Order,
        leg2_order: Order,
        strategy_id: str,
    ) -> ExecutionResult:
        """
        Submit both legs in parallel on the same exchange (asyncio.gather).

        Amendment 5 same-exchange race conditions handled:
        - RC-SAME-1: Halt flag before execution
        - RC-SAME-2: Exchange health degraded
        - RC-SAME-3: Timeout on either leg → cancel + rollback
        - RC-SAME-4: Partial fill ≤80% → cancel + rollback
        - RC-SAME-5: Partial fill >80% → accept
        - RC-SAME-6 through RC-SAME-11: Exception paths → rollback
        """
        # RC-SAME-1: Halt check
        if self._check_halt():
            logger.warning("same_exchange_rejected_halted strategy=%s", strategy_id)
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                legs=[],
                error="Engine halted",
                strategy_id=strategy_id,
            )

        # RC-SAME-1b: DeduplicationGate — Bug 26 fix
        # BUG-32: differentiate entry vs exit so close orders aren't blocked by recent entry
        _is_close = any(
            o.metadata.get("reduceOnly") for o in [leg1_order, leg2_order]
        )
        _dedup_key = f"{strategy_id}:{leg1_order.symbol}:{'close' if _is_close else 'open'}"
        if not await self._dedup_gate.check_and_register(_dedup_key):
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                legs=[],
                error="dedup_gate_blocked",
                strategy_id=strategy_id,
            )

        # RC-SAME-2: Health check
        if not self._check_health(exchange_id):
            logger.warning(
                "same_exchange_rejected_health exchange=%s strategy=%s",
                exchange_id, strategy_id
            )
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                legs=[],
                error=f"Exchange {exchange_id} health below threshold",
                strategy_id=strategy_id,
            )

        adapter = self._exchanges[exchange_id]
        # Bug 13-A (PHOENIX §8.2): per-strategy latency measurement
        _t0 = asyncio.get_event_loop().time()
        await self._acquire_lock(exchange_id)

        try:
            # Submit both legs in parallel
            results = await asyncio.gather(
                self._place_with_timeout(adapter, leg1_order),
                self._place_with_timeout(adapter, leg2_order),
                return_exceptions=True,
            )

            leg1_trade = results[0] if not isinstance(results[0], BaseException) else None
            leg2_trade = results[1] if not isinstance(results[1], BaseException) else None
            leg1_err = results[0] if isinstance(results[0], BaseException) else None
            leg2_err = results[1] if isinstance(results[1], BaseException) else None

            leg1_result = LegResult(
                order=leg1_order, trade=leg1_trade,
                error=str(leg1_err) if leg1_err else None,
                expected_price=leg1_order.price,
                fill_price=leg1_trade.price if leg1_trade else None,
            )
            leg2_result = LegResult(
                order=leg2_order, trade=leg2_trade,
                error=str(leg2_err) if leg2_err else None,
                expected_price=leg2_order.price,
                fill_price=leg2_trade.price if leg2_trade else None,
            )

            # Check for failures
            has_failure = leg1_err is not None or leg2_err is not None

            # Check partial fill thresholds
            leg1_ratio = leg1_result.fill_ratio(leg1_order.amount)
            leg2_ratio = leg2_result.fill_ratio(leg2_order.amount)
            partial_below = (
                (leg1_trade is not None and leg1_ratio <= self._config.partial_fill_threshold) or
                (leg2_trade is not None and leg2_ratio <= self._config.partial_fill_threshold)
            )

            if has_failure or partial_below:
                # Rollback: cancel unfilled, unwind filled
                leg1_filled = leg1_trade is not None and leg1_result.filled_amount > 0
                leg2_filled = leg2_trade is not None and leg2_result.filled_amount > 0
                # BUG-85: errored legs (leg1_err/leg2_err is not None) must also attempt cancel —
                # the order may have reached the exchange before the timeout/error response arrived.
                # A cancel on a non-existent order is harmless (benign -2011 from Binance).
                # Only skip rollback when we know the leg was never submitted (neither trade nor error).
                _rb1_needed = leg1_trade is not None or leg1_err is not None
                _rb2_needed = leg2_trade is not None or leg2_err is not None
                rb1, rb1_reason = (await self._rollback_order(exchange_id, leg1_order, filled=leg1_filled, filled_amount=leg1_result.filled_amount) if _rb1_needed else (True, ""))
                rb2, rb2_reason = (await self._rollback_order(exchange_id, leg2_order, filled=leg2_filled, filled_amount=leg2_result.filled_amount) if _rb2_needed else (True, ""))

                if not rb1 or not rb2:
                    # Register ALL failed legs (not just one) to correctly account for
                    # stranded exposure when both rollbacks fail simultaneously.
                    should_halt = False
                    reason = rb1_reason if not rb1 else rb2_reason
                    for _rb_ok, _rb_reason, _fo in (
                        (rb1, rb1_reason, leg1_order),
                        (rb2, rb2_reason, leg2_order),
                    ):
                        if not _rb_ok:
                            _sh = self._stranded_tracker.register(
                                exchange_id=exchange_id,
                                symbol=_fo.symbol,
                                side=str(_fo.side),
                                size=float(_fo.amount),
                                value_usd=float(_fo.amount * (_fo.price or Decimal("0"))),
                                reason=_rb_reason,
                            )
                            should_halt = should_halt or _sh
                    if should_halt:
                        halt_local()
                    logger.critical(
                        "same_exchange_rollback_failed exchange=%s strategy=%s",
                        exchange_id, strategy_id
                    )
                    return ExecutionResult(
                        status=ExecutionStatus.ROLLBACK_FAILED,
                        legs=[leg1_result, leg2_result],
                        error="Rollback failed — engine halted" if should_halt else "Rollback failed — stranded alert",
                        strategy_id=strategy_id,
                    )

                return ExecutionResult(
                    status=ExecutionStatus.ROLLED_BACK,
                    legs=[leg1_result, leg2_result],
                    strategy_id=strategy_id,
                )

            # Bug 13-A: same-exchange = 2-leg parallel
            _elapsed_ms = (asyncio.get_event_loop().time() - _t0) * 1000
            # Tier1 patch 3-1: async log (non-critical INFO off hot path)
            _async_log_info(
                "latency_measured strategy=%s legs=2 mode=same_exchange elapsed_ms=%.1f",
                strategy_id, _elapsed_ms,
            )
            return ExecutionResult(
                status=ExecutionStatus.SUCCESS,
                legs=[leg1_result, leg2_result],
                strategy_id=strategy_id,
            )

        finally:
            self._release_lock(exchange_id)
            # BUG-93: do NOT clear — concurrent executions share this dict;
            # clearing one call's entries corrupts another in-flight execution.
            # order_id uniqueness guarantees no false dedup across executions.

    # -----------------------------------------------------------------------
    # MULTI-LEG SAME-EXCHANGE EXECUTION (sequential, N legs)
    # -----------------------------------------------------------------------

    async def execute_multi_leg(
        self,
        exchange_id: str,
        orders: list[Order],
        strategy_id: str,
    ) -> ExecutionResult:
        """
        Sequential N-leg execution on a single exchange (e.g., triangular arbitrage).

        Each order is placed in sequence. On any failure or partial fill below
        threshold, all previously completed legs are rolled back in reverse order.
        """
        if self._check_halt():
            logger.warning("multi_leg_rejected_halted strategy=%s", strategy_id)
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                legs=[],
                error="Engine halted",
                strategy_id=strategy_id,
            )

        if not self._check_health(exchange_id):
            logger.warning(
                "multi_leg_rejected_health exchange=%s strategy=%s",
                exchange_id, strategy_id,
            )
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                legs=[],
                error=f"Exchange {exchange_id} health below threshold",
                strategy_id=strategy_id,
            )

        adapter = self._exchanges[exchange_id]
        # Bug 13-A (PHOENIX §8.2): per-strategy latency measurement
        _t0 = asyncio.get_event_loop().time()
        await self._acquire_lock(exchange_id)

        completed: list[LegResult] = []

        try:
            for i, order in enumerate(orders):
                error_msg: str | None = None
                trade: Trade | None = None
                try:
                    trade = await self._place_with_timeout(adapter, order)
                except asyncio.TimeoutError:
                    error_msg = "timeout"
                    logger.error(
                        "multi_leg_timeout leg=%d exchange=%s strategy=%s",
                        i, exchange_id, strategy_id,
                    )
                except Exception as exc:
                    error_msg = str(exc)
                    logger.error(
                        "multi_leg_failed leg=%d exchange=%s error=%s strategy=%s",
                        i, exchange_id, exc, strategy_id,
                    )

                if error_msg is not None:
                    # Rollback completed[K-1..0] in reverse order
                    all_rb_ok = True
                    last_rb_reason = ""
                    for prev_leg in reversed(completed):
                        rb_ok_val, rb_reason = await self._rollback_order(
                            exchange_id, prev_leg.order,
                            filled=True, filled_amount=prev_leg.filled_amount,
                        )
                        if not rb_ok_val:
                            all_rb_ok = False
                            last_rb_reason = rb_reason
                    if not all_rb_ok:
                        failed_leg = completed[-1] if completed else None
                        should_halt = self._stranded_tracker.register(
                            exchange_id=exchange_id,
                            symbol=failed_leg.order.symbol if failed_leg else "unknown",
                            side=str(failed_leg.order.side) if failed_leg else "unknown",
                            size=float(failed_leg.order.amount) if failed_leg else 0.0,
                            value_usd=float(failed_leg.order.amount * (failed_leg.order.price or Decimal("0"))) if failed_leg else 0.0,
                            reason=last_rb_reason,
                        )
                        if should_halt:
                            halt_local()
                        logger.critical(
                            "multi_leg_rollback_failed HALT_SET exchange=%s strategy=%s",
                            exchange_id, strategy_id,
                        )
                        return ExecutionResult(
                            status=ExecutionStatus.ROLLBACK_FAILED,
                            legs=completed,
                            error=f"Rollback failed — {'halted' if should_halt else 'stranded alert'}. Leg {i}: {error_msg}",
                            strategy_id=strategy_id,
                        )
                    return ExecutionResult(
                        status=ExecutionStatus.ROLLED_BACK,
                        legs=completed,
                        error=f"Leg {i} failed: {error_msg}",
                        strategy_id=strategy_id,
                    )

                leg_result = LegResult(
                    order=order, trade=trade,
                    expected_price=order.price,
                    fill_price=trade.price if trade else None,
                )
                completed.append(leg_result)

                fill_ratio = leg_result.fill_ratio(order.amount)
                if fill_ratio <= self._config.partial_fill_threshold:
                    logger.warning(
                        "multi_leg_partial_below_threshold leg=%d ratio=%s strategy=%s",
                        i, fill_ratio, strategy_id,
                    )
                    all_rb_ok = True
                    last_rb_reason = ""
                    for prev_leg in reversed(completed):
                        rb_ok_val, rb_reason = await self._rollback_order(
                            exchange_id, prev_leg.order,
                            filled=True, filled_amount=prev_leg.filled_amount,
                        )
                        if not rb_ok_val:
                            all_rb_ok = False
                            last_rb_reason = rb_reason
                    if not all_rb_ok:
                        failed_leg = completed[-1] if completed else None
                        should_halt = self._stranded_tracker.register(
                            exchange_id=exchange_id,
                            symbol=failed_leg.order.symbol if failed_leg else "unknown",
                            side=str(failed_leg.order.side) if failed_leg else "unknown",
                            size=float(failed_leg.order.amount) if failed_leg else 0.0,
                            value_usd=float(failed_leg.order.amount * (failed_leg.order.price or Decimal("0"))) if failed_leg else 0.0,
                            reason=last_rb_reason,
                        )
                        if should_halt:
                            halt_local()
                        logger.critical(
                            "multi_leg_partial_rollback_failed HALT_SET exchange=%s strategy=%s",
                            exchange_id, strategy_id,
                        )
                        return ExecutionResult(
                            status=ExecutionStatus.ROLLBACK_FAILED,
                            legs=completed,
                            error=f"Rollback failed — {'halted' if should_halt else 'stranded alert'}. Leg {i} partial fill {fill_ratio:.2%}",
                            strategy_id=strategy_id,
                        )
                    return ExecutionResult(
                        status=ExecutionStatus.ROLLED_BACK,
                        legs=completed,
                        error=f"Leg {i} partial fill {fill_ratio:.2%} below threshold",
                        strategy_id=strategy_id,
                    )

            # Bug 13-A: multi-leg same-exchange = N-leg sequential
            _elapsed_ms = (asyncio.get_event_loop().time() - _t0) * 1000
            # Tier1 patch 3-1: async logs off hot path
            _async_log_info(
                "multi_leg_success legs=%d strategy=%s",
                len(completed), strategy_id,
            )
            _async_log_info(
                "latency_measured strategy=%s legs=%d mode=multi_leg elapsed_ms=%.1f",
                strategy_id, len(completed), _elapsed_ms,
            )
            return ExecutionResult(
                status=ExecutionStatus.SUCCESS,
                legs=completed,
                strategy_id=strategy_id,
            )

        finally:
            self._release_lock(exchange_id)

    # -----------------------------------------------------------------------
    # CROSS-EXCHANGE ATOMIC EXECUTION (Amendment 4, 14-step protocol)
    # -----------------------------------------------------------------------

    async def execute_cross_exchange(
        self,
        leg1_order: Order,
        leg2_order: Order,
        strategy_id: str,
        min_edge: Decimal,
    ) -> ExecutionResult:
        """
        Cross-exchange sequential atomic execution per Amendment 4.

        PHASE PRE-VALIDATION (steps 1-7)
        PHASE SEQUENTIAL SUBMISSION (steps 8-11)
        PHASE ROLLBACK (step 12)
        PHASE RECONCILIATION (steps 13-14)
        """
        ex_a_id = leg1_order.exchange_id
        ex_b_id = leg2_order.exchange_id
        adapter_a = self._exchanges.get(ex_a_id)
        adapter_b = self._exchanges.get(ex_b_id)

        if adapter_a is None or adapter_b is None:
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                legs=[],
                error=f"Unknown exchange(s): {ex_a_id}, {ex_b_id}",
                strategy_id=strategy_id,
            )

        # ── PHASE PRE-VALIDATION ─────────────────────────────────────────

        # Step 0 (implicit): Halt check — RC-CROSS-1
        if self._check_halt():
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                legs=[],
                error="Engine halted",
                strategy_id=strategy_id,
            )

        # Step 0b: DeduplicationGate — Bug 26 fix (race-condition duplicate orders)
        # BUG-32: differentiate entry vs exit so close orders aren't blocked by recent entry
        _is_close = any(
            o.metadata.get("reduceOnly") for o in [leg1_order, leg2_order]
        )
        _dedup_key = f"{strategy_id}:{leg1_order.symbol}:{'close' if _is_close else 'open'}"
        if not await self._dedup_gate.check_and_register(_dedup_key):
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                legs=[],
                error="dedup_gate_blocked",
                strategy_id=strategy_id,
            )

        # Step 1: Verify BOTH exchanges health_score >= 0.6 — RC-CROSS-2
        # CRITICAL: health checks must run BEFORE margin reservation so rejection
        # paths don't leak in-flight reservations (no try/finally coverage yet).
        if not self._check_health(ex_a_id):
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                legs=[],
                error=f"Exchange {ex_a_id} health below threshold",
                strategy_id=strategy_id,
            )
        if not self._check_health(ex_b_id):
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                legs=[],
                error=f"Exchange {ex_b_id} health below threshold",
                strategy_id=strategy_id,
            )

        # Step 0c: MarginTracker — in-flight reservation (BUG-19/29 fix)
        # Placed AFTER health checks: any reject path before try/finally must run
        # before reservation to avoid leaking in-flight margin.
        # available_usd uses configured per-exchange budget; falls back to $100k for
        # low-capital operation; tighten to actual balance for production protection.
        # BUG-75: Exit trades (reduceOnly) free margin rather than consuming it.
        # Skipping reservation for exits prevents thrashing where exits can never
        # execute because in-flight entry margin blocks them.
        _required_a = leg1_order.price * leg1_order.amount if leg1_order.price and leg1_order.amount else Decimal("0")
        _required_b = leg2_order.price * leg2_order.amount if leg2_order.price and leg2_order.amount else Decimal("0")
        _budget_per_ex = Decimal(str(getattr(self._config, "per_exchange_budget_usd", 100_000)))
        _margin_reserved_a = False
        _margin_reserved_b = False
        if not _is_close:
            _margin_ok_a = await self._margin_tracker.check_and_reserve(ex_a_id, _required_a, _budget_per_ex)
            if _margin_ok_a:
                _margin_reserved_a = True
                _margin_ok_b = await self._margin_tracker.check_and_reserve(ex_b_id, _required_b, _budget_per_ex)
                if _margin_ok_b:
                    _margin_reserved_b = True
            else:
                _margin_ok_b = False
            if not _margin_ok_a or not _margin_ok_b:
                # Release already-reserved leg; clear flag to prevent double-release in finally
                if _margin_reserved_a:
                    await self._margin_tracker.release(ex_a_id, _required_a)
                    _margin_reserved_a = False
                return ExecutionResult(
                    status=ExecutionStatus.REJECTED,
                    legs=[],
                    error="margin_tracker_blocked",
                    strategy_id=strategy_id,
                )

        # Step 2-3: Balance/margin checks — skipped in unit layer (handled by guardian)
        # Step 4: Re-read orderbooks REMOVED — adds ~600ms REST latency.
        # Strategy layer (SignalGenerator + CEXOrderbookSlippage) pre-validates spread.
        # ob_a, ob_b variables not used downstream, so removal is safe.

        # Step 5: max_rollback_cost check (Amendment 3C) — delegated to guardian
        # Step 6-7: Acquire execution locks on BOTH exchanges (sorted to prevent deadlock)
        # Bug 13-A (PHOENIX §8.2): cross-exchange latency measurement starts here
        _t0 = asyncio.get_event_loop().time()
        first_id, second_id = sorted([ex_a_id, ex_b_id])
        # Tier1 patch (PHOENIX §8.3): parallel lock acquire via asyncio.gather
        # sorted() preserved to prevent deadlocks (lock acquire order is canonical)
        # Margin safety: wrap lock acquisition — if CancelledError/BaseException fires here
        # (outside the try/finally block), we must release reserved margin before propagating.
        try:
            await asyncio.gather(
                self._acquire_lock(first_id),
                self._acquire_lock(second_id),
            )
        except BaseException:
            if _margin_reserved_a:
                await self._margin_tracker.release(ex_a_id, _required_a)
            if _margin_reserved_b:
                await self._margin_tracker.release(ex_b_id, _required_b)
            raise

        # BUG-71: Sync lot sizes across exchanges BEFORE placing orders.
        # Each exchange independently floors qty to its own step size.
        # If Binance stepSize=1.0 and Bitget stepSize=0.001, a 2.038 size
        # becomes 2.0 on Binance and 2.038 on Bitget → $0.20 net-long mismatch.
        # Fix: compute the coarser (larger) step and floor BOTH legs to it.
        try:
            _step_a = await adapter_a.get_lot_step(leg1_order.symbol)
            _step_b = await adapter_b.get_lot_step(leg2_order.symbol)
            _coarser = max(_step_a, _step_b)
            if _coarser > Decimal("0"):
                _synced = (leg1_order.amount // _coarser) * _coarser
                if _synced <= Decimal("0"):
                    if _margin_reserved_a:
                        await self._margin_tracker.release(ex_a_id, _required_a)
                    if _margin_reserved_b:
                        await self._margin_tracker.release(ex_b_id, _required_b)
                    self._release_lock(first_id)
                    self._release_lock(second_id)
                    return ExecutionResult(
                        status=ExecutionStatus.REJECTED,
                        legs=[],
                        error=f"lot_size_sync_zero step={_coarser} amount={leg1_order.amount}",
                        strategy_id=strategy_id,
                    )
                if _synced != leg1_order.amount:
                    logger.info(
                        "lot_size_synced symbol=%s step_a=%s step_b=%s original=%s synced=%s",
                        leg1_order.symbol, _step_a, _step_b, leg1_order.amount, _synced,
                    )
                    leg1_order = leg1_order.model_copy(update={"amount": _synced})
                    leg2_order = leg2_order.model_copy(update={"amount": _synced})
                # BUG-71 Major #2: Reject if synced notional < exchange MIN_NOTIONAL.
                # Each adapter independently bumps sub-notional qty using its own step size,
                # desynchronizing the legs again. Reject here to prevent the desync.
                from src.core.config_loader import get_config as _gc
                _min_notional = Decimal(str(_gc("execution.min_trade_notional_usd") or 5))
                _synced_notional = _synced * (leg1_order.price or Decimal("0"))
                if Decimal("0") < _synced_notional < _min_notional:
                    logger.warning(
                        "lot_size_sync_sub_notional symbol=%s synced=%s notional=%.4f min=%.2f — rejecting",
                        leg1_order.symbol, _synced, float(_synced_notional), float(_min_notional),
                    )
                    if _margin_reserved_a:
                        await self._margin_tracker.release(ex_a_id, _required_a)
                    if _margin_reserved_b:
                        await self._margin_tracker.release(ex_b_id, _required_b)
                    self._release_lock(first_id)
                    self._release_lock(second_id)
                    return ExecutionResult(
                        status=ExecutionStatus.REJECTED,
                        legs=[],
                        error=f"lot_size_sync_sub_notional synced={_synced} notional={_synced_notional:.4f}",
                        strategy_id=strategy_id,
                    )
        except Exception as _lsync_exc:
            # BUG-71 Major #1: Reject trade when lot_size sync fails.
            # Proceeding with original unsynchronized sizes causes unhedged exposure.
            logger.warning(
                "lot_size_sync_failed symbol=%s err=%s — rejecting trade to prevent unhedged exposure",
                leg1_order.symbol, _lsync_exc,
            )
            if _margin_reserved_a:
                await self._margin_tracker.release(ex_a_id, _required_a)
            if _margin_reserved_b:
                await self._margin_tracker.release(ex_b_id, _required_b)
            self._release_lock(first_id)
            self._release_lock(second_id)
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                legs=[],
                error=f"lot_size_sync_failed: {_lsync_exc}",
                strategy_id=strategy_id,
            )

        leg1_result: LegResult | None = None
        leg2_result: LegResult | None = None
        leg1_trade: Trade | None = None

        try:
            # ── PHASE SEQUENTIAL SUBMISSION ──────────────────────────────

            # Step 8: Submit Leg 1 on Exchange A
            try:
                leg1_trade = await self._place_with_timeout(adapter_a, leg1_order)
                leg1_result = LegResult(
                    order=leg1_order, trade=leg1_trade,
                    expected_price=leg1_order.price,
                    fill_price=leg1_trade.price if leg1_trade else None,
                )
            except asyncio.TimeoutError:
                logger.error("leg1_timeout exchange=%s strategy=%s", ex_a_id, strategy_id)
                # BUG-37: try cancel first; check return value and alert if it fails.
                # BUG-39: for exit (reduceOnly) orders, cancel failure means the close
                # WAS filled — do NOT place opposite-direction unwind (that re-opens).
                _is_exit_leg = bool(leg1_order.metadata.get("reduceOnly"))
                rb_ok, rb_reason = await self._rollback_order(ex_a_id, leg1_order)
                if not rb_ok:
                    if _is_exit_leg:
                        # Cancel failed → exit order likely already filled → position closed.
                        logger.info(
                            "leg1_timeout_exit_cancel_failed_assuming_filled exchange=%s symbol=%s "
                            "— exit likely succeeded, no unwind",
                            ex_a_id, leg1_order.symbol,
                        )
                    else:
                        # Entry order: cancel failed → order may have filled before timeout.
                        # BUG-38 fix allows this retry with filled=True.
                        rb_ok2, rb_reason2 = await self._rollback_order(
                            ex_a_id, leg1_order, filled=True
                        )
                        if not rb_ok2:
                            should_halt = self._stranded_tracker.register(
                                exchange_id=ex_a_id,
                                symbol=leg1_order.symbol,
                                side=str(leg1_order.side),
                                size=float(leg1_order.amount),
                                value_usd=float(leg1_order.amount * (leg1_order.price or Decimal("0"))),
                                reason=f"leg1_timeout_rollback_failed:{rb_reason2}",
                            )
                            if should_halt:
                                halt_local()
                            logger.critical(
                                "leg1_timeout_rollback_failed HALT_SET=%s exchange=%s strategy=%s",
                                should_halt, ex_a_id, strategy_id,
                            )
                return ExecutionResult(
                    status=ExecutionStatus.ROLLED_BACK,
                    legs=[LegResult(order=leg1_order, error="timeout")],
                    error="Leg 1 timeout",
                    strategy_id=strategy_id,
                )
            except Exception as exc:
                logger.error("leg1_failed exchange=%s error=%s strategy=%s", ex_a_id, exc, strategy_id)
                return ExecutionResult(
                    status=ExecutionStatus.ROLLED_BACK,
                    legs=[LegResult(order=leg1_order, error=str(exc))],
                    error=f"Leg 1 failed: {exc}",
                    strategy_id=strategy_id,
                )

            # Step 9: Evaluate Leg 1 fill
            leg1_ratio = leg1_result.fill_ratio(leg1_order.amount)

            if leg1_ratio <= self._config.partial_fill_threshold:
                # Partial ≤80% or zero fill → unwind if filled, cancel if not
                logger.warning(
                    "leg1_partial_below_threshold ratio=%s strategy=%s",
                    leg1_ratio, strategy_id
                )
                leg1_filled = leg1_result.trade is not None and leg1_result.filled_amount > 0
                rb_ok, rb_reason = await self._rollback_order(ex_a_id, leg1_order, filled=leg1_filled, filled_amount=leg1_result.filled_amount)
                if not rb_ok:
                    should_halt = self._stranded_tracker.register(
                        exchange_id=ex_a_id,
                        symbol=leg1_order.symbol,
                        side=str(leg1_order.side),
                        size=float(leg1_order.amount),
                        value_usd=float(leg1_order.amount * (leg1_order.price or Decimal("0"))),
                        reason=rb_reason,
                    )
                    if should_halt:
                        halt_local()
                    logger.critical(
                        "leg1_partial_rollback_failed HALT_SET exchange=%s strategy=%s",
                        ex_a_id, strategy_id
                    )
                    return ExecutionResult(
                        status=ExecutionStatus.ROLLBACK_FAILED,
                        legs=[leg1_result],
                        error=f"Leg 1 partial rollback failed — {'engine halted' if should_halt else 'stranded alert'}",
                        strategy_id=strategy_id,
                    )
                return ExecutionResult(
                    status=ExecutionStatus.ROLLED_BACK,
                    legs=[leg1_result],
                    error=f"Leg 1 partial fill {leg1_ratio:.2%} below threshold",
                    strategy_id=strategy_id,
                )

            # Partial >80%: adjust leg2 size to match leg1 actual fill
            adjusted_leg2 = leg2_order
            if leg1_ratio < Decimal("1.0"):
                adjusted_leg2 = leg2_order.model_copy(
                    update={"amount": leg1_result.filled_amount}
                )
                logger.info(
                    "leg2_adjusted_for_partial leg1_ratio=%s new_amount=%s",
                    leg1_ratio, adjusted_leg2.amount
                )

            # Step 10: Submit Leg 2 on Exchange B
            try:
                leg2_trade = await self._place_with_timeout(adapter_b, adjusted_leg2)
                leg2_result = LegResult(
                    order=adjusted_leg2, trade=leg2_trade,
                    expected_price=adjusted_leg2.price,
                    fill_price=leg2_trade.price if leg2_trade else None,
                )
            except asyncio.TimeoutError:
                logger.error("leg2_timeout exchange=%s strategy=%s", ex_b_id, strategy_id)
                return await self._do_rollback_cross(
                    ex_a_id, leg1_order, leg1_result,
                    LegResult(order=adjusted_leg2, error="timeout"),
                    strategy_id, "Leg 2 timeout"
                )
            except Exception as exc:
                logger.error("leg2_failed exchange=%s error=%s strategy=%s", ex_b_id, exc, strategy_id)
                return await self._do_rollback_cross(
                    ex_a_id, leg1_order, leg1_result,
                    LegResult(order=adjusted_leg2, error=str(exc)),
                    strategy_id, f"Leg 2 failed: {exc}"
                )

            # Step 11: Evaluate Leg 2 fill
            leg2_ratio = leg2_result.fill_ratio(adjusted_leg2.amount)
            if leg2_ratio <= self._config.partial_fill_threshold:
                return await self._do_rollback_cross(
                    ex_a_id, leg1_order, leg1_result,
                    leg2_result, strategy_id,
                    f"Leg 2 partial fill {leg2_ratio:.2%} below threshold"
                )

            # ── PHASE RECONCILIATION (step 13 — async, non-blocking) ──────
            reconcile_task = asyncio.ensure_future(
                self._post_execution_reconcile(
                    ex_a_id, ex_b_id, strategy_id,
                    leg1_result=leg1_result,
                    leg2_result=leg2_result,
                    delay_s=self._config.post_reconcile_delay_s,
                )
            )
            reconcile_task.add_done_callback(self._reconcile_done_callback)

            # Bug 13-A: cross-exchange = 2-leg sequential
            _elapsed_ms = (asyncio.get_event_loop().time() - _t0) * 1000
            # Tier1 patch 3-1: async logs off hot path
            _async_log_info(
                "cross_exchange_success leg1=%s leg2=%s strategy=%s",
                leg1_result.filled_amount, leg2_result.filled_amount, strategy_id,
            )
            _async_log_info(
                "latency_measured strategy=%s legs=2 mode=cross_exchange elapsed_ms=%.1f",
                strategy_id, _elapsed_ms,
            )
            return ExecutionResult(
                status=ExecutionStatus.SUCCESS,
                legs=[leg1_result, leg2_result],
                strategy_id=strategy_id,
            )

        finally:
            self._release_lock(ex_a_id)
            self._release_lock(ex_b_id)
            # Release margin reservations — explicit release on success/rollback paths.
            # TTL (60s) remains as safety net for unhandled paths.
            if _margin_reserved_a:
                await self._margin_tracker.release(ex_a_id, _required_a)
            if _margin_reserved_b:
                await self._margin_tracker.release(ex_b_id, _required_b)
            # BUG-93: do NOT clear (see execute_same_exchange finally block comment)

    async def _do_rollback_cross(
        self,
        ex_a_id: str,
        leg1_order: Order,
        leg1_result: LegResult,
        leg2_result: LegResult,
        strategy_id: str,
        reason: str,
    ) -> ExecutionResult:
        """
        Amendment 4 Step 12: Rollback both legs on cross-exchange failure.

        - Leg 1 on Exchange A: cancel or unwind filled amount.
        - Leg 2 on Exchange B: unwind if partially filled (F-5 fix).
        If rollback fails → HALT flag + stranded position alert.
        """
        logger.warning(
            "cross_exchange_rollback reason=%s strategy=%s",
            reason, strategy_id
        )

        # F-5 fix: Unwind leg2 partial fill on Exchange B BEFORE unwinding leg1.
        # Executing leg2 unwind first reduces directional exposure faster.
        _leg2_halt = False  # deferred halt signal from leg2 rollback failure
        leg2_filled = leg2_result.trade is not None and leg2_result.filled_amount > 0
        if leg2_filled:
            ex_b_id = leg2_result.order.exchange_id
            leg2_trade_order_id = leg2_result.trade.order_id if leg2_result.trade else None
            rb2_ok, rb2_reason = await self._rollback_order(
                ex_b_id, leg2_result.order, order_id=leg2_trade_order_id,
                filled=True, filled_amount=leg2_result.filled_amount,
            )
            if not rb2_ok:
                logger.error(
                    "cross_exchange_leg2_rollback_failed exchange=%s strategy=%s reason=%s",
                    ex_b_id, strategy_id, rb2_reason
                )
                _leg2_halt = self._stranded_tracker.register(
                    exchange_id=ex_b_id,
                    symbol=leg2_result.order.symbol,
                    side=str(leg2_result.order.side),
                    size=float(leg2_result.filled_amount),
                    value_usd=float(leg2_result.filled_amount * (leg2_result.order.price or Decimal("0"))),
                    reason=f"leg2_partial_fill_rollback_failed:{rb2_reason}",
                )
                if _leg2_halt:
                    # Halt threshold reached on leg2 failure.
                    # Do NOT halt immediately — allow leg1 corrective unwind to proceed.
                    # The halt will fire from the leg1 path below if leg1 also fails.
                    logger.warning(
                        "cross_exchange_stranded_leg2_halt_threshold_reached exchange=%s symbol=%s "
                        "— deferring halt to allow leg1 corrective unwind",
                        ex_b_id, leg2_result.order.symbol,
                    )

        # BUG-42: exit leg1 filled + leg2 failed → do NOT unwind leg1 (already closed).
        # The stranded position is the UNCLOSED side on ex_b (leg2), not ex_a.
        _leg1_is_exit = bool(leg1_order.metadata.get("reduceOnly"))
        leg1_filled = leg1_result.trade is not None and leg1_result.filled_amount > 0
        if _leg1_is_exit and leg1_filled:
            ex_b_id_strd = leg2_result.order.exchange_id if leg2_result.order else ex_b_id
            should_halt = self._stranded_tracker.register(
                exchange_id=ex_b_id_strd,
                symbol=leg1_order.symbol,
                side=str(leg2_result.order.side) if leg2_result.order else "unknown",
                size=float(leg1_result.filled_amount),
                value_usd=float(leg1_result.filled_amount * (leg1_order.price or Decimal("0"))),
                reason=f"exit_leg1_filled_leg2_failed_short_stranded:{reason}",
            )
            if should_halt:
                halt_local()
            logger.critical(
                "do_rollback_cross: exit_leg1_filled_leg2_failed "
                "short_stranded exchange=%s symbol=%s HALT_SET=%s reason=%s",
                ex_b_id_strd, leg1_order.symbol, should_halt, reason,
            )
            return ExecutionResult(
                status=ExecutionStatus.ROLLBACK_FAILED,
                legs=[leg1_result, leg2_result],
                error=f"Exit leg1 filled, leg2 failed — unclosed short stranded on {ex_b_id_strd}: {reason}",
                strategy_id=strategy_id,
            )

        # If leg1 was filled, place opposing order to unwind; otherwise cancel
        trade_order_id = leg1_result.trade.order_id if leg1_result.trade else None
        rb_ok, rb_reason = await self._rollback_order(
            ex_a_id, leg1_order, order_id=trade_order_id, filled=leg1_filled,
            filled_amount=leg1_result.filled_amount
        )

        if not rb_ok:
            should_halt = self._stranded_tracker.register(
                exchange_id=ex_a_id,
                symbol=leg1_order.symbol,
                side=str(leg1_order.side),
                size=float(leg1_order.amount),
                value_usd=float(leg1_order.amount * (leg1_order.price or Decimal("0"))),
                reason=rb_reason,
            )
            if should_halt:
                halt_local()
            logger.critical(
                "cross_exchange_rollback_failed HALT_SET exchange=%s strategy=%s",
                ex_a_id, strategy_id
            )
            return ExecutionResult(
                status=ExecutionStatus.ROLLBACK_FAILED,
                legs=[leg1_result, leg2_result],
                error=f"Rollback failed on {ex_a_id} — {'engine halted' if should_halt else 'stranded alert'}. Reason: {reason}",
                strategy_id=strategy_id,
            )

        # Fire deferred halt: leg2 rollback failed + threshold exceeded, but leg1 succeeded.
        # The comment at the deferral site said "fire from leg1 path if leg1 also fails" but
        # that is too conservative — if leg2 stranded exposure exceeded $30 threshold we must
        # halt regardless of leg1 outcome.
        if _leg2_halt:
            halt_local()
            logger.critical(
                "cross_exchange_deferred_leg2_halt_fired strategy=%s — "
                "leg2 stranded threshold exceeded, leg1 rolled back cleanly",
                strategy_id,
            )
            return ExecutionResult(
                status=ExecutionStatus.ROLLBACK_FAILED,
                legs=[leg1_result, leg2_result],
                error=f"Leg2 rollback failed (stranded threshold exceeded) on {leg2_result.order.exchange_id if leg2_result.order else 'unknown'} — halt fired after leg1 unwind. Reason: {reason}",
                strategy_id=strategy_id,
            )

        return ExecutionResult(
            status=ExecutionStatus.ROLLED_BACK,
            legs=[leg1_result, leg2_result],
            error=reason,
            strategy_id=strategy_id,
        )

    @staticmethod
    def _reconcile_done_callback(task: asyncio.Task[None]) -> None:
        """Log errors from fire-and-forget reconciliation tasks."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("post_execution_reconcile_unhandled error=%s", exc)

    async def _post_execution_reconcile(
        self,
        ex_a_id: str,
        ex_b_id: str,
        strategy_id: str,
        leg1_result: LegResult | None = None,
        leg2_result: LegResult | None = None,
        delay_s: float = 5.0,
    ) -> None:
        """
        Amendment 4 Steps 13-14: After delay_s, verify fills via REST on BOTH exchanges.
        Compares expected fills (from leg results) against actual positions.
        Logs discrepancies and alerts on mismatch.
        """
        await asyncio.sleep(delay_s)
        logger.info(
            "post_execution_reconcile start ex_a=%s ex_b=%s strategy=%s",
            ex_a_id, ex_b_id, strategy_id
        )

        expected_fills: dict[str, Decimal] = {}
        expected_symbols: dict[str, str] = {}
        if leg1_result and leg1_result.trade:
            expected_fills[ex_a_id] = leg1_result.trade.amount
            expected_symbols[ex_a_id] = leg1_result.order.symbol
        if leg2_result and leg2_result.trade:
            expected_fills[ex_b_id] = leg2_result.trade.amount
            expected_symbols[ex_b_id] = leg2_result.order.symbol

        for ex_id in (ex_a_id, ex_b_id):
            adapter = self._exchanges.get(ex_id)
            if adapter is None:
                continue
            try:
                positions = await adapter.get_positions()
                expected = expected_fills.get(ex_id, Decimal("0"))
                expected_sym = expected_symbols.get(ex_id, "")
                logger.info(
                    "post_execution_reconcile ex=%s positions=%d expected_fill=%s symbol=%s",
                    ex_id, len(positions), expected, expected_sym,
                )
                # BUG-64: Check symbol-specific position, not total position count.
                # len(positions)==0 misses the case where other symbols have open positions.
                if expected > 0 and expected_sym:
                    matching = [p for p in positions if p.symbol == expected_sym]
                    if not matching:
                        logger.warning(
                            "reconcile_mismatch ex=%s symbol=%s expected_fill=%s "
                            "but_no_matching_position strategy=%s",
                            ex_id, expected_sym, expected, strategy_id,
                        )
                    else:
                        # Check amount within 5% tolerance band
                        actual_size = abs(matching[0].size)
                        if actual_size > 0:
                            diff_pct = abs(actual_size - expected) / expected
                            if diff_pct > Decimal("0.05"):
                                logger.warning(
                                    "reconcile_amount_mismatch ex=%s symbol=%s "
                                    "expected=%.6f actual=%.6f diff_pct=%.1f%% strategy=%s",
                                    ex_id, expected_sym, float(expected),
                                    float(actual_size), float(diff_pct * 100), strategy_id,
                                )
            except Exception as exc:
                logger.error(
                    "post_execution_reconcile_error ex=%s error=%s", ex_id, exc
                )
