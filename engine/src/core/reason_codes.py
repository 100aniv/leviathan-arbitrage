"""Phoenix Path-B Day-2: Controlled reason-code vocabulary for pre-trade rejections.

Every trade rejection path in the Live engine must emit one of these codes via
`PreTradeValidator` so telemetry (Prometheus counter `leviathan_signal_rejected_total`)
and structured logs (`live_mode.rejected_by_<code>`) stay consistent across
refactors.

BUG-227 lineage: four gates (symbol_cooldown, strategy_cooldown, dedup_blocked,
risk_rejected) were silently rolling back at DEBUG level, hiding FF/FR entry
blockers in canary v233. Centralising the vocabulary prevents a recurrence —
adding a new gate requires a new ReasonCode, which in turn requires a label
on the counter and a log tag.
"""
from __future__ import annotations

from enum import Enum


class ReasonCode(str, Enum):
    """Stable vocabulary for every pre-trade rejection path.

    Values are snake_case strings so they can be used directly as Prometheus
    counter labels and structured log tags. Order of declaration mirrors the
    fail-fast evaluation order in `PreTradeValidator.validate()`.
    """

    STRATEGY_FILTERED = "strategy_filtered"
    """Strategy ID is not in the allowlist (engine.json strategy_filter)."""

    STRATEGY_COOLDOWN = "strategy_cooldown"
    """Strategy hit max_loss_per_trade and is in single_loss_disable window (US-164)."""

    KILL_SWITCH_HALT = "kill_switch_halt"
    """Global halt flag is set (threading.Event + Redis SET) — Tier-1 kill switch."""

    CIRCUIT_BREAKER_OPEN = "circuit_breaker_open"
    """CircuitBreaker is OPEN — 300s cooldown after consecutive failures."""

    RATE_LIMITED = "rate_limited"
    """Per-exchange TokenBucket (5 rps, 10 burst) is drained — backoff required."""

    FLASH_GUARD_BLOCKED = "flash_guard_blocked"
    """FlashGuard detected toxic microstructure (sudden price gap / flash crash)."""

    SESSION_LOSS_LIMIT = "session_loss_limit"
    """Cumulative session loss exceeded live.max_daily_loss_pct — engine halted."""

    RISK_GUARDIAN_REJECTED = "risk_guardian_rejected"
    """RiskGuardian 11-check ensemble vetoed the trade (position/drawdown/exposure/etc)."""

    SYMBOL_COOLDOWN = "symbol_cooldown"
    """Same symbol traded within execution.symbol_cooldown_s window."""

    MARGIN_INSUFFICIENT = "margin_insufficient"
    """Futures leg cached margin below _MIN_MARGIN_ENTRY_USD (BUG-74 soft block)."""

    NOTIONAL_BELOW_MIN = "notional_below_min"
    """Leg notional below exchange min and auto-bump disabled (BUG-220)."""

    NOTIONAL_BUMP_EXCEEDS_RISK = "notional_bump_exceeds_risk"
    """BUG-228c auto-bump would violate risk.max_position_pct cap — trade rejected."""

    DEDUP_COLLISION = "dedup_collision"
    """DeduplicationGate atomic check-and-register detected an in-flight collision."""

    UNIVERSE_MISS = "universe_miss"
    """Symbol not in active trading universe (reserved for future universe gate)."""

    ORDERBOOK_TOXIC = "orderbook_toxic"
    """Pre-exec toxicity filter (imbalance/depth_volatility/empty_book) tripped."""

    MARKET_IMPACT_HIGH = "market_impact_high"
    """BookWalk estimated slippage exceeds strategy_filters.max_market_impact_bps."""

    NO_VALID_ORDERS = "no_valid_orders"
    """_legs_to_orders() returned empty list (all legs had invalid price/size)."""

    BUDGET_EXHAUSTED = "budget_exhausted"
    """Strategy's daily loss budget exhausted (StrategyBudgetLedger) — Path-B Day-3."""
