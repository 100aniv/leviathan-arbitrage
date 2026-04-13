"""BUG-76: FF adaptive exit threshold must NOT override static exit.

Root cause: AdaptiveThreshold.exit_percentile=50.0 computes p50 of ALL observed
spreads. In an elevated-spread regime (10-50bps market), p50 ≈ 34-37bps, which
exceeds min_spread_bps=27bps. Every newly opened position immediately triggers
the exit condition (current_spread ≤ p50), closing at a guaranteed loss.

Fix: Both exit_threshold_bps paths in futures_futures.py use static only:
    _exit_threshold_bps = min_spread_bps * 0.15  (~4bps)
and never override with adaptive.thresholds[1].
"""
from __future__ import annotations

import inspect
import math
from collections import deque
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Minimal AdaptiveThreshold reimplementation for unit testing
# ---------------------------------------------------------------------------


@dataclass
class _AdaptiveThreshold:
    """Simplified replica of src.core.adaptive_threshold.AdaptiveThreshold."""

    static_entry: float = 60.0
    static_exit: float = 30.0
    entry_percentile: float = 95.0
    exit_percentile: float = 50.0
    min_samples: int = 60
    max_entry_multiplier: float = 2.0
    window: int = 1440
    _observations: deque = field(default_factory=lambda: deque(maxlen=1440), repr=False)

    def update(self, value_bps: float) -> None:
        max_allowed = self.static_entry * self.max_entry_multiplier
        if 0 < value_bps <= max_allowed:
            self._observations.append(value_bps)

    @property
    def is_ready(self) -> bool:
        return len(self._observations) >= self.min_samples

    def _percentile(self, pct: float) -> float:
        data = sorted(self._observations)
        n = len(data)
        if n == 0:
            return 0.0
        k = (pct / 100.0) * (n - 1)
        f = math.floor(k)
        c = min(f + 1, n - 1)
        return data[f] + (k - f) * (data[c] - data[f])

    @property
    def thresholds(self) -> tuple[float, float]:
        if not self.is_ready:
            return (self.static_entry, self.static_exit)
        dynamic_entry = max(self._percentile(self.entry_percentile), self.static_entry)
        dynamic_exit = self._percentile(self.exit_percentile)
        return (dynamic_entry, dynamic_exit)


# ---------------------------------------------------------------------------
# Tests: verify the p50 problem exists in a live-like scenario
# ---------------------------------------------------------------------------


class TestAdaptiveExitProblem:
    """Confirm that naive adaptive exit causes immediate-close bug."""

    def _make_ready_threshold(self, spread_range=(27, 50)) -> _AdaptiveThreshold:
        """Create a threshold with 120 observations spanning spread_range."""
        at = _AdaptiveThreshold()
        lo, hi = spread_range
        for i in range(120):
            at.update(lo + (hi - lo) * (i / 119))
        assert at.is_ready
        return at

    def test_p50_exceeds_min_spread_in_elevated_market(self):
        """In a 27-50bps market, p50 ≈ 38bps which exceeds min_spread_bps=27."""
        at = self._make_ready_threshold(spread_range=(27, 50))
        _, p50_exit = at.thresholds
        assert p50_exit > 27.0, (
            f"p50={p50_exit:.1f} should exceed min_spread_bps=27 "
            "to demonstrate the bug scenario"
        )

    def test_naive_exit_triggers_immediately_after_entry(self):
        """If we used adaptive p50 as exit threshold, a 27bps entry would exit instantly."""
        at = self._make_ready_threshold(spread_range=(27, 50))
        _, p50_exit = at.thresholds

        # A position entered at exactly min_spread_bps = 27bps
        entry_spread = 27.0
        # Bug: current_spread (27bps) <= p50 (38bps) → would exit immediately
        would_exit_immediately = entry_spread <= p50_exit
        assert would_exit_immediately, (
            "This confirms BUG-76: naive adaptive exit causes immediate close"
        )

    def test_static_exit_never_triggers_at_entry_spread(self):
        """With static exit (min_spread * 0.15), a 27bps entry does NOT immediately exit."""
        min_spread_bps = 27.0
        static_exit = min_spread_bps * 0.15  # 4.05bps

        entry_spread = 27.0
        # Correct: 27bps > 4.05bps → condition false → no immediate exit
        assert entry_spread > static_exit, (
            "Static exit threshold is below entry spread → no immediate exit"
        )


