"""Native OrangeX adapter — Derivatives trading via direct REST. US-360."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any

from src.infra.exchange.native_adapter import NativeAdapter
from src.infra.exchange.rate_limiter import RateLimitConfig

logger = logging.getLogger(__name__)

_ORANGEX_RATE_LIMITS = {"default": RateLimitConfig(requests_per_second=10, burst=20)}
_REST_BASE = "https://api.orangex.com"


class NativeOrangeXAdapter(NativeAdapter):
    """Native OrangeX derivatives adapter (US-360)."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("rate_limits", _ORANGEX_RATE_LIMITS)
        super().__init__(exchange_id="orangex", **kwargs)

    def _rest_base_url(self) -> str:
        return _REST_BASE

    def _default_headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def _auth_headers(
        self,
        method: str,
        path: str,
        params: dict | None,
        data: dict | None,
    ) -> dict[str, str]:
        ts = str(int(time.time() * 1000))
        nonce = ts
        body = json.dumps(data) if data else ""
        sign_str = ts + nonce + method.upper() + path + body
        sign = hmac.new(self._api_secret.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
        return {"api-key": self._api_key, "api-sign": sign, "api-timestamp": ts, "api-nonce": nonce}

    def _ws_orderbook_url(self, symbol: str) -> str:
        return "wss://api.orangex.com/ws/api/v1"

    def _ws_subscribe_message(self, symbol: str) -> dict | None:
        sym = symbol.replace("/", "-").upper()
        return {"jsonrpc": "2.0", "method": "public/subscribe", "params": {"channels": [f"book.{sym}.20"]}}

    def _parse_ws_orderbook(self, msg: dict, symbol: str) -> Any | None:
        return None
