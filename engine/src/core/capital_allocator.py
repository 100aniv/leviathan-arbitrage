"""Capital allocation via Kelly Criterion with Half-Kelly dampening.

Kelly formula: f* = (b*p - q) / b
  where b = avg_win / avg_loss (odds ratio)
        p = win probability
        q = 1 - p

Half-Kelly: f = f* / 2 (reduces variance by ~50%, PnL by ~25%)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = "config/capital_allocation.json"


@dataclass
class StrategyAllocation:
    """Allocation result for a single strategy."""

    strategy_id: str
    kelly_fraction: float  # raw f*
    half_kelly: float  # f* / 2
    allocated_pct: float  # final % of total capital (after normalization)
    win_rate: float
    avg_win: float
    avg_loss: float


class CapitalAllocator:
    """전략별 자본 할당 비율 계산 (Kelly Criterion + Half-Kelly)."""

    def __init__(
        self,
        total_capital: float = 10_000.0,
        max_allocation_pct: float = 0.40,
        min_allocation_pct: float = 0.02,
        min_trades: int = 30,
        config_path: str = DEFAULT_CONFIG_PATH,
    ) -> None:
        self.total_capital = total_capital
        self.max_allocation_pct = max_allocation_pct
        self.min_allocation_pct = min_allocation_pct
        self.min_trades = min_trades
        self._config_path = Path(config_path)

    @staticmethod
    def kelly_fraction(
        win_rate: float, avg_win: float, avg_loss: float
    ) -> float:
        """Calculate raw Kelly fraction f* = (b*p - q) / b.

        Returns 0.0 for negative edge or invalid inputs.
        """
        if avg_loss <= 0 or avg_win <= 0 or not (0 < win_rate < 1):
            return 0.0

        b = avg_win / avg_loss  # odds ratio
        p = win_rate
        q = 1.0 - p

        f_star = (b * p - q) / b
        return max(f_star, 0.0)

    def compute_allocations(
        self, strategy_stats: dict[str, dict]
    ) -> list[StrategyAllocation]:
        """전략별 자본 할당 비율 계산.

        Args:
            strategy_stats: {strategy_id: {win_rate, avg_win, avg_loss, num_trades}}

        Returns:
            List of StrategyAllocation with normalized percentages.
        """
        allocations: list[StrategyAllocation] = []

        for sid, stats in strategy_stats.items():
            num_trades = stats.get("num_trades", 0)
            if num_trades < self.min_trades:
                logger.debug(
                    "capital_allocator.skip: %s trades=%d < min=%d",
                    sid, num_trades, self.min_trades,
                )
                continue

            wr = stats["win_rate"]
            avg_w = stats["avg_win"]
            avg_l = stats["avg_loss"]

            f_star = self.kelly_fraction(wr, avg_w, avg_l)
            half_k = f_star / 2.0

            # Clamp to [min, max]
            clamped = max(
                self.min_allocation_pct,
                min(half_k, self.max_allocation_pct),
            )

            allocations.append(
                StrategyAllocation(
                    strategy_id=sid,
                    kelly_fraction=f_star,
                    half_kelly=half_k,
                    allocated_pct=clamped,
                    win_rate=wr,
                    avg_win=avg_w,
                    avg_loss=avg_l,
                )
            )

        # Normalize so total <= 100%
        total = sum(a.allocated_pct for a in allocations)
        if total > 1.0 and allocations:
            scale = 1.0 / total
            for a in allocations:
                a.allocated_pct *= scale

        return allocations

    def save_config(self, allocations: list[StrategyAllocation]) -> None:
        """config/capital_allocation.json 동적 업데이트."""
        config: dict = {}
        for a in allocations:
            config[a.strategy_id] = {
                "kelly_fraction": round(a.kelly_fraction, 6),
                "half_kelly": round(a.half_kelly, 6),
                "allocated_pct": round(a.allocated_pct, 6),
                "allocated_usd": round(a.allocated_pct * self.total_capital, 2),
                "win_rate": round(a.win_rate, 4),
                "avg_win": round(a.avg_win, 4),
                "avg_loss": round(a.avg_loss, 4),
            }

        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        self._config_path.write_text(json.dumps(config, indent=2))
        logger.info(
            "capital_allocator.saved: %d strategies → %s",
            len(config),
            self._config_path,
        )

    def get_allocation_usd(
        self, allocations: list[StrategyAllocation], strategy_id: str
    ) -> float:
        """특정 전략의 할당 금액 (USD) 반환."""
        for a in allocations:
            if a.strategy_id == strategy_id:
                return a.allocated_pct * self.total_capital
        return 0.0

    # US-279: Regime-Aware capital allocation
    REGIME_KELLY_MULTIPLIER: dict[str, float] = {
        "bull": 1.0,
        "neutral": 0.7,
        "bear": 0.4,
        "crisis": 0.1,
    }

    def allocate_with_regime(
        self,
        strategy_stats: dict[str, dict],
        regime: str = "neutral",
    ) -> list[StrategyAllocation]:
        """Kelly allocation scaled by market regime.

        Multiplier: bull=1.0, neutral=0.7, bear=0.4, crisis=0.1.
        After scaling, re-normalizes so total <= 100%.
        """
        import os as _os
        if _os.getenv("REGIME_AWARE_ALLOCATION_ENABLED", "true").lower() == "false":
            return self.compute_allocations(strategy_stats)

        mult = self.REGIME_KELLY_MULTIPLIER.get(regime, 0.7)
        allocations = self.compute_allocations(strategy_stats)
        for a in allocations:
            a.allocated_pct *= mult

        # Re-normalize so total <= 100%
        total = sum(a.allocated_pct for a in allocations)
        if total > 1.0 and allocations:
            scale = 1.0 / total
            for a in allocations:
                a.allocated_pct *= scale

        logger.info(
            "capital_allocator.regime: regime=%s mult=%.1f strategies=%d",
            regime,
            mult,
            len(allocations),
        )
        return allocations
