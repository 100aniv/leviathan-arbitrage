"""Native Gate.io adapter — Spot trading via direct REST. US-360."""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any

from src.infra.exchange.native_adapter import NativeAdapter
from src.infra.exchange.rate_limiter import RateLimitConfig

logger = logging.getLogger(__name__)

_GATEIO_RATE_LIMITS = {"default": RateLimitConfig(requests_per_second=10, burst=20)}
_REST_BASE = "https://api.gateio.ws"


class NativeGateIOAdapter(NativeAdapter):
    """Native Gate.io spot adapter (US-360)."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("rate_limits", _GATEIO_RATE_LIMITS)
        super().__init__(exchange_id="gateio", **kwargs)

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
        import hashlib as _hs
        import json

        ts = str(int(time.time()))
        body = json.dumps(data) if data else ""
        body_hash = _hs.sha512(body.encode()).hexdigest()
        sign_str = f"{method}\n{path}\n{body_hash}\n{ts}"
        sign = hmac.new(self._api_secret.encode(), sign_str.encode(), hashlib.sha512).hexdigest()
        return {"KEY": self._api_key, "Timestamp": ts, "SIGN": sign}

    def _ws_orderbook_url(self, symbol: str) -> str:
        return "wss://api.gateio.ws/ws/v4/"

    def _ws_subscribe_message(self, symbol: str) -> dict | None:
        sym = symbol.replace("/", "_")
        return {
            "time": int(time.time()),
            "channel": "spot.order_book",
            "event": "subscribe",
            "payload": [sym, "20", "100ms"],
        }

    def _parse_ws_orderbook(self, msg: dict, symbol: str) -> Any | None:
        return None
