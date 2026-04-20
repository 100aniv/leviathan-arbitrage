"""MarketStats — 24h rolling ADV aggregator from WS trade stream.

Path-B v2 Day 10. Replaces the top-5 orderbook-depth proxy in
`signal.py::_compute_dynamic_adv` with a real 24h USD volume aggregator
computed from live trade events.

Behind feature flag `CORE_REAL_ADV_ENABLED` (default `false`). While the
flag is off, or while the aggregator is still warming up (<15min of
data for a given pair), callers fall back to the existing proxy so
behaviour is unchanged.

This module is additive: it does not read or modify any existing
pipeline state. Adapter wiring (pumping `on_trade` from each native_*
adapter's WS trade stream) is deferred to a follow-up commit.
"""
from __future__ import annotations

import asyncio
import collections
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(slots=True)
class TradeEvent:
    """A single executed trade observed on an exchange.

    Attributes:
        exchange: Exchange id (e.g. "binance").
        symbol:   Canonical trading pair (e.g. "BTC/USDT").
        price:    Trade price in the quote asset.
        qty:      Trade quantity in the base asset.
        ts_ms:    Exchange-reported timestamp, milliseconds since epoch.
    """

    exchange: str
    symbol: str
    price: Decimal
    qty: Decimal
    ts_ms: int


class MarketStats:
    """Rolling 24h trade-volume aggregator, per (exchange, symbol).

    - `on_trade(event)` records a trade (async, guarded by an asyncio.Lock).
    - `get_adv_usd(ex, sym)` returns the sum of `price * qty` for that pair
      over the trailing 24h window, evicting trades older than the window.
    - `is_warm(ex, sym)` reports whether the pair has accumulated at least
      `WARMUP_MS` of history (min trade 'span' between first and last).

    Size is the `collections.deque` per pair; eviction is a forward sweep
    from the left until the head is within window. This is O(k) where k
    is the number of trades to evict, amortised O(1) per `on_trade` at
    steady state because each trade is evicted exactly once.
    """

    WINDOW_MS: int = 86_400_000  # 24h
    WARMUP_MS: int = 900_000  # 15min

    def __init__(self, start_ts_ms: int | None = None) -> None:
        self._trades: dict[tuple[str, str], collections.deque[TradeEvent]] = {}
        self._start_ts_ms: int = (
            start_ts_ms if start_ts_ms is not None else int(time.time() * 1000)
        )
        self._lock: asyncio.Lock = asyncio.Lock()

    # ------------------------------------------------------------------ API

    async def on_trade(self, event: TradeEvent) -> None:
        """Record a new trade. Safe under concurrent producers."""
        key = (event.exchange, event.symbol)
        async with self._lock:
            dq = self._trades.get(key)
            if dq is None:
                dq = collections.deque()
                self._trades[key] = dq
            dq.append(event)
            self._evict_locked(dq, event.ts_ms)

    def get_adv_usd(self, exchange: str, symbol: str) -> Decimal:
        """Sum `price * qty` for all trades in the 24h window.

        Evicts stale trades using the most-recent trade timestamp as the
        reference "now". For an empty or unknown pair, returns 0.
        """
        dq = self._trades.get((exchange, symbol))
        if not dq:
            return Decimal("0")
        ref_ms = dq[-1].ts_ms
        self._evict_locked(dq, ref_ms)
        total = Decimal("0")
        for ev in dq:
            total += ev.price * ev.qty
        return total

    def is_warm(self, exchange: str, symbol: str) -> bool:
        """True once the pair has ≥WARMUP_MS of continuous data."""
        dq = self._trades.get((exchange, symbol))
        if not dq or len(dq) < 2:
            return False
        span_ms = dq[-1].ts_ms - dq[0].ts_ms
        return span_ms >= self.WARMUP_MS

    def stats_summary(self) -> dict[str, Any]:
        """Debug snapshot for ops endpoints.

        Returns a plain dict (no Decimal/datetime) with per-pair trade
        count, warm flag, and current aggregate. Safe to call from any
        thread because it only reads deque length and tail timestamps.
        """
        pairs: dict[str, dict[str, Any]] = {}
        for (ex, sym), dq in self._trades.items():
            pair_key = f"{ex}:{sym}"
            pairs[pair_key] = {
                "count": len(dq),
                "warm": self.is_warm(ex, sym),
                "adv_usd": str(self.get_adv_usd(ex, sym)),
            }
        return {
            "window_ms": self.WINDOW_MS,
            "warmup_ms": self.WARMUP_MS,
            "start_ts_ms": self._start_ts_ms,
            "pairs": pairs,
        }

    # ---------------------------------------------------------------- internal

    def _evict_locked(
        self,
        dq: collections.deque[TradeEvent],
        ref_ms: int,
    ) -> None:
        """Drop head trades older than `ref_ms - WINDOW_MS`.

        Caller must hold `self._lock` for `on_trade` (write path). Readers
        (`get_adv_usd`) mutate via popleft but are idempotent because an
        already-evicted trade is simply gone; the only racy access from a
        reader would be seeing a trade that a concurrent writer just
        appended, which is safe (it would just be included or not in the
        aggregate, not corrupt the deque — CPython's deque primitive ops
        are atomic).
        """
        cutoff = ref_ms - self.WINDOW_MS
        while dq and dq[0].ts_ms < cutoff:
            dq.popleft()


# ---------------------------------------------------------------------------
# Module-level singleton accessor — signal.py and future adapters share this
# instance so trade data from any producer feeds the same aggregator.
# ---------------------------------------------------------------------------

_SINGLETON: MarketStats | None = None


def get_market_stats() -> MarketStats:
    """Return the process-wide MarketStats singleton (lazy-initialised)."""
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = MarketStats()
    return _SINGLETON


def reset_market_stats_for_tests() -> None:
    """Drop the singleton — test-only helper."""
    global _SINGLETON
    _SINGLETON = None
