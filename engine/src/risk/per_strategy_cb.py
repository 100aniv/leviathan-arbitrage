"""Per-strategy circuit breaker state machine (US-222 + US-228).

States
------
ACTIVE      — normal operation
THROTTLED   — reduced activity; composite score > 0.5 or 3 consecutive losses
HALTED      — fully blocked; composite score > 0.7; auto-recover after 300s if score < 0.3
SUSPENDED   — blocked for 1 hour; triggered by 3x HALTED events within 1 hour

Composite score = 0.4*DD + 0.3*loss_rate + 0.2*spread_anomaly + 0.1*rejection_rate

Cold start: strategies with < 20 trades always stay ACTIVE (protection disabled).
"""
from __future__ import annotations

import enum
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict

logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter, Gauge

    _STRATEGY_CB_STATE = Gauge(
        "leviathan_strategy_cb_state",
        "Per-strategy circuit breaker state (0=ACTIVE,1=THROTTLED,2=HALTED,3=SUSPENDED)",
        ["strategy"],
    )
    _STRATEGY_CB_TRANSITIONS = Counter(
        "leviathan_strategy_cb_transitions_total",
        "Per-strategy circuit breaker state transitions",
        ["strategy", "from_state", "to_state"],
    )
    _HAS_PROM = True
except Exception:
    _HAS_PROM = False

# Score thresholds
_SCORE_THROTTLE = 0.5
_SCORE_HALT = 0.7
_SCORE_RECOVER = 0.3

# Timing
_SUSPENDED_DURATION_S = 3600.0  # 1 hour auto-recovery
_HALTED_DURATION_S = 300.0  # cooldown before HALTED → ACTIVE
_HALTED_COUNT_WINDOW_S = 3600.0  # window for 3x HALTED → SUSPENDED
_HALTED_COUNT_THRESHOLD = 3  # events within window → SUSPENDED

# US-222: 3 consecutive losses → THROTTLED (300s cooldown maps to HALTED state)
_CONSECUTIVE_LOSS_THRESHOLD = 3

# Cold start guard
_COLD_START_TRADES = 20


class CBState(enum.Enum):
    ACTIVE = 0
    THROTTLED = 1
    HALTED = 2
    SUSPENDED = 3


@dataclass
class _StrategyMetrics:
    """Rolling metrics for composite score computation."""

    peak_pnl: float = 0.0
    current_pnl: float = 0.0
    # win=1, loss=0 over last 20 trades
    recent_results: deque = field(default_factory=lambda: deque(maxlen=20))
    # spread_bps values over last 20 trades
    recent_spreads: deque = field(default_factory=lambda: deque(maxlen=20))
    # approved=1, rejected=0 over last 20 proposals
    recent_proposals: deque = field(default_factory=lambda: deque(maxlen=20))
    consecutive_losses: int = 0
    total_trades: int = 0


@dataclass
class _CBEntry:
    state: CBState = CBState.ACTIVE
    metrics: _StrategyMetrics = field(default_factory=_StrategyMetrics)
    halted_at: float = 0.0
    suspended_at: float = 0.0
    # timestamps of each HALTED event (for SUSPENDED trigger)
    halted_events: deque = field(default_factory=lambda: deque(maxlen=10))


