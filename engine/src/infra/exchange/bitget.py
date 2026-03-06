"""Bitget adapter — Spot and USDT-M Futures (Perpetual Swaps)."""
from __future__ import annotations

import logging
from typing import Any

from src.infra.exchange.ccxt_adapter import CCXTAdapter
from src.infra.exchange.rate_limiter import RateLimitConfig

logger = logging.getLogger(__name__)

# Bitget rate limits (conservative retail defaults)
_BITGET_RATE_LIMITS: dict[str, RateLimitConfig] = {
    "default": RateLimitConfig(requests_per_second=10, burst=20),
    "order": RateLimitConfig(requests_per_second=10, burst=20),
}


class BitgetAdapter(CCXTAdapter):
    """
    Bitget-specific exchange adapter.

    Supports both Spot and USDT-M Futures (perpetual swaps).
    Both market types use the 'bitget' ccxt exchange id.
    Futures mode configures defaultType='swap' in exchange options.

    Usage:
        spot = BitgetAdapter(market_type="spot", api_key=..., api_secret=..., passphrase=...)
        futures = BitgetAdapter(market_type="futures", api_key=..., api_secret=..., passphrase=...)
    """

    def __init__(
        self,
        market_type: str = "spot",
        **kwargs: Any,
    ) -> None:
        extra_config: dict[str, Any] = kwargs.pop("extra_config", None) or {}

        if market_type == "futures":
            extra_config.setdefault("options", {})["defaultType"] = "swap"

        super().__init__(
            exchange_id="bitget",
            rate_limits=_BITGET_RATE_LIMITS,
            extra_config=extra_config,
            **kwargs,
        )
        self._market_type = market_type
        logger.info("BitgetAdapter initialised (market_type=%s)", market_type)
