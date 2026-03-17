"""Stale Orderbook Detector — multi-layer stale detection + blacklist.

US-066 Phase G: Defense-in-depth against Korean exchange stale orderbook data.
US-227: 4-layer stale detection system.

Responsibilities:
1. Cross-exchange price validation: compare mid-price against non-self median.
   For Korean exchanges, median uses only non-Korean exchanges.
2. Heartbeat EMA: track update interval EMA; stale if > 5x EMA (Layer 2).
3. Sequence gap detection: Binance pu / Bybit u / OKX sequence (Layer 3).
4. Spread normality: rolling median; stale if > 3x rolling median (Layer 4).
5. Blacklist management: TTL-based (exchange, symbol) pair blacklisting.
"""
from __future__ import annotations

import os
import time
from collections import deque
from statistics import median
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_EMA_ALPHA = 0.2          # EMA smoothing factor for heartbeat intervals
_HEARTBEAT_WARMUP = 10    # min samples before heartbeat check is active
_HEARTBEAT_WARMUP_S = 30.0  # min seconds before heartbeat check is active
_HEARTBEAT_STALE_MULT = 5.0  # stale if interval > N * EMA
_SPREAD_HISTORY_LEN = 50  # rolling window for spread normality
_SPREAD_STALE_MULT = 3.0  # stale if spread > N * rolling median