class PerStrategyCB:
    """
    Per-strategy circuit breaker.

    Usage
    -----
    cb = PerStrategyCB()
    # After each trade:
    cb.record_trade("cross_exchange", pnl=-5.0, spread_bps=12.0)
    # After each proposal (approved or rejected):
    cb.record_proposal("cross_exchange", rejected=False)
    # Before routing a signal:
    if not cb.is_allowed("cross_exchange"):
        return  # strategy blocked
    """

    def __init__(self) -> None:
        self._strategies: Dict[str, _CBEntry] = {}

    # ------------------------------------------------------------------
    # Public query interface
    # ------------------------------------------------------------------

    def is_allowed(self, strategy_id: str) -> bool:
        """True if the strategy may trade (ACTIVE or THROTTLED)."""
        entry = self._entry(strategy_id)
        self._maybe_auto_recover(strategy_id, entry)
        return entry.state in (CBState.ACTIVE, CBState.THROTTLED)

    def is_throttled(self, strategy_id: str) -> bool:
        """True if strategy is in THROTTLED state."""
        entry = self._entry(strategy_id)
        self._maybe_auto_recover(strategy_id, entry)
        return entry.state == CBState.THROTTLED

    def state(self, strategy_id: str) -> CBState:
        """Return current CBState for a strategy."""
        entry = self._entry(strategy_id)
        self._maybe_auto_recover(strategy_id, entry)
        return entry.state

    def compute_score(self, strategy_id: str) -> float:
        """Composite score in [0, 1]. Returns 0.0 during cold start."""
        entry = self._entry(strategy_id)
        m = entry.metrics

        if m.total_trades < _COLD_START_TRADES:
            return 0.0

        # Component 1: drawdown [0, 1]
        if m.peak_pnl > 0:
            dd = max(0.0, (m.peak_pnl - m.current_pnl) / m.peak_pnl)
        else:
            dd = 0.0
        dd = min(1.0, dd)

        # Component 2: loss rate over recent 20 trades
        if m.recent_results:
            loss_rate = 1.0 - (sum(m.recent_results) / len(m.recent_results))
        else:
            loss_rate = 0.0

        # Component 3: fraction of spreads > 2x median (anomaly)
        spread_anomaly = 0.0
        if len(m.recent_spreads) >= 5:
            spreads = sorted(m.recent_spreads)
            median = spreads[len(spreads) // 2]
            if median > 0:
                anomalous = sum(1 for s in spreads if s > 2 * median)
                spread_anomaly = anomalous / len(spreads)

        # Component 4: rejection rate
        if m.recent_proposals:
            rejection_rate = 1.0 - (sum(m.recent_proposals) / len(m.recent_proposals))
        else:
            rejection_rate = 0.0

        score = (
            0.4 * dd
            + 0.3 * loss_rate
            + 0.2 * spread_anomaly
            + 0.1 * rejection_rate
        )
        return min(1.0, max(0.0, score))

    # ------------------------------------------------------------------
    # Public mutation interface
    # ------------------------------------------------------------------

    def record_trade(
        self,
        strategy_id: str,
        pnl: float,
        spread_bps: float | None = None,
    ) -> None:
        """Record a completed trade and re-evaluate state transitions."""
        entry = self._entry(strategy_id)
        m = entry.metrics
        m.total_trades += 1

        m.current_pnl += pnl
        if m.current_pnl > m.peak_pnl:
            m.peak_pnl = m.current_pnl

        is_loss = pnl < 0
        m.recent_results.append(0 if is_loss else 1)
        m.consecutive_losses = m.consecutive_losses + 1 if is_loss else 0

        if spread_bps is not None:
            m.recent_spreads.append(spread_bps)

        self._evaluate_transitions(strategy_id, entry)

    def record_proposal(self, strategy_id: str, rejected: bool) -> None:
        """Record a trade proposal outcome for rejection-rate tracking."""
        entry = self._entry(strategy_id)
        entry.metrics.recent_proposals.append(0 if rejected else 1)

    # ------------------------------------------------------------------
    # Aggregate queries (for global CB integration)
    # ------------------------------------------------------------------

    def halted_count(self) -> int:
        """Number of strategies currently HALTED or SUSPENDED."""
        return sum(
            1 for e in self._strategies.values()
            if e.state in (CBState.HALTED, CBState.SUSPENDED)
        )

    def active_count(self) -> int:
        """Number of strategies currently ACTIVE or THROTTLED."""
        return sum(
            1 for e in self._strategies.values()
            if e.state in (CBState.ACTIVE, CBState.THROTTLED)
        )

    def all_states(self) -> dict[str, str]:
        """Return {strategy_id: state_name} for all tracked strategies."""
        return {sid: e.state.name for sid, e in self._strategies.items()}

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _entry(self, strategy_id: str) -> _CBEntry:
        if strategy_id not in self._strategies:
            self._strategies[strategy_id] = _CBEntry()
        return self._strategies[strategy_id]

    def _evaluate_transitions(self, strategy_id: str, entry: _CBEntry) -> None:
        m = entry.metrics
        if m.total_trades < _COLD_START_TRADES:
            return

        score = self.compute_score(strategy_id)
        now = time.monotonic()

        if entry.state == CBState.ACTIVE:
            if score > _SCORE_HALT:
                self._transition(strategy_id, entry, CBState.HALTED, now)
            elif score > _SCORE_THROTTLE or m.consecutive_losses >= _CONSECUTIVE_LOSS_THRESHOLD:
                self._transition(strategy_id, entry, CBState.THROTTLED, now)

        elif entry.state == CBState.THROTTLED:
            if score > _SCORE_HALT:
                self._transition(strategy_id, entry, CBState.HALTED, now)
            elif score < _SCORE_RECOVER and m.consecutive_losses == 0:
                self._transition(strategy_id, entry, CBState.ACTIVE, now)

        elif entry.state == CBState.HALTED:
            # 3x HALTED within 1h window → SUSPENDED
            recent_halts = sum(
                1 for t in entry.halted_events
                if now - t < _HALTED_COUNT_WINDOW_S
            )
            if recent_halts >= _HALTED_COUNT_THRESHOLD:
                self._transition(strategy_id, entry, CBState.SUSPENDED, now)

    def _maybe_auto_recover(self, strategy_id: str, entry: _CBEntry) -> None:
        """Time-based auto-recovery: SUSPENDED→ACTIVE (1h), HALTED→ACTIVE (300s if score<0.3)."""
        now = time.monotonic()
        if entry.state == CBState.SUSPENDED:
            if now - entry.suspended_at >= _SUSPENDED_DURATION_S:
                self._transition(strategy_id, entry, CBState.ACTIVE, now)
        elif entry.state == CBState.HALTED:
            if now - entry.halted_at >= _HALTED_DURATION_S:
                score = self.compute_score(strategy_id)
                if score < _SCORE_RECOVER:
                    self._transition(strategy_id, entry, CBState.ACTIVE, now)

    def _transition(
        self,
        strategy_id: str,
        entry: _CBEntry,
        new_state: CBState,
        now: float,
    ) -> None:
        old_state = entry.state
        if old_state == new_state:
            return

        entry.state = new_state
        if new_state == CBState.HALTED:
            entry.halted_at = now
            entry.halted_events.append(now)
        elif new_state == CBState.SUSPENDED:
            entry.suspended_at = now
        elif new_state == CBState.ACTIVE:
            entry.metrics.consecutive_losses = 0

        logger.warning(
            "per_strategy_cb.transition: strategy=%s %s→%s",
            strategy_id,
            old_state.name,
            new_state.name,
        )

        if _HAS_PROM:
            try:
                _STRATEGY_CB_STATE.labels(strategy=strategy_id).set(new_state.value)
                _STRATEGY_CB_TRANSITIONS.labels(
                    strategy=strategy_id,
                    from_state=old_state.name,
                    to_state=new_state.name,
                ).inc()
            except Exception:
                pass
