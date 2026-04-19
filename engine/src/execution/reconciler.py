"""Position reconciler — compare engine vs exchange state, detect discrepancies."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable

from src.core.models import Position
from src.infra.exchange.base import ExchangeAdapter

logger = logging.getLogger(__name__)


def _inc_discrepancy(exchange_id: str, discrepancy_type: str) -> None:
    """BUG-198b: lazy import to avoid cycles; degrade silently if metrics unavailable."""
    try:
        from src.infra.metrics import RECONCILER_DISCREPANCY_TOTAL
        RECONCILER_DISCREPANCY_TOTAL.labels(
            exchange=exchange_id or "unknown",
            type=discrepancy_type,
        ).inc()
    except Exception as _exc:
        logger.debug("reconciler.metric_export_failed type=%s error=%s", discrepancy_type, _exc)

# Tolerance for position size comparison (floating point rounding)
_SIZE_TOLERANCE = Decimal("0.0001")


def aggregate_engine_positions(records) -> dict[str, Position]:
    """BUG-223: Sum signed quantities per (exchange_id, symbol) across strategies.

    PositionManager keys by (strategy_id, exchange_id, symbol); two strategies on
    the same symbol produce two records that the exchange sees as one net position.
    Collapse to "{exchange_id}:{symbol}" and sum signed size so the reconciler's
    engine-vs-exchange comparison is apples-to-apples.
    """
    agg: dict[str, Position] = {}
    for p in records:
        key = f"{p.exchange_id}:{p.symbol}"
        side = str(getattr(p, "side", "")).upper()
        qty = abs(Decimal(str(p.quantity)))
        signed = -qty if ("SHORT" in side or "SELL" in side) else qty
        entry = getattr(p, "entry_price", None) or getattr(p, "avg_price", Decimal("0"))
        if key in agg:
            existing = agg[key]
            agg[key] = Position(
                exchange_id=existing.exchange_id,
                symbol=existing.symbol,
                size=existing.size + signed,
                entry_price=existing.entry_price,
            )
        else:
            agg[key] = Position(
                exchange_id=p.exchange_id,
                symbol=p.symbol,
                size=signed,
                entry_price=entry,
            )
    return agg


@dataclass
class ReconciliationResult:
    has_discrepancy: bool
    discrepancies: list[str]
    engine_positions: dict[str, Position]
    exchange_positions: dict[str, Position]
    fetch_failed_exchanges: list[str] = field(default_factory=list)


class PositionReconciler:
    """
    Reconciles engine-tracked positions against live exchange state.

    Run periodically (every 60s) to detect discrepancies.
    On discrepancy: fires on_discrepancy callback and logs alert.

    Also tracks stranded positions (Amendment 4, step 14).
    """

    def __init__(
        self,
        exchanges: list[ExchangeAdapter],
        on_discrepancy: Callable[[ReconciliationResult], None] | None = None,
        size_tolerance: Decimal = _SIZE_TOLERANCE,
    ) -> None:
        self._exchanges = {ex.exchange_id: ex for ex in exchanges}
        self.on_discrepancy = on_discrepancy
        self._size_tolerance = size_tolerance
        self._stranded: set[str] = set()
        # BUG-202: extend BUG-164 2-cycle guard to ALSO cover the unrecorded type
        # ("exchange has, engine doesn't" = position just filled, engine.register
        # not yet complete). First-cycle unrecorded keys get INFO-level logging;
        # second-cycle persistence escalates to CRITICAL + on_discrepancy callback.
        # The guard at src/main.py suppresses Telegram but NOT the CRITICAL log
        # line, so we have to downgrade the log here, at source.
        self._prev_unrecorded_keys: set[str] = set()

    @property
    def stranded_positions(self) -> set[str]:
        return self._stranded

    def mark_stranded(self, key: str) -> None:
        """Mark a position as stranded (rollback failed, needs recovery)."""
        self._stranded.add(key)
        logger.critical("stranded_position_marked key=%s", key)
        # BUG-198b: export stranded mark to Prometheus as a discrepancy type.
        _exchange_id = key.split(":", 1)[0] if ":" in key else "unknown"
        _inc_discrepancy(_exchange_id, "stranded")

    def clear_stranded(self, key: str) -> None:
        """Clear a stranded position after recovery."""
        self._stranded.discard(key)
        logger.info("stranded_position_cleared key=%s", key)

    async def reconcile(
        self, engine_positions: dict[str, Position]
    ) -> ReconciliationResult:
        """
        Compare engine_positions against live exchange state.

        engine_positions: dict keyed by "{exchange_id}:{symbol}"
        Returns ReconciliationResult with any discrepancies found.
        """
        # Fetch positions from all exchanges
        exchange_positions: dict[str, Position] = {}
        fetch_failed_exchanges: list[str] = []  # BUG-01: track API failures
        for exchange_id, adapter in self._exchanges.items():
            try:
                # BUG-184: use strict variant so network errors (RemoteProtocolError,
                # timeouts) raise instead of silently returning [], which previously
                # triggered false CRITICAL "engine has position, exchange has no position"
                # alerts when Binance HTTP/2 connections terminated mid-fetch.
                _strict = getattr(adapter, "get_positions_strict", None)
                if _strict is not None:
                    positions = await _strict()
                else:
                    logger.warning(
                        "adapter %s lacks get_positions_strict — BUG-184 false-alert guard disabled",
                        exchange_id,
                    )
                    positions = await adapter.get_positions()
                for pos in positions:
                    key = f"{pos.exchange_id}:{pos.symbol}"
                    exchange_positions[key] = pos
            except Exception as exc:
                logger.error("reconciler_fetch_error exchange=%s error=%s", exchange_id, exc)
                fetch_failed_exchanges.append(exchange_id)  # BUG-01/184: don't return false negative

        discrepancies: list[str] = []
        # BUG-202: track unrecorded keys observed this cycle (for 2-cycle guard).
        unrecorded_this_cycle: set[str] = set()
        transient_unrecorded: list[str] = []  # first-cycle only — don't escalate

        # Check: positions exchange has that engine doesn't know about
        for key, ex_pos in exchange_positions.items():
            if key not in engine_positions:
                # Bug 28: skip positions where entry_price=0 (Bitget REST stale) AND size is tiny
                # Real positions with entry_price=0 are still tracked — only skip truly negligible ones
                notional_by_mark = abs(ex_pos.size * ex_pos.mark_price) if ex_pos.mark_price and ex_pos.mark_price > 0 else Decimal("0")
                if ex_pos.entry_price == Decimal("0") and notional_by_mark < Decimal("0.01"):
                    logger.debug("reconciler.ghost_skipped key=%s mark_notional=%s", key, notional_by_mark)
                    continue
                msg = (
                    f"{key}: engine has no record, "
                    f"exchange has size={ex_pos.size}"
                )
                unrecorded_this_cycle.add(key)
                # BUG-202: first-cycle unrecorded = race window between exchange
                # fill ACK and PositionManager.register. Treat as transient INFO
                # on first observation; only escalate to discrepancies on the
                # second cycle when the orphan persists.
                if key in self._prev_unrecorded_keys:
                    discrepancies.append(msg)
                    logger.warning("reconciler_discrepancy %s (persistent)", msg)
                    # BUG-198b: exchange has position, engine doesn't know.
                    _inc_discrepancy(ex_pos.exchange_id, "unrecorded")
                else:
                    transient_unrecorded.append(msg)
                    logger.info(
                        "reconciler_unrecorded_transient %s "
                        "(will escalate next cycle if persistent — BUG-202 race guard)",
                        msg,
                    )

        # Check: positions engine tracks that exchange doesn't have
        for key, eng_pos in engine_positions.items():
            # BUG-84: skip positions from failed exchanges — can't validate and would
            # generate false "engine has position, exchange has none" discrepancy alerts
            eng_exchange_id = key.split(":")[0]
            if eng_exchange_id in fetch_failed_exchanges:
                continue
            if key not in exchange_positions:
                if abs(eng_pos.size) > self._size_tolerance:
                    msg = (
                        f"{key}: engine has size={eng_pos.size}, "
                        f"exchange has no position"
                    )
                    discrepancies.append(msg)
                    logger.warning("reconciler_discrepancy %s", msg)
                    # BUG-198b: engine tracks it, exchange doesn't — orphan.
                    _inc_discrepancy(eng_exchange_id, "orphan")
            else:
                # Check size mismatch
                ex_pos = exchange_positions[key]
                size_diff = abs(eng_pos.size - ex_pos.size)
                if size_diff > self._size_tolerance:
                    msg = (
                        f"{key}: engine={eng_pos.size}, "
                        f"exchange={ex_pos.size} (diff={size_diff})"
                    )
                    discrepancies.append(msg)
                    logger.warning("reconciler_discrepancy %s", msg)
                    # BUG-198b: sizes don't match.
                    _inc_discrepancy(eng_exchange_id, "size_mismatch")

        # Include stranded positions in discrepancies
        for stranded_key in self._stranded:
            msg = f"{stranded_key}: stranded position (rollback failed)"
            if msg not in discrepancies:
                discrepancies.append(msg)

        # BUG-198a: publish current stranded/ghost count per exchange as a Gauge
        # so operators can alert when >0 for >N minutes.
        try:
            from src.infra.metrics import GHOST_POSITIONS_CURRENT
            _counts: dict[str, int] = {ex_id: 0 for ex_id in self._exchanges}
            for stranded_key in self._stranded:
                _ex_id = stranded_key.split(":", 1)[0] if ":" in stranded_key else "unknown"
                _counts[_ex_id] = _counts.get(_ex_id, 0) + 1
            for _ex_id, _count in _counts.items():
                GHOST_POSITIONS_CURRENT.labels(exchange=_ex_id).set(_count)
        except Exception as _gauge_exc:
            logger.debug("reconciler.ghost_gauge_failed error=%s", _gauge_exc)

        # BUG-01: API fetch failures = incomplete data — log separately, not as mismatch
        if fetch_failed_exchanges:
            logger.error(
                "reconciler_fetch_incomplete exchanges=%s — reconciliation data incomplete",
                fetch_failed_exchanges,
            )

        # BUG-202: roll unrecorded keys state forward for next cycle's guard.
        # BUG-210: per-exchange roll-forward. When an exchange's fetch fails
        # (e.g. Binance HTTP/2 RemoteProtocolError), it contributes zero
        # entries to unrecorded_this_cycle — unconditionally overwriting
        # _prev_unrecorded_keys would wipe prior-cycle state for ALL
        # exchanges, causing persistent ghosts to never escalate to CRITICAL
        # under intermittent network conditions. Instead, update only entries
        # belonging to successfully-fetched exchanges, and preserve prior
        # entries for failed-fetch exchanges.
        successful = set(self._exchanges) - set(fetch_failed_exchanges)
        new_prev = {
            k for k in unrecorded_this_cycle
            if k.split(":", 1)[0] in successful
        }
        for k in self._prev_unrecorded_keys:
            if k.split(":", 1)[0] not in successful:
                new_prev.add(k)
        self._prev_unrecorded_keys = new_prev

        # API 조회 실패(fetch_failed_exchanges)는 실제 포지션 불일치가 아님 — has_discrepancy에서 제외
        has_discrepancy = len(discrepancies) > 0

        result = ReconciliationResult(
            has_discrepancy=has_discrepancy,
            discrepancies=discrepancies,
            engine_positions=engine_positions,
            exchange_positions=exchange_positions,
            fetch_failed_exchanges=fetch_failed_exchanges,
        )

        # Only call on_discrepancy for REAL position mismatches, not transient API failures.
        # Fetch failures get logger.error above — no Telegram spam on exchange downtime.
        if discrepancies:
            logger.critical(
                "reconciler_discrepancy_found count=%d details=%s",
                len(discrepancies),
                discrepancies,
            )
            if self.on_discrepancy:
                self.on_discrepancy(result)
        elif transient_unrecorded:
            # BUG-202: first-cycle unrecorded positions observed — race with
            # PositionManager.register. Log at INFO so operators can see the
            # race window without false CRITICAL alerts. No callback.
            logger.info(
                "reconciler_unrecorded_transient_count=%d keys=%s",
                len(transient_unrecorded),
                list(unrecorded_this_cycle)[:5],
            )

        return result
