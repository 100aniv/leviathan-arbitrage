"""Bybit adapter."""
from __future__ import annotations

from typing import Any

from src.infra.exchange.ccxt_adapter import CCXTAdapter


class BybitAdapter(CCXTAdapter):
    """Bybit adapter supporting spot and derivatives."""

    def __init__(self, market_type: str = "spot", **kwargs: Any) -> None:
        super().__init__(exchange_id="bybit", **kwargs)
        self._market_type = market_type
