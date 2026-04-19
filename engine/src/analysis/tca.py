"""Transaction Cost Analysis (TCA) — Implementation Shortfall + Latency + Fill Rate.

US-116: Measures execution quality via rolling percentile windows.
US-329: Arrival Price, Timing decomposition, Per-strategy breakdown.
WS-D3: RollingPerformance — 30-day rolling Sharpe + Max Drawdown.
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


@dataclass
class ExecutionRecord:
    """Single execution measurement."""
    expected_price: float
    fill_price: float
    latency_ms: float
    filled_ratio: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    strategy_id: str = ""
    # US-329: Arrival price + timing decomposition
    arrival_price: float = 0.0  # price at signal generation time
    signal_ts: float = 0.0      # signal generation timestamp (epoch)
    decision_ts: float = 0.0    # strategy decision timestamp
    submission_ts: float = 0.0  # order submission timestamp
    fill_ts: float = 0.0        # fill confirmation timestamp


class PercentileTracker:
    """Rolling window percentile calculator (no numpy dependency)."""

    def __init__(self, window_size: int = 1000) -> None:
        self._data: deque[float] = deque(maxlen=window_size)

    def add(self, value: float) -> None:
        self._data.append(value)

    def percentile(self, pct: float) -> float:
        if not self._data:
            return 0.0
        sorted_data = sorted(self._data)
        idx = (pct / 100.0) * (len(sorted_data) - 1)
        lower = int(idx)
        upper = min(lower + 1, len(sorted_data) - 1)
        frac = idx - lower
        return sorted_data[lower] * (1 - frac) + sorted_data[upper] * frac

    @property
    def count(self) -> int:
        return len(self._data)


class TCAAnalyzer:
    """Transaction Cost Analysis — tracks IS, latency, and fill rate."""

    def __init__(self, window_size: int = 1000) -> None:
        self._is_tracker = PercentileTracker(window_size)
        self._latency_tracker = PercentileTracker(window_size)
        self._fill_rates: deque[float] = deque(maxlen=window_size)
        self._records: deque[ExecutionRecord] = deque(maxlen=window_size)
        self._window_size = window_size
        # US-329: per-strategy trackers
        self._strategy_is: dict[str, PercentileTracker] = defaultdict(
            lambda: PercentileTracker(window_size)
        )
        self._strategy_latency: dict[str, PercentileTracker] = defaultdict(
            lambda: PercentileTracker(window_size)
        )
        self._strategy_fills: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=window_size)
        )
        # US-329: timing decomposition trackers
        self._signal_to_fill_tracker = PercentileTracker(window_size)
        self._signal_to_decision_tracker = PercentileTracker(window_size)
        self._decision_to_submit_tracker = PercentileTracker(window_size)
        self._submit_to_fill_tracker = PercentileTracker(window_size)

    def record_execution(
        self,
        expected_price: float,
        fill_price: float,
        latency_ms: float,
        filled_ratio: float,
        strategy_id: str = "",
        arrival_price: float = 0.0,
        signal_ts: float = 0.0,
        decision_ts: float = 0.0,
        submission_ts: float = 0.0,
        fill_ts: float = 0.0,
    ) -> None:
        if expected_price <= 0:
            return
        is_bps = abs(fill_price - expected_price) / expected_price * 10_000
        self._is_tracker.add(is_bps)
        self._latency_tracker.add(max(0.0, latency_ms))
        self._fill_rates.append(max(0.0, min(1.0, filled_ratio)))
        self._records.append(ExecutionRecord(
            expected_price=expected_price,
            fill_price=fill_price,
            latency_ms=latency_ms,
            filled_ratio=filled_ratio,
            strategy_id=strategy_id,
            arrival_price=arrival_price,
            signal_ts=signal_ts,
            decision_ts=decision_ts,
            submission_ts=submission_ts,
            fill_ts=fill_ts,
        ))
        # US-329: per-strategy tracking
        if strategy_id:
            self._strategy_is[strategy_id].add(is_bps)
            self._strategy_latency[strategy_id].add(max(0.0, latency_ms))
            self._strategy_fills[strategy_id].append(max(0.0, min(1.0, filled_ratio)))
        # US-329: timing decomposition (non-negative validation for clock skew)
        if signal_ts > 0 and fill_ts > signal_ts:
            self._signal_to_fill_tracker.add((fill_ts - signal_ts) * 1000)
        if signal_ts > 0 and decision_ts > signal_ts:
            self._signal_to_decision_tracker.add((decision_ts - signal_ts) * 1000)
        if decision_ts > 0 and submission_ts > decision_ts:
            self._decision_to_submit_tracker.add((submission_ts - decision_ts) * 1000)
        if submission_ts > 0 and fill_ts > submission_ts:
            self._submit_to_fill_tracker.add((fill_ts - submission_ts) * 1000)

    def get_summary(self) -> dict:
        fill_rate = (
            sum(self._fill_rates) / len(self._fill_rates) * 100
            if self._fill_rates else 0.0
        )
        result = {
            "is_p50_bps": round(self._is_tracker.percentile(50), 2),
            "is_p95_bps": round(self._is_tracker.percentile(95), 2),
            "latency_p50_ms": round(self._latency_tracker.percentile(50), 1),
            "latency_p95_ms": round(self._latency_tracker.percentile(95), 1),
            "latency_p99_ms": round(self._latency_tracker.percentile(99), 1),
            "fill_rate_pct": round(fill_rate, 1),
            "sample_count": self._is_tracker.count,
        }
        # US-329: timing decomposition
        if self._signal_to_fill_tracker.count > 0:
            result["timing"] = {
                "signal_to_fill_p50_ms": round(self._signal_to_fill_tracker.percentile(50), 1),
                "signal_to_fill_p95_ms": round(self._signal_to_fill_tracker.percentile(95), 1),
                "signal_to_decision_p50_ms": round(self._signal_to_decision_tracker.percentile(50), 1),
                "decision_to_submit_p50_ms": round(self._decision_to_submit_tracker.percentile(50), 1),
                "submit_to_fill_p50_ms": round(self._submit_to_fill_tracker.percentile(50), 1),
            }
        return result

    def get_strategy_summary(self, strategy_id: str) -> dict:
        """US-329: Per-strategy TCA breakdown."""
        is_t = self._strategy_is.get(strategy_id)
        lat_t = self._strategy_latency.get(strategy_id)
        fills = self._strategy_fills.get(strategy_id)
        if is_t is None or is_t.count == 0:
            return {"error": f"No data for strategy {strategy_id}"}
        fill_rate = sum(fills) / len(fills) * 100 if fills else 0.0
        return {
            "strategy_id": strategy_id,
            "is_p50_bps": round(is_t.percentile(50), 2),
            "is_p95_bps": round(is_t.percentile(95), 2),
            "latency_p50_ms": round(lat_t.percentile(50), 1),
            "latency_p95_ms": round(lat_t.percentile(95), 1),
            "fill_rate_pct": round(fill_rate, 1),
            "sample_count": is_t.count,
        }

    def get_all_strategy_summaries(self) -> dict[str, dict]:
        """US-329: All strategies at once."""
        return {sid: self.get_strategy_summary(sid) for sid in self._strategy_is}


# ---------------------------------------------------------------------------
# WS-D3: 30-day rolling performance (Sharpe + MDD)
# ---------------------------------------------------------------------------


@dataclass
class _DailyReturn:
    date: datetime
    pnl_usd: float  # realized PnL for the UTC day
    equity: float   # cumulative equity AFTER this day closed


class RollingPerformance:
    """Maintains a 30-day rolling PnL series and exposes Sharpe + MDD.

    Inputs:
      - ``record_pnl(pnl_usd)``: accumulate PnL into the current UTC day.
      - ``roll_day(now_utc)``: close the current day's bucket and start a new
        one. Called by live.py at UTC 00:00 or when a new day is detected.

    Outputs:
      - ``sharpe_30d()``: annualized (×√365) Sharpe using daily returns.
      - ``mdd_30d_pct()``: max drawdown percentage across the rolling window.

    Empty/insufficient data returns 0.0 — callers can still push the gauge
    without special-casing cold start.
    """

    WINDOW_DAYS = 30
    PERIODS_PER_YEAR = 365

    def __init__(self, initial_equity: float = 0.0, window_days: int = WINDOW_DAYS) -> None:
        self._window_days = window_days
        self._history: deque[_DailyReturn] = deque(maxlen=window_days)
        self._current_day: datetime | None = None
        self._current_pnl: float = 0.0
        self._running_equity: float = float(initial_equity)

    # -----------------------------------------------------------------------
    # Ingestion
    # -----------------------------------------------------------------------

    def record_pnl(self, pnl_usd: float, now_utc: datetime | None = None) -> None:
        """Add PnL to the current UTC day bucket. Auto-rolls if the day advanced."""
        now = now_utc or datetime.now(timezone.utc)
        day = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        if self._current_day is None:
            self._current_day = day
        elif day > self._current_day:
            # A UTC day rolled over while we weren't looking — commit the
            # pending day and start a new bucket before accumulating.
            self._commit_current()
            self._current_day = day
        self._current_pnl += float(pnl_usd)

    def roll_day(self, now_utc: datetime | None = None) -> None:
        """Explicitly close the current day and start a fresh one (scheduler path)."""
        now = now_utc or datetime.now(timezone.utc)
        day = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        if self._current_day is None:
            self._current_day = day
            return
        self._commit_current()
        self._current_day = day

    def _commit_current(self) -> None:
        if self._current_day is None:
            return
        self._running_equity += self._current_pnl
        self._history.append(
            _DailyReturn(
                date=self._current_day,
                pnl_usd=self._current_pnl,
                equity=self._running_equity,
            )
        )
        self._current_pnl = 0.0

    # -----------------------------------------------------------------------
    # Metrics
    # -----------------------------------------------------------------------

    def _daily_returns(self) -> list[float]:
        """Daily return fractions relative to prior equity. Cold-start uses pnl/1."""
        out: list[float] = []
        prev_equity = self._history[0].equity - self._history[0].pnl_usd if self._history else 0.0
        for rec in self._history:
            base = prev_equity if prev_equity not in (0.0, 0) else 1.0
            out.append(rec.pnl_usd / base)
            prev_equity = rec.equity
        return out

    def sharpe_30d(self, risk_free: float = 0.0) -> float:
        """Annualized Sharpe. Requires >= 2 committed days (std needs >=2 samples)."""
        if len(self._history) < 2:
            return 0.0
        returns = self._daily_returns()
        n = len(returns)
        mean = sum(returns) / n
        var = sum((r - mean) ** 2 for r in returns) / n
        std = math.sqrt(var)
        if std == 0:
            return 0.0
        return ((mean - risk_free) / std) * math.sqrt(self.PERIODS_PER_YEAR)

    def mdd_30d_pct(self) -> float:
        """Max drawdown as a positive percentage (0-100)."""
        if not self._history:
            return 0.0
        peak = self._history[0].equity
        max_dd = 0.0
        for rec in self._history:
            if rec.equity > peak:
                peak = rec.equity
            if peak > 0:
                dd = (peak - rec.equity) / peak
                if dd > max_dd:
                    max_dd = dd
        return max_dd * 100.0

    def push_metrics(self) -> None:
        """Emit Prometheus gauges. Safe to call every tick — graceful on missing metrics."""
        try:
            from src.infra.metrics import SHARPE_30D, MDD_30D_PCT
            SHARPE_30D.set(self.sharpe_30d())
            MDD_30D_PCT.set(self.mdd_30d_pct())
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # Introspection (tests + dashboards)
    # -----------------------------------------------------------------------

    @property
    def history(self) -> list[_DailyReturn]:
        return list(self._history)

    @property
    def current_equity(self) -> float:
        return self._running_equity + self._current_pnl
