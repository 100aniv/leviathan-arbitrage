"""Upbit adapter — Korean exchange with KRW pairs."""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from src.core.models import FeeRate
from src.infra.exchange.ccxt_adapter import CCXTAdapter

logger = logging.getLogger(__name__)


class UpbitAdapter(CCXTAdapter):
    """
    Upbit Korean exchange adapter.

    Specifics:
    - All pairs are KRW-denominated (e.g., BTC/KRW)
    - Flat 0.05% fee for all trade types
    - No futures/margin support
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(exchange_id="upbit", **kwargs)

    def normalize_symbol(self, symbol: str) -> str:
        """Ensure symbol uses KRW quote (e.g., 'BTC' -> 'BTC/KRW')."""
        if "/" not in symbol:
            return f"{symbol}/KRW"
        return symbol

    async def get_fee_rate(self, symbol: str) -> FeeRate:
        """Upbit charges a flat 0.05% fee on all trades."""
        return FeeRate(
            maker=Decimal("0.0005"),
            taker=Decimal("0.0005"),
            symbol=symbol,
            exchange_id=self.exchange_id,
        )