class StaleOrderbookDetector:
    """Multi-layer stale orderbook defense for shadow mode.

    Usage::

        detector = StaleOrderbookDetector()

        # In _on_orderbook, after book update:
        if not detector.check_cross_exchange(exchange, symbol, book, all_books):
            return  # stale price drift detected

        # In signal generation:
        if detector.is_blacklisted(exchange, symbol):
            return  # blacklisted pair

        # On fat-tail loss:
        detector.add_blacklist(exchange, symbol)
    """

    KOREAN_EXCHANGES = {"upbit", "bithumb", "coinone"}

    def __init__(
        self,
        deviation_pct: float | None = None,
        blacklist_ttl_s: float | None = None,
        min_comparison_exchanges: int = 2,
    ) -> None:
        """
        Args:
            deviation_pct: Max allowed mid-price deviation from median (fraction).
                           Default: STALE_CROSS_DEVIATION_PCT env var (0.10 = 10%).
            blacklist_ttl_s: Seconds a blacklisted pair stays blocked.
                             Default: STALE_BLACKLIST_TTL_S env var (300s).
            min_comparison_exchanges: Min exchange count for cross-validation.
                                      Skip check if fewer available (returns True).
        """
        self._deviation_pct: float = (
            deviation_pct
            if deviation_pct is not None
            else float(os.getenv("STALE_CROSS_DEVIATION_PCT", "0.10"))
        )
        self._blacklist_ttl_s: float = (
            blacklist_ttl_s
            if blacklist_ttl_s is not None
            else float(os.getenv("STALE_BLACKLIST_TTL_S", "300"))
        )
        self._min_comparison_exchanges = min_comparison_exchanges
        # {(exchange, symbol): expiry_monotonic_time}
        self._blacklist: dict[tuple[str, str], float] = {}

        # Layer 2: Heartbeat EMA tracking
        # {(exchange, symbol): (ema_interval_s, last_ts, sample_count, first_ts)}
        self._heartbeat: dict[tuple[str, str], tuple[float, float, int, float]] = {}

        # Layer 3: Last sequence per (exchange, symbol)
        self._last_seq: dict[tuple[str, str], int] = {}

        # Layer 4: Rolling spread history per (exchange, symbol)
        self._spread_history: dict[tuple[str, str], deque[float]] = {}

    def check_cross_exchange(
        self,
        exchange: str,
        symbol: str,
        book: Any,
        all_books: dict[str, dict[str, Any]],
    ) -> bool:
        """Validate book's mid-price against other exchanges' median.

        For Korean exchanges, comparison uses only non-Korean exchanges.
        Returns True if valid (or insufficient data), False if stale.
        """
        try:
            best_bid = book.best_bid()
            best_ask = book.best_ask()
            if best_bid is None or best_ask is None:
                return True
            mid_price = float((best_bid + best_ask) / 2)
            if mid_price <= 0:
                return True
        except Exception:
            return True

        is_korean = exchange in self.KOREAN_EXCHANGES
        symbol_books: dict[str, Any] = all_books.get(symbol, {})
        comparison_prices: list[float] = []

        for other_exchange, other_book in symbol_books.items():
            if other_exchange == exchange:
                continue
            # For Korean exchange: only use non-Korean as reference (Scenario 2 mitigation)
            if is_korean and other_exchange in self.KOREAN_EXCHANGES:
                continue
            try:
                ob_bid = other_book.best_bid()
                ob_ask = other_book.best_ask()
                if ob_bid is None or ob_ask is None:
                    continue
                other_mid = float((ob_bid + ob_ask) / 2)
                if other_mid > 0:
                    comparison_prices.append(other_mid)
            except Exception:
                continue

        # Insufficient comparison data: skip validation (returns True)
        if len(comparison_prices) < self._min_comparison_exchanges:
            return True

        ref_median = median(comparison_prices)
        if ref_median <= 0:
            return True

        deviation = abs(mid_price - ref_median) / ref_median
        if deviation > self._deviation_pct:
            logger.info(
                "stale_detector.cross_validation_failed",
                exchange=exchange,
                symbol=symbol,
                mid_price=f"{mid_price:.6f}",
                ref_median=f"{ref_median:.6f}",
                deviation_pct=f"{deviation * 100:.2f}%",
                threshold_pct=f"{self._deviation_pct * 100:.2f}%",
            )
            return False

        return True

    def is_blacklisted(self, exchange: str, symbol: str) -> bool:
        """Check if (exchange, symbol) is currently blacklisted.

        Auto-cleans expired entries on access.
        """
        key = (exchange, symbol)
        expiry = self._blacklist.get(key)
        if expiry is None:
            return False
        now = time.monotonic()
        if now >= expiry:
            del self._blacklist[key]
            return False
        return True

    def add_blacklist(self, exchange: str, symbol: str, ttl_s: float | None = None) -> None:
        """Blacklist (exchange, symbol) for TTL seconds.

        If already blacklisted (TTL not expired), skip re-registration to prevent
        infinite blacklist loop where fat-tail losses repeatedly reset the TTL.

        Args:
            ttl_s: Override TTL in seconds. Defaults to self._blacklist_ttl_s.
        """
        key = (exchange, symbol)
        now = time.monotonic()
        existing_expiry = self._blacklist.get(key)
        if existing_expiry is not None and now < existing_expiry:
            logger.debug(
                "stale_detector.blacklist_already_active",
                exchange=exchange,
                symbol=symbol,
                remaining_s=f"{existing_expiry - now:.1f}",
            )
            return
        effective_ttl = ttl_s if ttl_s is not None else self._blacklist_ttl_s
        expiry = now + effective_ttl
        self._blacklist[key] = expiry
        logger.info(
            "stale_detector.blacklist_added",
            exchange=exchange,
            symbol=symbol,
            ttl_s=effective_ttl,
        )

    # ------------------------------------------------------------------
    # Layer 2: Heartbeat EMA
    # ------------------------------------------------------------------

    def update_heartbeat(self, exchange: str, symbol: str) -> None:
        """Record a new orderbook update for heartbeat EMA tracking."""
        key = (exchange, symbol)
        now = time.monotonic()
        entry = self._heartbeat.get(key)
        if entry is None:
            self._heartbeat[key] = (0.0, now, 0, now)
            return
        ema, last_ts, count, first_ts = entry
        interval = now - last_ts
        if interval <= 0:
            return
        new_ema = _EMA_ALPHA * interval + (1 - _EMA_ALPHA) * ema if ema > 0 else interval
        self._heartbeat[key] = (new_ema, now, count + 1, first_ts)

    def check_heartbeat(self, exchange: str, symbol: str) -> bool:
        """Layer 2: Return False if heartbeat interval is stale (> 5x EMA).

        Passes through (returns True) during warmup (< 10 samples or < 30s).
        """
        key = (exchange, symbol)
        entry = self._heartbeat.get(key)
        if entry is None:
            return True
        ema, last_ts, count, first_ts = entry
        now = time.monotonic()
        # Warmup guard
        if count < _HEARTBEAT_WARMUP or (now - first_ts) < _HEARTBEAT_WARMUP_S:
            return True
        if ema <= 0:
            return True
        elapsed = now - last_ts
        if elapsed > _HEARTBEAT_STALE_MULT * ema:
            logger.debug(
                "stale_detector.heartbeat_stale",
                exchange=exchange,
                symbol=symbol,
                elapsed_s=f"{elapsed:.2f}",
                ema_s=f"{ema:.2f}",
            )
            return False
        return True

    # ------------------------------------------------------------------
    # Layer 3: Sequence gap
    # ------------------------------------------------------------------

    def check_sequence_gap(
        self,
        exchange: str,
        symbol: str,
        seq: int | None,
    ) -> bool:
        """Layer 3: Return False if sequence gap detected.

        Passes through (returns True) if seq is None (exchange doesn't provide it).
        Only gaps > 1 are flagged (single-skip is tolerated for packet loss).
        """
        if seq is None:
            return True
        key = (exchange, symbol)
        prev = self._last_seq.get(key)
        self._last_seq[key] = seq
        if prev is None:
            return True
        gap = seq - prev
        if gap > 2:  # gap of 1 is expected; >2 means missed updates
            logger.warning(
                "stale_detector.sequence_gap",
                exchange=exchange,
                symbol=symbol,
                prev_seq=prev,
                curr_seq=seq,
                gap=gap,
            )
            return False
        return True

    # ------------------------------------------------------------------
    # Layer 4: Spread normality
    # ------------------------------------------------------------------

    def update_spread(self, exchange: str, symbol: str, spread: float) -> None:
        """Record current spread for rolling median normality check."""
        if spread <= 0:
            return
        key = (exchange, symbol)
        if key not in self._spread_history:
            self._spread_history[key] = deque(maxlen=_SPREAD_HISTORY_LEN)
        self._spread_history[key].append(spread)

    def check_spread_normality(
        self,
        exchange: str,
        symbol: str,
        current_spread: float,
    ) -> bool:
        """Layer 4: Return False if current spread > 3x rolling median.

        Passes through if fewer than 10 history samples (warmup).
        """
        if current_spread <= 0:
            return True
        key = (exchange, symbol)
        history = self._spread_history.get(key)
        if history is None or len(history) < 10:
            return True
        ref_median = median(history)
        if ref_median <= 0:
            return True
        if current_spread > _SPREAD_STALE_MULT * ref_median:
            logger.warning(
                "stale_detector.spread_abnormal",
                exchange=exchange,
                symbol=symbol,
                spread=f"{current_spread:.6f}",
                median=f"{ref_median:.6f}",
                ratio=f"{current_spread / ref_median:.1f}x",
            )
            return False
        return True

    # ------------------------------------------------------------------
    # Unified check
    # ------------------------------------------------------------------

    def check_all_layers(
        self,
        exchange: str,
        symbol: str,
        book: Any,
        all_books: dict[str, dict[str, Any]],
        seq: int | None = None,
        current_spread: float | None = None,
    ) -> bool:
        """Run all 4 layers in a single call. Returns False if any layer fails.

        Layer 1: cross-exchange price median (check_cross_exchange)
        Layer 2: heartbeat EMA staleness (check_heartbeat)
        Layer 3: sequence gap detection (check_sequence_gap)
        Layer 4: spread normality (check_spread_normality)
        """
        if not self.check_cross_exchange(exchange, symbol, book, all_books):
            return False
        if not self.check_heartbeat(exchange, symbol):
            return False
        if not self.check_sequence_gap(exchange, symbol, seq):
            return False
        if current_spread is not None and not self.check_spread_normality(
            exchange, symbol, current_spread
        ):
            return False
        return True

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def cleanup_expired(self) -> None:
        """Remove all expired blacklist entries."""
        now = time.monotonic()
        expired = [k for k, exp in self._blacklist.items() if now >= exp]
        for k in expired:
            del self._blacklist[k]

    def blacklist_count(self) -> int:
        """Return current number of active (non-expired) blacklist entries."""
        self.cleanup_expired()
        return len(self._blacklist)
