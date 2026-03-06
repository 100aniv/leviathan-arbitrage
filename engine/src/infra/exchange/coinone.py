"""Coinone adapter — Korean exchange."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.core.models import FeeRate
from src.infra.exchange.ccxt_adapter import CCXTAdapter


class CoinoneAdapter(CCXTAdapter):
    """Coinone Korean exchange adapter."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(exchange_id="coinone", **kwargs)

    async def get_fee_rate(self, symbol: str) -> FeeRate:
        """Coinone standard trading fee: 0.2%."""
        return FeeRate(
            maker=Decimal("0.002"),
            taker=Decimal("0.002"),
            symbol=symbol,
            exchange_id=self.exchange_id,
        )
