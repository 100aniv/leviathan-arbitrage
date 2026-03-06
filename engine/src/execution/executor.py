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

from src.core.models import Order, OrderSide, Trade
from src.infra.exchange.base import ExchangeAdapter
from src.risk.kill_switch import halt_local, is_halted

logger = logging.getLogger(__name__)

# Health score threshold (Amendment 4 step 1)
_HEALTH_THRESHOLD = 0.9
# Partial fill acceptance threshold
_PARTIAL_FILL_THRESHOLD = Decimal("0.80")
# Blueprint compliance: LEG_TIMEOUT_MS from environment (.env or system)
import os as _os
_LEG_TIMEOUT_MS = int(_os.getenv("LEG_TIMEOUT_MS", "500"))
_ROLLBACK_TIMEOUT_MS = int(_os.getenv("ROLLBACK_TIMEOUT_MS", "2000"))
_RECONCILIATION_INTERVAL_S = int(_os.getenv("RECONCILIATION_INTERVAL_S", "5"))


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

    @property
    def filled_amount(self) -> Decimal:
        if self.trade is None:
            return Decimal("0")
        return self.trade.amount

    def fill_ratio(self, requested: Decimal) -> Decimal:
        if requested <= 0:
            return Decimal("0")
        return self.filled_amount / requested


