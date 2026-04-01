"""BaseMode — shared interface for LiveMode (and deprecated ShadowMode).

Phase I: defines common types and stats classes.
Full extraction to follow in Phase J.

WIRING AC:
  生成 (create):  LiveMode(BaseMode) instantiated in main.py _init_live_mode()
  주입 (inject):  LiveMode inherits BaseMode directly (class hierarchy)
  호출 (call):    LiveMode.start() / LiveMode.stop() at runtime
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PerStrategyStats:
    """Per-strategy PnL statistics (shared by LiveMode and ShadowMode).

    Note: LiveMode defines its own extended PerStrategyStats in live.py
    (with signals, trades, rejections, pnl_history fields).  This base
    version is a minimal common denominator for future unification.
    """
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0


@dataclass
class BaseModeStats:
    """Base statistics for all trading modes."""
    trades_executed: int = 0
    trades_won: int = 0
    trades_lost: int = 0
    total_pnl: float = 0.0
    peak_pnl: float = 0.0
    max_drawdown: float = 0.0
    trades_risk_blocked: int = 0
    by_strategy: dict = field(default_factory=dict)


class BaseMode:
    """Abstract base for trading modes.

    Provides shared interface for LiveMode (Phase I).
    ShadowMode is deprecated and wraps LiveMode.

    Subclasses must implement start() and stop().
    """

    async def start(self) -> None:  # pragma: no cover
        """Start the trading mode — override in subclass."""
        raise NotImplementedError

    async def stop(self) -> None:  # pragma: no cover
        """Stop the trading mode — override in subclass."""
        raise NotImplementedError
