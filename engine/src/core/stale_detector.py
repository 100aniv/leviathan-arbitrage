"""Stale Orderbook Detector — cross-exchange price validation + blacklist.

US-066 Phase G: Defense-in-depth against Korean exchange stale orderbook data.

Three responsibilities:
1. Cross-exchange price validation: compare mid-price against non-self median.
   For Korean exchanges, median uses only non-Korean exchanges.
2. Blacklist management: TTL-based (exchange, symbol) pair blacklisting.
3. Prometheus counters for observability.
"""
from __future__ import annotations

import os
import time
from statistics import median
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


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

    def add_blacklist(self, exchange: str, symbol: str) -> None:
        """Blacklist (exchange, symbol) for TTL seconds."""
        key = (exchange, symbol)
        expiry = time.monotonic() + self._blacklist_ttl_s
        self._blacklist[key] = expiry
        logger.info(
            "stale_detector.blacklist_added",
            exchange=exchange,
            symbol=symbol,
            ttl_s=self._blacklist_ttl_s,
        )

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
