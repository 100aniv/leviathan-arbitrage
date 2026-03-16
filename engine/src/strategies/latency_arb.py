"""Deprecated: Latency Arbitrage strategy (US-194).

Latency-boost logic has been merged into CrossExchangeStrategy.
Use CrossExchangeConfig(latency_boost=True) + CrossExchangeStrategy instead.

This module is kept as a compatibility shim so existing imports do not break
during the transition period.
"""
from __future__ import annotations

import warnings
from decimal import Decimal

from pydantic import BaseModel, Field

from src.core.latency_tracker import LatencyTracker
from src.core.models import Trade
from src.strategies.base import BaseStrategy, CostCalculator
from src.strategies.cross_exchange import CrossExchangeConfig, CrossExchangeStrategy


class LatencyArbConfig(BaseModel):
    """Deprecated config shim. Use CrossExchangeConfig(latency_boost=True)."""

    min_latency_advantage_ms: float = Field(default=5.0, ge=0.0)
    max_position_size: Decimal = Field(default=Decimal("1.0"), gt=Decimal("0"))
    min_net_profit_usdt: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))


class LatencyArbStrategy(CrossExchangeStrategy):
    """Deprecated wrapper. Use CrossExchangeStrategy(latency_boost=True)."""

    STRATEGY_TYPE = "latency_arb"

    def __init__(
        self,
        strategy_id: str,
        cost_calculator: CostCalculator,
        latency_tracker: LatencyTracker,
        config: LatencyArbConfig | None = None,
    ) -> None:
        warnings.warn(
            "LatencyArbStrategy is deprecated (US-194). "
            "Use CrossExchangeStrategy with CrossExchangeConfig(latency_boost=True).",
            DeprecationWarning,
            stacklevel=2,
        )
        cfg = config or LatencyArbConfig()
        ce_config = CrossExchangeConfig(
            min_spread_bps=Decimal("0"),  # no spread gate — latency gate takes over
            max_position_size=cfg.max_position_size,
            latency_boost=True,
            min_latency_advantage_ms=cfg.min_latency_advantage_ms,
        )
        super().__init__(strategy_id, cost_calculator, config=ce_config, latency_tracker=latency_tracker)

    async def on_fill(self, trade: Trade) -> None:
        await super().on_fill(trade)
