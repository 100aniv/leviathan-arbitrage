"""LEVIATHAN DataQualityManager — Central data quality gateway.

US-286: Unifies StaleOrderbookDetector + HealthChecker under a single entry point.
US-287: Per-exchange/strategy differential freshness thresholds.
US-288: Exchange health score aggregation (min-based).
US-289: Z-score anomaly detection for price outliers.
US-290: Bithumb-specific stale data handling (tighter deviation, blacklist).

Usage::

    dqm = DataQualityManager()
    result = dqm.check(exchange, symbol, mid_price, spread_pct, last_update_ts)
    if not result.ok:
        # reject this orderbook update
        ...

    score = dqm.get_health_score("binance")
    scores = dqm.get_all_health_scores()
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from statistics import mean, stdev
from typing import Any

import structlog

from src.core.config import get_settings
from src.core.exchanges import KRW_EXCHANGES
from src.infra.exchange.health_checker import HealthChecker

logger = structlog.get_logger(__name__)


def _op():
    """Return operational settings (deferred to avoid import-time cycles)."""
    return get_settings().operational


# --- Freshness defaults (seconds) ---
_FRESHNESS_FUTURES_S = _op().freshness_futures_s
_FRESHNESS_DEFAULT_S = _op().freshness_default_s
_FRESHNESS_KOREAN_S = _op().freshness_korean_s
_FRESHNESS_BITHUMB_S = _op().freshness_bithumb_s

# --- Bithumb-specific (US-290) ---
_BITHUMB_DEVIATION_PCT = _op().bithumb_deviation_pct
_BITHUMB_LARGE_DEVIATION_MULT = _op().bithumb_large_deviation_mult
_BITHUMB_BLACKLIST_TTL_S = _op().bithumb_blacklist_ttl_s

# --- Anomaly detection (US-289) ---
_ANOMALY_WINDOW = _op().anomaly_window
_ANOMALY_Z_THRESHOLD = _op().anomaly_z_threshold
_ANOMALY_ISOLATION_S = _op().anomaly_isolation_s
_ANOMALY_WARMUP = _op().anomaly_warmup

# --- Exchange classification ---
FUTURES_EXCHANGES = frozenset({"binance_futures", "bybit_futures", "okx_futures", "bitget_futures"})


@dataclass
class DataQualityResult:
    """Result of a data quality check."""

    ok: bool
    score: float  # 0.0 (worst) to 1.0 (best)
    reasons: list[str] = field(default_factory=list)


class AnomalyDetector:
    """Z-score based price anomaly detection (US-289).

    Maintains a rolling window of prices per (exchange, symbol) pair.
    Flags prices that deviate > z_threshold standard deviations from the rolling mean.
    """

    def __init__(
        self,
        window: int = _ANOMALY_WINDOW,
        z_threshold: float = _ANOMALY_Z_THRESHOLD,
        warmup: int = _ANOMALY_WARMUP,
    ) -> None:
        self._window = window
        self._z_threshold = z_threshold
        self._warmup = warmup
        # (exchange, symbol) -> deque of recent prices
        self._prices: dict[tuple[str, str], deque[float]] = {}
        # (exchange, symbol) -> isolation expiry monotonic time
        self._isolated: dict[tuple[str, str], float] = {}

    def update_and_check(
        self, exchange: str, symbol: str, price: float
    ) -> tuple[bool, str]:
        """Update rolling stats and check for anomaly.

        Returns:
            (is_normal, reason) — True if price is normal, False if anomaly.
        """
        key = (exchange, symbol)

        # Check if currently isolated
        now = time.monotonic()
        if key in self._isolated:
            if now < self._isolated[key]:
                return False, f"isolated until {self._isolated[key] - now:.1f}s"
            del self._isolated[key]

        # Get or create price buffer
        if key not in self._prices:
            self._prices[key] = deque(maxlen=self._window)

        buf = self._prices[key]

        # Warmup: not enough data yet — pass through
        if len(buf) < self._warmup:
            buf.append(price)
            return True, "warmup"

        # Compute z-score
        mu = mean(buf)
        sd = stdev(buf) if len(buf) >= 2 else 0.0

        if sd > 0:
            z = abs(price - mu) / sd
        else:
            # All same price — insufficient variance to judge. Pass through.
            buf.append(price)
            return True, "no_variance"

        if z > self._z_threshold:
            # Isolate for _ANOMALY_ISOLATION_S seconds — don't add to buffer
            self._isolated[key] = now + _ANOMALY_ISOLATION_S
            logger.warning(
                "data_quality_anomaly_detected",
                exchange=exchange,
                symbol=symbol,
                price=price,
                mean=round(mu, 6),
                z_score=round(z, 2),
            )
            return False, f"z-score {z:.2f} > {self._z_threshold}"

        # Normal — add to buffer
        buf.append(price)
        return True, ""

    def cleanup(self) -> None:
        """Remove expired isolations."""
        now = time.monotonic()
        expired = [k for k, v in self._isolated.items() if now >= v]
        for k in expired:
            del self._isolated[k]


class DataQualityManager:
    """Central data quality gateway (US-286).

    Integrates:
    - StaleOrderbookDetector (existing) — cross-exchange validation
    - HealthChecker (per-exchange) — health scoring
    - AnomalyDetector (US-289) — z-score outlier detection
    - Differential freshness (US-287) — per-exchange thresholds
    - Bithumb specialization (US-290) — tighter deviation, fast blacklist
    """

    def __init__(self) -> None:
        # Per-exchange HealthChecker instances (lazy init)
        self._health_checkers: dict[str, HealthChecker] = {}
        # Exchanges marked as always-healthy (Paper adapters bypass HealthChecker)
        self._always_healthy: set[str] = set()
        # Anomaly detector
        self._anomaly = AnomalyDetector()
        # Blacklist: (exchange, symbol) -> expiry monotonic time
        self._blacklist: dict[tuple[str, str], float] = {}
        # Last update timestamps: (exchange, symbol) -> monotonic time
        self._last_update: dict[tuple[str, str], float] = {}
        # Counters for monitoring
        self._check_count: int = 0
        self._reject_count: int = 0
        self._blacklist_count: int = 0
        # Bithumb price medians for large-deviation detection (US-290)
        self._bithumb_medians: dict[str, deque[float]] = {}

    # ------------------------------------------------------------------
    # Registration & Health (US-288)
    # ------------------------------------------------------------------

    def register_exchange(self, exchange_id: str, *, always_healthy: bool = False) -> None:
        """Explicitly register an exchange for health tracking.

        Args:
            exchange_id: Exchange identifier.
            always_healthy: If True, bypass HealthChecker and always return 1.0.
                            Use for Paper/synthetic adapters that have no real WS feed.
        """
        if always_healthy:
            self._always_healthy.add(exchange_id)
        else:
            self.get_or_create_health_checker(exchange_id)

    def get_or_create_health_checker(self, exchange_id: str) -> HealthChecker:
        """Get existing or lazily create a HealthChecker for an exchange."""
        if exchange_id not in self._health_checkers:
            self._health_checkers[exchange_id] = HealthChecker(
                exchange_id=exchange_id,
                stale_threshold_seconds=self.get_freshness_threshold(exchange_id),
            )
        return self._health_checkers[exchange_id]

    def get_health_score(self, exchange_id: str) -> float:
        """Get health score for a single exchange (0.0-1.0)."""
        if exchange_id in self._always_healthy:
            return 1.0  # Paper/synthetic adapter — no real WS feed to measure
        checker = self._health_checkers.get(exchange_id)
        if checker is None:
            return 1.0  # optimistic if not registered
        return checker.health_score

    def get_all_health_scores(self) -> dict[str, float]:
        """Get health scores for all registered exchanges."""
        return {
            eid: checker.health_score
            for eid, checker in self._health_checkers.items()
        }

    def aggregate_health_score(self, exchanges: list[str] | None = None) -> float:
        """Aggregate health score — min-based (weakest exchange limits all).

        Args:
            exchanges: Specific exchanges to aggregate. None = all registered.
        """
        targets = exchanges or list(self._health_checkers.keys())
        if not targets:
            return 1.0

        scores = [self.get_health_score(e) for e in targets]
        return min(scores) if scores else 1.0

    # ------------------------------------------------------------------
    # Health recording delegates (US-288)
    # ------------------------------------------------------------------

    def record_heartbeat(self, exchange_id: str) -> None:
        """Record a heartbeat from an exchange (delegates to HealthChecker)."""
        checker = self.get_or_create_health_checker(exchange_id)
        checker.record_heartbeat()

    def record_api_latency(self, exchange_id: str, latency_ms: float) -> None:
        """Record API latency for an exchange."""
        checker = self.get_or_create_health_checker(exchange_id)
        checker.record_api_latency(latency_ms)

    def record_ws_disconnect(self, exchange_id: str) -> None:
        """Record WebSocket disconnect for an exchange."""
        checker = self.get_or_create_health_checker(exchange_id)
        checker.record_ws_disconnect()

    def record_ws_connect(self, exchange_id: str) -> None:
        """Record WebSocket connect for an exchange."""
        checker = self.get_or_create_health_checker(exchange_id)
        checker.record_ws_connect()

    # ------------------------------------------------------------------
    # Freshness (US-287)
    # ------------------------------------------------------------------

    def get_freshness_threshold(self, exchange_id: str) -> float:
        """Get differential freshness threshold for an exchange (seconds).

        - Futures: 0.5s (fast-moving markets)
        - Bithumb: 1.0s (stale-prone, tighter than general Korean)
        - Korean (upbit/coinone): 2.0s
        - Default: 1.0s
        """
        eid_lower = exchange_id.lower()
        if eid_lower in FUTURES_EXCHANGES:
            return _FRESHNESS_FUTURES_S
        if eid_lower == "bithumb":
            return _FRESHNESS_BITHUMB_S
        if eid_lower in KRW_EXCHANGES:
            return _FRESHNESS_KOREAN_S
        return _FRESHNESS_DEFAULT_S

    def check_freshness(
        self, exchange: str, symbol: str, last_update_ts: float | None = None
    ) -> bool:
        """Check if data is fresh enough for this exchange.

        Args:
            exchange: Exchange ID.
            symbol: Trading symbol.
            last_update_ts: Monotonic timestamp of last update. None = use internal tracking.

        Returns:
            True if fresh, False if stale.
        """
        key = (exchange, symbol)
        ts = last_update_ts or self._last_update.get(key)
        if ts is None:
            return True  # No data yet — optimistic

        age = time.monotonic() - ts
        threshold = self.get_freshness_threshold(exchange)
        return age <= threshold

    def record_update(self, exchange: str, symbol: str) -> None:
        """Record that we received a fresh update for (exchange, symbol)."""
        self._last_update[(exchange, symbol)] = time.monotonic()

    # ------------------------------------------------------------------
    # Blacklist management
    # ------------------------------------------------------------------

    def is_blacklisted(self, exchange: str, symbol: str) -> bool:
        """Check if (exchange, symbol) pair is currently blacklisted."""
        key = (exchange, symbol)
        if key not in self._blacklist:
            return False
        if time.monotonic() >= self._blacklist[key]:
            del self._blacklist[key]
            return False
        return True

    def add_blacklist(
        self, exchange: str, symbol: str, ttl_s: float | None = None
    ) -> None:
        """Add (exchange, symbol) to blacklist with TTL."""
        default_ttl = (
            _BITHUMB_BLACKLIST_TTL_S
            if exchange.lower() == "bithumb"
            else 300.0
        )
        ttl = ttl_s if ttl_s is not None else default_ttl
        key = (exchange, symbol)
        self._blacklist[key] = time.monotonic() + ttl
        self._blacklist_count += 1
        logger.info(
            "data_quality_blacklisted",
            exchange=exchange,
            symbol=symbol,
            ttl_s=ttl,
        )

    # ------------------------------------------------------------------
    # Bithumb specialization (US-290)
    # ------------------------------------------------------------------

    def _check_bithumb_deviation(
        self, symbol: str, mid_price: float
    ) -> tuple[bool, str]:
        """Bithumb-specific: detect large price deviations on small-cap coins.

        Uses rolling median and rejects if deviation > BITHUMB_DEVIATION_PCT.
        If deviation > 2x → instant blacklist (TTL 600s).
        """
        if symbol not in self._bithumb_medians:
            self._bithumb_medians[symbol] = deque(maxlen=50)

        buf = self._bithumb_medians[symbol]

        if len(buf) < 5:
            buf.append(mid_price)
            return True, ""

        from statistics import median
        med = median(buf)
        if med <= 0:
            buf.append(mid_price)
            return True, ""

        deviation = abs(mid_price - med) / med

        if deviation > _BITHUMB_LARGE_DEVIATION_MULT:
            # 2x+ deviation → instant blacklist (fake spread)
            self.add_blacklist("bithumb", symbol, _BITHUMB_BLACKLIST_TTL_S)
            logger.warning(
                "data_quality_bithumb_large_deviation",
                symbol=symbol,
                price=mid_price,
                median=round(med, 6),
                deviation_pct=round(deviation * 100, 1),
            )
            return False, f"bithumb {deviation*100:.1f}% deviation (2x+) → blacklisted"

        if deviation > _BITHUMB_DEVIATION_PCT:
            logger.warning(
                "data_quality_bithumb_deviation",
                symbol=symbol,
                price=mid_price,
                median=round(med, 6),
                deviation_pct=round(deviation * 100, 1),
            )
            return False, f"bithumb {deviation*100:.1f}% > {_BITHUMB_DEVIATION_PCT*100:.0f}% threshold"

        # Normal — update buffer
        buf.append(mid_price)
        return True, ""

    # ------------------------------------------------------------------
    # Central check (US-286)
    # ------------------------------------------------------------------

    def check(
        self,
        exchange: str,
        symbol: str,
        mid_price: float,
        spread_pct: float = 0.0,
        last_update_ts: float | None = None,
    ) -> DataQualityResult:
        """Central data quality check — single entry point.

        Runs all quality layers:
        1. Blacklist check
        2. Freshness check (US-287)
        3. Bithumb deviation check (US-290)
        4. Anomaly detection (US-289)

        Args:
            exchange: Exchange ID.
            symbol: Trading symbol.
            mid_price: Current mid-price.
            spread_pct: Current bid-ask spread as fraction.
            last_update_ts: Monotonic timestamp of last orderbook update.

        Returns:
            DataQualityResult with ok, score, and reasons.
        """
        self._check_count += 1
        reasons: list[str] = []
        score = 1.0

        # Layer 0: Blacklist (before recording update — don't refresh timestamp for blacklisted)
        if self.is_blacklisted(exchange, symbol):
            self._reject_count += 1
            return DataQualityResult(ok=False, score=0.0, reasons=["blacklisted"])

        # Snapshot previous update time BEFORE recording current
        prev_ts = self._last_update.get((exchange, symbol))
        self.record_update(exchange, symbol)

        # Layer 1: Freshness (US-287) — check gap since previous update
        effective_ts = last_update_ts if last_update_ts is not None else prev_ts
        if effective_ts is not None and not self.check_freshness(exchange, symbol, effective_ts):
            reasons.append("stale_data")
            score *= 0.3

        # Layer 2: Bithumb specialization (US-290)
        if exchange.lower() == "bithumb" and mid_price > 0:
            bithumb_ok, bithumb_reason = self._check_bithumb_deviation(
                symbol, mid_price
            )
            if not bithumb_ok:
                self._reject_count += 1
                return DataQualityResult(
                    ok=False, score=0.0, reasons=[bithumb_reason]
                )

        # Layer 3: Anomaly detection (US-289)
        if mid_price > 0:
            anomaly_ok, anomaly_reason = self._anomaly.update_and_check(
                exchange, symbol, mid_price
            )
            if not anomaly_ok:
                self._reject_count += 1
                return DataQualityResult(
                    ok=False, score=0.0, reasons=[f"anomaly: {anomaly_reason}"]
                )

        # Layer 4: Health score factor
        health = self.get_health_score(exchange)
        score *= health

        ok = score > 0.3 and not reasons
        if not ok:
            self._reject_count += 1

        return DataQualityResult(ok=ok, score=round(score, 4), reasons=reasons)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup_expired(self) -> int:
        """Remove expired blacklist entries and anomaly isolations.

        Returns:
            Number of entries cleaned up.
        """
        now = time.monotonic()
        expired_bl = [k for k, v in self._blacklist.items() if now >= v]
        for k in expired_bl:
            del self._blacklist[k]
        self._anomaly.cleanup()
        return len(expired_bl)

    # ------------------------------------------------------------------
    # Stats / Monitoring
    # ------------------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        """Return monitoring stats."""
        return {
            "check_count": self._check_count,
            "reject_count": self._reject_count,
            "blacklist_count": self._blacklist_count,
            "active_blacklist": len(self._blacklist),
            "registered_exchanges": list(self._health_checkers.keys()),
            "health_scores": self.get_all_health_scores(),
        }
