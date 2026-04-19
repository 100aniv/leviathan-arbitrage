"""WS-D2: Pre-execution Toxicity Filter.

Rejects signals whose orderbook health indicates toxic flow. Two checks:

1. ``orderbook_imbalance`` — |bid_depth − ask_depth| / (bid_depth + ask_depth).
   Values > 0.7 mean one side has >85% of the displayed size. That skew tends
   to either (a) collapse before we can unwind or (b) reflect adversarial
   quoting — we refuse to enter.
2. ``depth_volatility_1min`` — std of (bid+ask depth) samples taken over the
   last 60 seconds. Values > 3× the rolling median indicate quote instability.

Rejections emit a counter ``leviathan_signals_rejected_toxicity_total``
and an INFO log ``signal_rejected_by_toxicity`` so operators can diagnose.
"""
from __future__ import annotations

import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class _DepthSample:
    ts: float
    total_depth: float  # bid_depth + ask_depth (base asset)


@dataclass
class ToxicityConfig:
    """Tunable thresholds — defaults match the WS-D2 spec."""
    imbalance_limit: float = 0.7
    depth_volatility_multiplier: float = 3.0
    depth_history_seconds: float = 60.0
    # Minimum samples before depth_volatility gate activates (cold start)
    min_depth_samples: int = 10
    # Top-N levels to sum for "displayed depth" on each side
    depth_levels: int = 5


class ToxicityFilter:
    """Per-(exchange, symbol) orderbook health filter.

    Maintains a rolling window of (bid+ask) depth samples per book. Exposes a
    single ``check(book, exchange, symbol, strategy_id)`` method that returns
    a rejection reason string if the book is toxic, else ``None``.

    Designed to be cheap: O(depth_levels) on each call plus a bounded deque
    push. No external state, safe to share across strategies.
    """

    def __init__(self, config: ToxicityConfig | None = None) -> None:
        self._config = config or ToxicityConfig()
        # (exchange, symbol) -> deque[_DepthSample]
        self._depth_history: dict[tuple[str, str], deque[_DepthSample]] = {}

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _side_depth(self, levels: dict[Decimal, Decimal], side: str) -> float:
        """Sum top-N quantities. ``side`` ∈ {'bid','ask'} controls sort order."""
        if not levels:
            return 0.0
        n = self._config.depth_levels
        if side == "bid":
            prices = sorted(levels.keys(), reverse=True)[:n]
        else:  # ask
            prices = sorted(levels.keys())[:n]
        total = Decimal("0")
        for p in prices:
            total += levels[p]
        return float(total)

    def _record_depth(
        self, key: tuple[str, str], total_depth: float, now: float
    ) -> deque[_DepthSample]:
        hist = self._depth_history.setdefault(key, deque(maxlen=600))
        hist.append(_DepthSample(ts=now, total_depth=total_depth))
        # Evict samples older than history_seconds
        cutoff = now - self._config.depth_history_seconds
        while hist and hist[0].ts < cutoff:
            hist.popleft()
        return hist

    @staticmethod
    def _std_median(values: list[float]) -> tuple[float, float]:
        """Return (std, median) for a list of floats. Safe on small lists."""
        if not values:
            return 0.0, 0.0
        n = len(values)
        mean = sum(values) / n
        var = sum((v - mean) ** 2 for v in values) / n
        std = math.sqrt(var)
        sorted_v = sorted(values)
        mid = n // 2
        if n % 2 == 0:
            median = (sorted_v[mid - 1] + sorted_v[mid]) / 2
        else:
            median = sorted_v[mid]
        return std, median

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def check(
        self,
        *,
        book: Any,
        exchange: str,
        symbol: str,
        strategy_id: str = "unknown",
    ) -> str | None:
        """Evaluate book health. Returns rejection reason or None (pass).

        Side-effect: increments ``SIGNALS_REJECTED_TOXICITY`` counter on reject.
        Logger signature is stable: consumers grep
        ``signal_rejected_by_toxicity`` to find rejections.
        """
        bids = getattr(book, "bids", None)
        asks = getattr(book, "asks", None)
        if not bids or not asks:
            self._emit_rejection(strategy_id, exchange, symbol, "empty_book")
            return "empty_book"

        bid_depth = self._side_depth(bids, "bid")
        ask_depth = self._side_depth(asks, "ask")
        total = bid_depth + ask_depth
        if total <= 0:
            self._emit_rejection(strategy_id, exchange, symbol, "empty_book")
            return "empty_book"

        # 1) Imbalance gate.
        imbalance = (bid_depth - ask_depth) / total
        if abs(imbalance) > self._config.imbalance_limit:
            self._emit_rejection(
                strategy_id, exchange, symbol, "imbalance",
                imbalance=imbalance, bid=bid_depth, ask=ask_depth,
            )
            return "imbalance"

        # 2) Depth volatility gate — only once we have enough samples.
        now = time.monotonic()
        hist = self._record_depth((exchange, symbol), total, now)
        if len(hist) >= self._config.min_depth_samples:
            values = [s.total_depth for s in hist]
            std, median = self._std_median(values)
            # Guard against zero-median books (e.g. illiquid pairs).
            if median > 0 and std > self._config.depth_volatility_multiplier * median:
                self._emit_rejection(
                    strategy_id, exchange, symbol, "depth_volatility",
                    std=std, median=median,
                )
                return "depth_volatility"

        return None

    def _emit_rejection(
        self,
        strategy_id: str,
        exchange: str,
        symbol: str,
        reason: str,
        **extras: Any,
    ) -> None:
        """Counter + INFO log for a toxicity rejection."""
        try:
            from src.infra.metrics import SIGNALS_REJECTED_TOXICITY
            SIGNALS_REJECTED_TOXICITY.labels(
                strategy=strategy_id or "unknown",
                exchange=exchange or "unknown",
                reason=reason,
            ).inc()
        except Exception:
            # metrics import failure is non-fatal
            pass
        extras_str = " ".join(f"{k}={v}" for k, v in extras.items())
        logger.info(
            "signal_rejected_by_toxicity strategy=%s exchange=%s symbol=%s reason=%s %s",
            strategy_id, exchange, symbol, reason, extras_str,
        )
