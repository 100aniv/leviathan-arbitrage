"""Native MEXC adapter — Spot trading via direct REST (no ccxt). US-360."""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any

from src.infra.exchange.native_adapter import NativeAdapter
from src.infra.exchange.rate_limiter import RateLimitConfig

logger = logging.getLogger(__name__)

_MEXC_RATE_LIMITS = {"default": RateLimitConfig(requests_per_second=20, burst=40)}
_REST_BASE = "https://api.mexc.com"


class NativeMEXCAdapter(NativeAdapter):
    """Native MEXC spot adapter (US-360)."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("rate_limits", _MEXC_RATE_LIMITS)
        super().__init__(exchange_id="mexc", **kwargs)

    def _rest_base_url(self) -> str:
        return _REST_BASE

    def _default_headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "X-MEXC-APIKEY": self._api_key}

    def _auth_headers(
        self,
        method: str,
        path: str,
        params: dict | None,
        data: dict | None,
    ) -> dict[str, str]:
        ts = str(int(time.time() * 1000))
        query = "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
        query += f"&timestamp={ts}"
        sign = hmac.new(self._api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        return {"signature": sign, "timestamp": ts}

    def _ws_orderbook_url(self, symbol: str) -> str:
        return "wss://wbs.mexc.com/ws"

    def _ws_subscribe_message(self, symbol: str) -> dict | None:
        sym = symbol.replace("/", "")
        return {"method": "SUBSCRIPTION", "params": [f"spot@public.limit.depth.v3.api@{sym}@20"]}

    def _parse_ws_orderbook(self, msg: dict, symbol: str) -> Any | None:
        return None  # Parsed by collector