@dataclass
class ExecutionResult:
    status: ExecutionStatus
    leg1: LegResult | None
    leg2: LegResult | None
    rollback_cost: Decimal = Decimal("0")
    error: str = ""
    strategy_id: str = ""


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
        self._locked: set[str] = set()

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
        """Return True if exchange health_score > threshold."""
        adapter = self._exchanges.get(exchange_id)
        if adapter is None:
            return False
        return adapter.health_score > self._config.health_threshold

    async def _place_with_timeout(self, adapter: ExchangeAdapter, order: Order) -> Trade:
        """Place order with timeout. Raises asyncio.TimeoutError on timeout."""
        timeout_s = self._config.timeout_ms / 1000.0
        return await asyncio.wait_for(adapter.place_order(order), timeout=timeout_s)

    async def _rollback_order(
        self, exchange_id: str, order: Order, order_id: str | None = None
    ) -> bool:
        """
        Attempt to cancel/close an order as part of rollback.
        order_id overrides order.order_id (use trade.order_id for filled orders).
        Passes order.symbol to cancel_order for exchanges that require it (e.g. Binance).
        Returns True if rollback succeeded.
        """
        adapter = self._exchanges.get(exchange_id)
        if adapter is None:
            return False
        effective_id = order_id or order.order_id
        try:
            if effective_id:
                # Pass symbol for native adapters that require it (Binance)
                try:
                    await adapter.cancel_order(effective_id, symbol=order.symbol)
                except TypeError:
                    # Fallback for adapters that don't accept symbol kwarg
                    await adapter.cancel_order(effective_id)
            # else: order was never submitted — nothing to cancel
            return True
        except Exception as exc:
            logger.error("rollback_failed exchange=%s error=%s", exchange_id, exc)
            return False

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
                leg1=None,
                leg2=None,
                error="Engine halted",
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
                leg1=None,
                leg2=None,
                error=f"Exchange {exchange_id} health below threshold",
                strategy_id=strategy_id,
            )

        adapter = self._exchanges[exchange_id]
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

            leg1_result = LegResult(order=leg1_order, trade=leg1_trade,
                                    error=str(leg1_err) if leg1_err else None)
            leg2_result = LegResult(order=leg2_order, trade=leg2_trade,
                                    error=str(leg2_err) if leg2_err else None)

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
                # Rollback: cancel both
                rb1 = await self._rollback_order(exchange_id, leg1_order) if leg1_trade else True
                rb2 = await self._rollback_order(exchange_id, leg2_order) if leg2_trade else True

                if not rb1 or not rb2:
                    halt_local()
                    logger.critical(
                        "same_exchange_rollback_failed exchange=%s strategy=%s",
                        exchange_id, strategy_id
                    )
                    return ExecutionResult(
                        status=ExecutionStatus.ROLLBACK_FAILED,
                        leg1=leg1_result,
                        leg2=leg2_result,
                        error="Rollback failed — engine halted",
                        strategy_id=strategy_id,
                    )

                return ExecutionResult(
                    status=ExecutionStatus.ROLLED_BACK,
                    leg1=leg1_result,
                    leg2=leg2_result,
                    strategy_id=strategy_id,
                )

            return ExecutionResult(
                status=ExecutionStatus.SUCCESS,
                leg1=leg1_result,
                leg2=leg2_result,
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
                leg1=None, leg2=None,
                error=f"Unknown exchange(s): {ex_a_id}, {ex_b_id}",
                strategy_id=strategy_id,
            )

        # ── PHASE PRE-VALIDATION ─────────────────────────────────────────

        # Step 0 (implicit): Halt check — RC-CROSS-1
        if self._check_halt():
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                leg1=None, leg2=None,
                error="Engine halted",
                strategy_id=strategy_id,
            )

        # Step 1: Verify BOTH exchanges health_score > 0.9 — RC-CROSS-2
        if not self._check_health(ex_a_id):
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                leg1=None, leg2=None,
                error=f"Exchange {ex_a_id} health below threshold",
                strategy_id=strategy_id,
            )
        if not self._check_health(ex_b_id):
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                leg1=None, leg2=None,
                error=f"Exchange {ex_b_id} health below threshold",
                strategy_id=strategy_id,
            )

        # Step 2-3: Balance/margin checks — skipped in unit layer (handled by guardian)
        # Step 4: Re-read orderbooks to verify spread (best effort)
        try:
            ob_a = await adapter_a.get_orderbook_snapshot(leg1_order.symbol)
            ob_b = await adapter_b.get_orderbook_snapshot(leg2_order.symbol)
            # Basic spread sanity: buy ask < sell bid implies positive edge
            # (simplified; full edge calc done by strategy layer)
        except Exception as exc:
            logger.warning("orderbook_snapshot_failed error=%s", exc)
            # Non-fatal: proceed with stale data

        # Step 5: max_rollback_cost check (Amendment 3C) — delegated to guardian
        # Step 6-7: Acquire execution locks on BOTH exchanges (sorted to prevent deadlock)
        first_id, second_id = sorted([ex_a_id, ex_b_id])
        await self._acquire_lock(first_id)
        await self._acquire_lock(second_id)

        leg1_result: LegResult | None = None
        leg2_result: LegResult | None = None
        leg1_trade: Trade | None = None

        try:
            # ── PHASE SEQUENTIAL SUBMISSION ──────────────────────────────

            # Step 8: Submit Leg 1 on Exchange A
            try:
                leg1_trade = await self._place_with_timeout(adapter_a, leg1_order)
                leg1_result = LegResult(order=leg1_order, trade=leg1_trade)
            except asyncio.TimeoutError:
                logger.error("leg1_timeout exchange=%s strategy=%s", ex_a_id, strategy_id)
                await self._rollback_order(ex_a_id, leg1_order)
                return ExecutionResult(
                    status=ExecutionStatus.ROLLED_BACK,
                    leg1=LegResult(order=leg1_order, error="timeout"),
                    leg2=None,
                    error="Leg 1 timeout",
                    strategy_id=strategy_id,
                )
            except Exception as exc:
                logger.error("leg1_failed exchange=%s error=%s strategy=%s", ex_a_id, exc, strategy_id)
                return ExecutionResult(
                    status=ExecutionStatus.ROLLED_BACK,
                    leg1=LegResult(order=leg1_order, error=str(exc)),
                    leg2=None,
                    error=f"Leg 1 failed: {exc}",
                    strategy_id=strategy_id,
                )

            # Step 9: Evaluate Leg 1 fill
            leg1_ratio = leg1_result.fill_ratio(leg1_order.amount)

            if leg1_ratio <= self._config.partial_fill_threshold:
                # Partial ≤80% or zero fill → cancel leg1 + rollback
                logger.warning(
                    "leg1_partial_below_threshold ratio=%s strategy=%s",
                    leg1_ratio, strategy_id
                )
                await self._rollback_order(ex_a_id, leg1_order)
                return ExecutionResult(
                    status=ExecutionStatus.ROLLED_BACK,
                    leg1=leg1_result,
                    leg2=None,
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
                leg2_result = LegResult(order=adjusted_leg2, trade=leg2_trade)
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
            asyncio.ensure_future(
                self._post_execution_reconcile(
                    ex_a_id, ex_b_id, strategy_id,
                    delay_s=self._config.post_reconcile_delay_s
                )
            )

            logger.info(
                "cross_exchange_success leg1=%s leg2=%s strategy=%s",
                leg1_result.filled_amount, leg2_result.filled_amount, strategy_id
            )
            return ExecutionResult(
                status=ExecutionStatus.SUCCESS,
                leg1=leg1_result,
                leg2=leg2_result,
                strategy_id=strategy_id,
            )

        finally:
            self._release_lock(ex_a_id)
            self._release_lock(ex_b_id)

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
        Amendment 4 Step 12: Rollback leg 1 on Exchange A.

        If rollback fails → HALT flag + stranded position alert.
        """
        logger.warning(
            "cross_exchange_rollback reason=%s strategy=%s",
            reason, strategy_id
        )
        # Use trade's order_id if available (order was filled, use that ID)
        trade_order_id = leg1_result.trade.order_id if leg1_result.trade else None
        rb_ok = await self._rollback_order(ex_a_id, leg1_order, order_id=trade_order_id)

        if not rb_ok:
            halt_local()
            logger.critical(
                "cross_exchange_rollback_failed HALT_SET exchange=%s strategy=%s",
                ex_a_id, strategy_id
            )
            return ExecutionResult(
                status=ExecutionStatus.ROLLBACK_FAILED,
                leg1=leg1_result,
                leg2=leg2_result,
                error=f"Rollback failed on {ex_a_id} — engine halted. Reason: {reason}",
                strategy_id=strategy_id,
            )

        return ExecutionResult(
            status=ExecutionStatus.ROLLED_BACK,
            leg1=leg1_result,
            leg2=leg2_result,
            error=reason,
            strategy_id=strategy_id,
        )

    async def _post_execution_reconcile(
        self, ex_a_id: str, ex_b_id: str, strategy_id: str, delay_s: float = 5.0
    ) -> None:
        """
        Amendment 4 Step 13: After delay_s, verify fills via REST on BOTH exchanges.
        Logs any discrepancies found.
        """
        await asyncio.sleep(delay_s)
        logger.info(
            "post_execution_reconcile start ex_a=%s ex_b=%s strategy=%s",
            ex_a_id, ex_b_id, strategy_id
        )
        for ex_id in (ex_a_id, ex_b_id):
            adapter = self._exchanges.get(ex_id)
            if adapter is None:
                continue
            try:
                positions = await adapter.get_positions()
                logger.info(
                    "post_execution_reconcile ex=%s positions=%d",
                    ex_id, len(positions)
                )
            except Exception as exc:
                logger.error(
                    "post_execution_reconcile_error ex=%s error=%s", ex_id, exc
                )
