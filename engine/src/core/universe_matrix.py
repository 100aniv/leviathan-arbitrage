"""Boot-time UniverseMatrix — precomputed (strategy, symbol, leg_a, leg_b) validity table.

Built once at startup, immutable for the process lifetime. Strategies consult
the matrix to verify that a signal's legs are actually tradeable before any
TradeRequest is emitted.

This prevents BUG-225-class failures where strategies emit signals for pairs
that don't exist on one leg of the hedge (e.g. AAVE/USDT on Upbit, which
only lists AAVE/KRW). Such signals produce one-sided fills and stranded
positions at execution time.

Design:
  - For each registered strategy type, the matrix enumerates every
    (symbol, leg_a, leg_b) combination that passes the adapter's
    ``supports_symbol`` check and fetches per-leg ``min_notional``.
  - Entries are stored in a dict keyed by (strategy_id, symbol, leg_a, leg_b)
    for O(1) membership tests.
  - Rejections are counted per (strategy, reason) and exposed via
    ``dump_report()`` for operator visibility.

Refresh policy:
  - Immutable for the process lifetime. An operator command to rebuild can be
    added later; signals must not silently re-enter after delisting.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)


# Mapping of strategy.STRATEGY_TYPE → required leg shape.
# Keys refer to the ``STRATEGY_TYPE`` attribute on each BaseStrategy subclass.
_STRATEGY_LEG_SHAPE: dict[str, str] = {
    "cross_exchange_spot": "spot_spot",
    "futures_futures": "futures_futures",
    "funding_rate_arb": "futures_futures",
    "spot_futures_basis": "spot_futures",
    "triangular": "triangular",
    # latency_arb is merged into cross_exchange (US-194), no separate shape.
    # statistical_arb + cex_dex are intentionally excluded — they have
    # bespoke shapes handled by their own validators (DEX leg, stat pair).
}


@dataclass(frozen=True)
class UniverseEntry:
    """Single validated (strategy, symbol, leg_a, leg_b) tuple.

    Immutable. Populated once at boot by :class:`UniverseMatrix`.
    """

    strategy_id: str
    symbol: str
    leg_a_exchange: str
    leg_b_exchange: Optional[str]
    min_notional_usd_a: Decimal
    min_notional_usd_b: Optional[Decimal]
    tick_size_a: Decimal
    validated_at: datetime
    required_market_type_a: str
    required_market_type_b: Optional[str]


_EntryKey = tuple[str, str, str, Optional[str]]


@dataclass
class _RejectionStats:
    """Per-strategy rejection counter broken down by reason code."""

    counts: dict[str, int] = field(default_factory=dict)

    def add(self, reason: str) -> None:
        self.counts[reason] = self.counts.get(reason, 0) + 1

    def total(self) -> int:
        return sum(self.counts.values())


class UniverseMatrix:
    """Boot-time registry of valid (strategy, symbol, legs) tuples.

    Consumers call :meth:`has_entry` or :meth:`get_entry` before emitting a
    TradeRequest. Strategies that cannot validate a leg against this matrix
    must reject the signal (defense in depth — PreTradeValidator also gates
    on the same data).
    """

    def __init__(self) -> None:
        self._entries: dict[_EntryKey, UniverseEntry] = {}
        self._rejections: dict[str, _RejectionStats] = {}
        self._built: bool = False

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    async def build(
        self,
        exchange_registry: dict[str, Any],
        strategy_registry: Iterable[Any],
    ) -> None:
        """Populate the matrix against the live exchange + strategy set.

        Args:
            exchange_registry: ``exchange_id -> adapter`` map. Each adapter
                must expose ``supports_symbol(symbol)`` and
                ``get_min_notional(symbol)`` (spec: see
                :class:`src.infra.exchange.base.ExchangeAdapter`).
            strategy_registry: Iterable of strategy instances with a
                ``STRATEGY_TYPE`` class attribute and a ``strategy_id``.
        """
        if self._built:
            logger.info("universe_matrix.build_skipped reason=already_built entries=%d",
                        len(self._entries))
            return

        # Collect candidate symbols from strategies' inferred symbol universes.
        # The matrix is symbol-driven — we only validate pairs that at least
        # one adapter claims to list. Gathering this list cheaply from the
        # exchange layer keeps the matrix O(#symbols × #exchanges^2).
        candidate_symbols = self._gather_candidate_symbols(exchange_registry)

        spot_exchanges, futures_exchanges = self._partition_by_market_type(
            exchange_registry
        )

        strategies_by_type: dict[str, list[str]] = {}
        for strategy in strategy_registry:
            stype = getattr(strategy, "STRATEGY_TYPE", None)
            if not stype:
                continue
            strategies_by_type.setdefault(stype, []).append(strategy.strategy_id)

        for stype, strategy_ids in strategies_by_type.items():
            shape = _STRATEGY_LEG_SHAPE.get(stype)
            if shape is None:
                logger.debug("universe_matrix.skip_strategy_type type=%s (no shape)", stype)
                continue
            for sid in strategy_ids:
                await self._build_for_strategy(
                    strategy_id=sid,
                    strategy_type=stype,
                    shape=shape,
                    exchange_registry=exchange_registry,
                    spot_exchanges=spot_exchanges,
                    futures_exchanges=futures_exchanges,
                    candidate_symbols=candidate_symbols,
                )

        self._built = True
        logger.info(
            "universe_matrix.built entries=%d strategies=%d exchanges=%d",
            len(self._entries),
            len(strategies_by_type),
            len(exchange_registry),
        )

    # ------------------------------------------------------------------
    # Lookup API
    # ------------------------------------------------------------------

    def has_entry(
        self,
        strategy_id: str,
        symbol: str,
        leg_a: str,
        leg_b: Optional[str] = None,
    ) -> bool:
        """Return True iff this tuple was validated at build time."""
        return (strategy_id, symbol, leg_a, leg_b) in self._entries

    def get_entry(
        self,
        strategy_id: str,
        symbol: str,
        leg_a: str,
        leg_b: Optional[str] = None,
    ) -> Optional[UniverseEntry]:
        """Return the validated entry or None."""
        return self._entries.get((strategy_id, symbol, leg_a, leg_b))

    def size(self) -> int:
        """Total number of validated entries across all strategies."""
        return len(self._entries)

    def dump_report(self) -> str:
        """Human-readable summary: counts per strategy + rejection reasons."""
        by_strategy: dict[str, int] = {}
        for (sid, _sym, _la, _lb) in self._entries.keys():
            by_strategy[sid] = by_strategy.get(sid, 0) + 1

        lines: list[str] = ["universe_matrix report"]
        all_sids = sorted(set(by_strategy) | set(self._rejections))
        for sid in all_sids:
            valid_n = by_strategy.get(sid, 0)
            rej = self._rejections.get(sid)
            rej_total = rej.total() if rej else 0
            parts = [f"{sid}: {valid_n} valid pairs"]
            if rej_total:
                detail = ", ".join(
                    f"{reason}: {count}"
                    for reason, count in sorted(
                        rej.counts.items(), key=lambda kv: -kv[1]
                    )
                )
                parts.append(f"{rej_total} rejected ({detail})")
            lines.append(" — ".join(parts))
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal build helpers
    # ------------------------------------------------------------------

    def _gather_candidate_symbols(
        self, exchange_registry: dict[str, Any]
    ) -> set[str]:
        """Collect candidate symbols from adapter-reported symbol lists.

        Adapters expose optional ``symbols``/``_symbols`` attributes populated
        by the collector layer. Falls back to a conservative set of majors
        when no adapter publishes a list — keeps the matrix functional in
        unit tests where adapters are mocked.
        """
        collected: set[str] = set()
        for adapter in exchange_registry.values():
            for attr in ("symbols", "_symbols", "subscribed_symbols"):
                val = getattr(adapter, attr, None)
                if isinstance(val, (set, list, tuple)):
                    collected.update(val)
        if collected:
            return collected
        # Conservative fallback (for tests and early boot before the collector
        # manager has populated symbol lists).
        return {"BTC/USDT", "ETH/USDT"}

    def _partition_by_market_type(
        self, exchange_registry: dict[str, Any]
    ) -> tuple[list[str], list[str]]:
        """Split adapters into spot + futures groups based on ``_market_type``."""
        spot: list[str] = []
        futures: list[str] = []
        for eid, adapter in exchange_registry.items():
            mtype = getattr(adapter, "_market_type", "spot")
            if mtype == "futures":
                futures.append(eid)
            else:
                spot.append(eid)
        return spot, futures

    async def _build_for_strategy(
        self,
        *,
        strategy_id: str,
        strategy_type: str,
        shape: str,
        exchange_registry: dict[str, Any],
        spot_exchanges: list[str],
        futures_exchanges: list[str],
        candidate_symbols: set[str],
    ) -> None:
        stats = self._rejections.setdefault(strategy_id, _RejectionStats())

        if shape == "spot_spot":
            await self._build_pair_shape(
                strategy_id, shape, spot_exchanges, spot_exchanges,
                exchange_registry, candidate_symbols, stats,
                required_a="spot", required_b="spot",
            )
        elif shape == "futures_futures":
            await self._build_pair_shape(
                strategy_id, shape, futures_exchanges, futures_exchanges,
                exchange_registry, candidate_symbols, stats,
                required_a="futures", required_b="futures",
            )
        elif shape == "spot_futures":
            await self._build_pair_shape(
                strategy_id, shape, spot_exchanges, futures_exchanges,
                exchange_registry, candidate_symbols, stats,
                required_a="spot", required_b="futures",
            )
        elif shape == "triangular":
            await self._build_triangular(
                strategy_id, spot_exchanges, exchange_registry,
                candidate_symbols, stats,
            )
        # unknown shapes already filtered at the call site.

    async def _build_pair_shape(
        self,
        strategy_id: str,
        shape: str,
        left_pool: list[str],
        right_pool: list[str],
        exchange_registry: dict[str, Any],
        candidate_symbols: set[str],
        stats: _RejectionStats,
        *,
        required_a: str,
        required_b: str,
    ) -> None:
        same_pool = left_pool is right_pool
        for symbol in sorted(candidate_symbols):
            for leg_a in left_pool:
                ad_a = exchange_registry.get(leg_a)
                if ad_a is None or not _supports(ad_a, symbol):
                    stats.add(f"{leg_a}_{required_a}_listing_miss")
                    continue
                for leg_b in right_pool:
                    if same_pool and leg_b <= leg_a:
                        # canonicalize ordered pairs — skip reflexive + mirror
                        continue
                    if not same_pool and leg_b == leg_a:
                        continue
                    ad_b = exchange_registry.get(leg_b)
                    if ad_b is None or not _supports(ad_b, symbol):
                        stats.add(f"{leg_b}_{required_b}_listing_miss")
                        continue
                    try:
                        notional_a = await ad_a.get_min_notional(symbol)
                        notional_b = await ad_b.get_min_notional(symbol)
                    except Exception as exc:
                        logger.debug(
                            "universe_matrix.min_notional_failed sid=%s sym=%s err=%s",
                            strategy_id, symbol, exc,
                        )
                        stats.add("min_notional_fetch_failed")
                        continue
                    tick = _tick_size(ad_a, symbol)
                    entry = UniverseEntry(
                        strategy_id=strategy_id,
                        symbol=symbol,
                        leg_a_exchange=leg_a,
                        leg_b_exchange=leg_b,
                        min_notional_usd_a=Decimal(str(notional_a)),
                        min_notional_usd_b=Decimal(str(notional_b)),
                        tick_size_a=tick,
                        validated_at=datetime.now(timezone.utc),
                        required_market_type_a=required_a,
                        required_market_type_b=required_b,
                    )
                    self._entries[(strategy_id, symbol, leg_a, leg_b)] = entry

    async def _build_triangular(
        self,
        strategy_id: str,
        spot_exchanges: list[str],
        exchange_registry: dict[str, Any],
        candidate_symbols: set[str],
        stats: _RejectionStats,
    ) -> None:
        """Triangular needs 3 complete pairs on a single exchange.

        The matrix stores one entry per (exchange, triangle base symbol).
        Triangle topology discovery is handled elsewhere; the matrix only
        guarantees the triangle's base symbol is listed and has a fetchable
        min_notional. leg_b is intentionally None — triangular is intra-exchange.
        """
        for eid in spot_exchanges:
            adapter = exchange_registry.get(eid)
            if adapter is None:
                continue
            for symbol in sorted(candidate_symbols):
                if not _supports(adapter, symbol):
                    stats.add(f"{eid}_spot_listing_miss")
                    continue
                try:
                    notional = await adapter.get_min_notional(symbol)
                except Exception:
                    stats.add("min_notional_fetch_failed")
                    continue
                entry = UniverseEntry(
                    strategy_id=strategy_id,
                    symbol=symbol,
                    leg_a_exchange=eid,
                    leg_b_exchange=None,
                    min_notional_usd_a=Decimal(str(notional)),
                    min_notional_usd_b=None,
                    tick_size_a=_tick_size(adapter, symbol),
                    validated_at=datetime.now(timezone.utc),
                    required_market_type_a="spot",
                    required_market_type_b=None,
                )
                self._entries[(strategy_id, symbol, eid, None)] = entry


# ---------------------------------------------------------------------------
# Adapter interaction helpers
# ---------------------------------------------------------------------------


def _supports(adapter: Any, symbol: str) -> bool:
    """Best-effort ``supports_symbol`` check with fail-open default."""
    fn = getattr(adapter, "supports_symbol", None)
    if fn is None:
        return True
    try:
        return bool(fn(symbol))
    except Exception:
        return False


def _tick_size(adapter: Any, symbol: str) -> Decimal:
    """Optional tick size lookup — return Decimal("0") when unknown."""
    fn = getattr(adapter, "get_tick_size", None)
    if fn is None:
        return Decimal("0")
    try:
        result = fn(symbol)
        if asyncio.iscoroutine(result):
            # tick_size is intentionally sync in the matrix — if an adapter
            # exposes only an async variant, treat it as unavailable.
            result.close()
            return Decimal("0")
        return Decimal(str(result))
    except Exception:
        return Decimal("0")