# ---------------------------------------------------------------------------
# Tests: verify the FIX is present in futures_futures.py source
# ---------------------------------------------------------------------------


class TestBug76FixInSource:
    def _get_ff_source(self) -> str:
        try:
            from src.strategies.futures_futures import FuturesFuturesStrategy
            return inspect.getsource(FuturesFuturesStrategy)
        except ImportError:
            import pathlib
            p = pathlib.Path(__file__).parent.parent.parent / "src/strategies/futures_futures.py"
            return p.read_text()

    def test_adaptive_exit_override_removed(self):
        """futures_futures.py must NOT contain adaptive p50 override for exit threshold."""
        source = self._get_ff_source()
        assert "BUG-76" in source, "BUG-76 fix comment not found in futures_futures.py"
        # The override code must NOT be present
        assert "_at_exit_m" not in source, (
            "_at_exit_m (adaptive exit override) still present — BUG-76 fix not applied"
        )
        assert "_at_exit = self._adaptive_threshold.thresholds" not in source, (
            "Adaptive exit threshold fetch still present — BUG-76 fix not applied"
        )

    def test_static_exit_formula_present(self):
        """The static exit (min_spread_bps * 0.15) must be the only exit threshold assignment."""
        source = self._get_ff_source()
        assert "min_spread_bps) * 0.15" in source, (
            "Static exit formula 'min_spread_bps * 0.15' not found"
        )

    def test_adaptive_entry_outlier_cap_still_present(self):
        """Adaptive threshold is still used for ENTRY outlier cap (p95). Must not remove it."""
        source = self._get_ff_source()
        # Outlier cap for entry uses thresholds[0] — should still be there
        assert "_outlier_cap" in source or "adaptive_threshold" in source, (
            "Adaptive threshold for entry outlier cap must remain"
        )


# ---------------------------------------------------------------------------
# Tests: correct static exit threshold values
# ---------------------------------------------------------------------------


class TestStaticExitThresholdValues:
    def test_static_exit_below_entry_gate(self):
        """static_exit = min_spread * 0.15 must be less than min_spread (27bps)."""
        min_spread_bps = 27.0
        static_exit = min_spread_bps * 0.15
        assert static_exit < min_spread_bps, (
            f"static_exit={static_exit:.2f} must be < min_spread={min_spread_bps}"
        )

    def test_static_exit_ensures_profitability(self):
        """At minimum entry (27bps), closing at static exit (4.05bps) must cover round-trip fees.

        Round-trip fees = ENTRY fees + EXIT fees:
          Entry: Binance taker 5bps + Bitget taker 6bps = 11bps
          Exit:  Binance taker 5bps + Bitget taker 6bps = 11bps
          Total: 22bps

        At 27bps min entry: gross_capture = 27 - 4.05 = 22.95bps > 22bps → 0.95bps net profit.
        Tight but valid. Higher-spread entries (30-40bps) give significantly more margin.
        """
        min_spread_bps = 27.0
        static_exit = min_spread_bps * 0.15  # 4.05bps
        entry_fees_bps = 11.0   # Binance 5bps + Bitget 6bps (entry trade)
        exit_fees_bps = 11.0    # Binance 5bps + Bitget 6bps (exit trade)
        total_round_trip_fees_bps = entry_fees_bps + exit_fees_bps  # 22bps

        gross_capture = min_spread_bps - static_exit  # 27 - 4.05 = 22.95bps
        net_profit_bps = gross_capture - total_round_trip_fees_bps  # 22.95 - 22 = 0.95bps

        assert net_profit_bps > 0, (
            f"Static exit threshold must be profitable at min entry: "
            f"gross={gross_capture:.2f}bps fees={total_round_trip_fees_bps:.0f}bps net={net_profit_bps:.2f}bps"
        )
