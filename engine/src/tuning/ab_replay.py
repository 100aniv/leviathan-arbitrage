"""A/B Replay Infrastructure for Auto-Tuner Parameter Comparison (US-200).

Provides deterministic replay of recorded trading events under two
different parameter sets, enabling offline comparison of tuner effects.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ReplayEvent:
    """A single recorded trading event (signal + optional fill)."""

    timestamp_ms: int
    strategy_id: str
    symbol: str
    signal_edge_bps: float
    threshold_bps: float  # active threshold at time of signal
    filled: bool = False
    fill_pnl_usd: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ABResult:
    """Comparison result of two parameter sets replayed on same events."""

    params_a: dict[str, Any]
    params_b: dict[str, Any]
    total_events: int
    # Set A results
    a_trades: int = 0
    a_pnl: float = 0.0
    a_win_rate: float = 0.0
    # Set B results
    b_trades: int = 0
    b_pnl: float = 0.0
    b_win_rate: float = 0.0
    # Comparison
    pnl_diff: float = 0.0  # b_pnl - a_pnl
    wr_diff: float = 0.0   # b_win_rate - a_win_rate

    @property
    def b_is_better(self) -> bool:
        return self.pnl_diff > 0


def _replay_with_threshold(events: list[ReplayEvent], threshold_bps: float) -> tuple[int, float, float]:
    """Replay events with a fixed threshold, return (trades, pnl, win_rate)."""
    trades = 0
    pnl = 0.0
    wins = 0

    for evt in events:
        # A signal is "traded" if its edge exceeds the threshold
        if evt.signal_edge_bps >= threshold_bps:
            trades += 1
            pnl += evt.fill_pnl_usd
            if evt.fill_pnl_usd > 0:
                wins += 1

    win_rate = (wins / trades * 100) if trades > 0 else 0.0
    return trades, pnl, win_rate


def replay_session(
    events: list[ReplayEvent],
    params_a: dict[str, Any],
    params_b: dict[str, Any],
) -> ABResult:
    """Deterministic replay of events under two parameter sets.

    Currently supports threshold_bps comparison. Extensible to other
    parameters (e.g., max_holding_bars, zscore_entry) in future phases.

    Args:
        events: List of recorded ReplayEvent objects.
        params_a: Parameter set A (baseline). Must contain 'threshold_bps'.
        params_b: Parameter set B (candidate). Must contain 'threshold_bps'.

    Returns:
        ABResult with comparison metrics.
    """
    threshold_a = float(params_a.get("threshold_bps", 10.0))
    threshold_b = float(params_b.get("threshold_bps", 10.0))

    a_trades, a_pnl, a_wr = _replay_with_threshold(events, threshold_a)
    b_trades, b_pnl, b_wr = _replay_with_threshold(events, threshold_b)

    result = ABResult(
        params_a=params_a,
        params_b=params_b,
        total_events=len(events),
        a_trades=a_trades,
        a_pnl=a_pnl,
        a_win_rate=a_wr,
        b_trades=b_trades,
        b_pnl=b_pnl,
        b_win_rate=b_wr,
        pnl_diff=b_pnl - a_pnl,
        wr_diff=b_wr - a_wr,
    )

    logger.info(
        "AB replay completed: A(%.1fbps)=%d trades/$%.2f vs B(%.1fbps)=%d trades/$%.2f, diff=$%.2f",
        threshold_a, a_trades, a_pnl,
        threshold_b, b_trades, b_pnl,
        result.pnl_diff,
    )

    return result
