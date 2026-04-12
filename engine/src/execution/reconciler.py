"""Position reconciler — compare engine vs exchange state, detect discrepancies."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable

from src.core.models import Position
from src.infra.exchange.base import ExchangeAdapter

logger = logging.getLogger(__name__)

# Tolerance for position size comparison (floating point rounding)
_SIZE_TOLERANCE = Decimal("0.0001")


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

    @property
    def stranded_positions(self) -> set[str]:
        return self._stranded

    def mark_stranded(self, key: str) -> None:
        """Mark a position as stranded (rollback failed, needs recovery)."""
        self._stranded.add(key)
        logger.critical("stranded_position_marked key=%s", key)

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
                positions = await adapter.get_positions()
                for pos in positions:
                    key = f"{pos.exchange_id}:{pos.symbol}"
                    exchange_positions[key] = pos
            except Exception as exc:
                logger.error("reconciler_fetch_error exchange=%s error=%s", exchange_id, exc)
                fetch_failed_exchanges.append(exchange_id)  # BUG-01: don't return false negative

        discrepancies: list[str] = []

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
                discrepancies.append(msg)
                logger.warning("reconciler_discrepancy %s", msg)

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

        # Include stranded positions in discrepancies
        for stranded_key in self._stranded:
            msg = f"{stranded_key}: stranded position (rollback failed)"
            if msg not in discrepancies:
                discrepancies.append(msg)

        # BUG-01: API fetch failures = incomplete data — log separately, not as mismatch
        if fetch_failed_exchanges:
            logger.error(
                "reconciler_fetch_incomplete exchanges=%s — reconciliation data incomplete",
                fetch_failed_exchanges,
            )

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

        return result
