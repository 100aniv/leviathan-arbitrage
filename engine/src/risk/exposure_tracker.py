"""LEVIATHAN Exposure Tracker.

Tracks net exposure per (exchange, base_asset) in Redis.
Detects cross-strategy correlation per Amendment 7 Scenario 5.

Net exposure key: leviathan:exposure:{exchange}:{base_asset}
  Positive = net long, Negative = net short.

Amendment 7 Scenario 5: If strategy A goes long BTC/USDT on Binance AND
strategy B goes short BTC-perp on Binance, the net exposure reveals an
unintended basis position. This tracker detects that scenario.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import structlog

from src.risk.guardian import TradeProposal

logger = structlog.get_logger(__name__)

EXPOSURE_KEY = "leviathan:exposure:{exchange}:{base_asset}"


class ExposureTracker:
    """
    Computes and checks net exposure across all strategies for a given
    (exchange, base_asset) pair.

    When redis_client is provided, state lives in Redis so multiple processes
    share a consistent view. When redis_client is None, falls back to an
    in-memory dict (single-process only, no persistence).
    """

    def __init__(self, redis_client: Any = None) -> None:
        self._redis = redis_client
        self._memory: dict[str, str] = {}  # fallback when Redis unavailable

    async def get_net_exposure(self, exchange_id: str, base_asset: str) -> Decimal:
        """Read current net exposure. Returns 0 if no data."""
        key = EXPOSURE_KEY.format(exchange=exchange_id, base_asset=base_asset)
        if self._redis is not None:
            val = await self._redis.get(key)
        else:
            val = self._memory.get(key)
        if val is None:
            return Decimal("0")
        return Decimal(val.decode() if isinstance(val, bytes) else str(val))

    async def update_exposure(
        self,
        exchange_id: str,
        base_asset: str,
        delta: Decimal,
    ) -> Decimal:
        """
        Add delta to current net exposure and persist.
        Returns new net exposure value.
        delta > 0 for long positions added, delta < 0 for short positions.
        """
        current = await self.get_net_exposure(exchange_id, base_asset)
        new_val = current + delta

        key = EXPOSURE_KEY.format(exchange=exchange_id, base_asset=base_asset)
        if self._redis is not None:
            await self._redis.set(key, str(new_val))
        else:
            self._memory[key] = str(new_val)

        logger.debug(
            "exposure_updated",
            exchange=exchange_id,
            base_asset=base_asset,
            delta=str(delta),
            new_net=str(new_val),
        )
        return new_val

    async def check_correlation(
        self,
        proposal: TradeProposal,
        max_net_exposure: Decimal,
    ) -> bool:
        """
        Check if proposed trade keeps net exposure within max_net_exposure.

        Returns True if trade is safe (within limits).
        Returns False if trade would create excessive net exposure (Amendment 7).

        Amendment 7 Scenario 5: Two strategies creating an unintended basis
        position is caught here by computing hypothetical net exposure.
        """
        if "/" not in proposal.symbol:
            return True  # Cannot compute base asset; skip check gracefully

        base_asset = proposal.symbol.split("/")[0]
        current_net = await self.get_net_exposure(proposal.exchange_id, base_asset)

        # BUY increases net (positive), SELL decreases net (negative)
        delta = proposal.size if proposal.side.upper() == "BUY" else -proposal.size
        hypothetical_net = current_net + delta

        is_safe = abs(hypothetical_net) <= max_net_exposure

        if not is_safe:
            logger.warning(
                "correlation_check_failed",
                exchange=proposal.exchange_id,
                base_asset=base_asset,
                current_net=str(current_net),
                delta=str(delta),
                hypothetical_net=str(hypothetical_net),
                max_net_exposure=str(max_net_exposure),
            )

        return is_safe

    async def get_portfolio_exposure(
        self,
        exchanges: list[str],
        assets: list[str],
    ) -> dict[tuple[str, str], Decimal]:
        """
        Compute snapshot of non-zero net exposures across all (exchange, asset) pairs.
        Returns dict mapping (exchange_id, base_asset) -> net_exposure.
        """
        result: dict[tuple[str, str], Decimal] = {}
        for exchange in exchanges:
            for asset in assets:
                net = await self.get_net_exposure(exchange, asset)
                if net != Decimal("0"):
                    result[(exchange, asset)] = net
        return result
