"""Native LBank adapter — Spot trading via direct REST. US-360."""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any

from src.infra.exchange.native_adapter import NativeAdapter
from src.infra.exchange.rate_limiter import RateLimitConfig

logger = logging.getLogger(__name__)

_LBANK_RATE_LIMITS = {"default": RateLimitConfig(requests_per_second=5, burst=10)}
_REST_BASE = "https://api.lbank.info"


class NativeLBankAdapter(NativeAdapter):
    """Native LBank spot adapter (US-360)."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("rate_limits", _LBANK_RATE_LIMITS)
        super().__init__(exchange_id="lbank", **kwargs)

    def _rest_base_url(self) -> str:
        return _REST_BASE

    def _default_headers(self) -> dict[str, str]:
        return {"Content-Type": "application/x-www-form-urlencoded"}

    def _auth_headers(
        self,
        method: str,
        path: str,
        params: dict | None,
        data: dict | None,
    ) -> dict[str, str]:
        ts = str(int(time.time() * 1000))
        all_params = dict(params or {})
        all_params.update(data or {})
        all_params["api_key"] = self._api_key
        all_params["timestamp"] = ts
        sorted_str = "&".join(f"{k}={v}" for k, v in sorted(all_params.items()))
        sign = hmac.new(self._api_secret.encode(), sorted_str.encode(), hashlib.sha256).hexdigest().upper()
        return {"sign": sign, "timestamp": ts, "api_key": self._api_key}

    def _ws_orderbook_url(self, symbol: str) -> str:
        return "wss://www.lbank.info/ws/V2/"

    def _ws_subscribe_message(self, symbol: str) -> dict | None:
        sym = symbol.replace("/", "_").lower()
        return {"action": "subscribe", "subscribe": "depth", "depth": "20", "pair": sym}

    def _parse_ws_orderbook(self, msg: dict, symbol: str) -> Any | None:
        return None
