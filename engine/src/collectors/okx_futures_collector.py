"""OKX futures (SWAP) public orderbook collector via native WebSocket."""
from __future__ import annotations

from typing import Callable, Awaitable

import structlog

from src.collectors.base_collector import BaseCollector

logger = structlog.get_logger(__name__)


def _normalize_symbol(symbol: str) -> str:
    """Convert canonical "BTC/USDT" -> OKX futures instId "BTC-USDT-SWAP"."""
    return symbol.replace("/", "-") + "-SWAP"


def _denormalize_symbol(inst_id: str) -> str:
    """Convert OKX futures instId "BTC-USDT-SWAP" -> canonical "BTC/USDT"."""
    without_swap = inst_id.removesuffix("-SWAP")
    return without_swap.replace("-", "/")


class OKXFuturesCollector(BaseCollector):
    """Collects OKX perpetual futures (SWAP) orderbook data via the public V5 WebSocket.

    Endpoint: wss://ws.okx.com:8443/ws/v5/public  (same as spot)

    Subscribes to the "books50-l2-tbt" channel using SWAP instIds (e.g. BTC-USDT-SWAP).
    Both "snapshot" and "update" actions are forwarded as full replacements.

    No API key is required.
    """

    _WS_URL = "wss://ws.okx.com:8443/ws/v5/public"
    _CHANNEL = "books50-l2-tbt"

    def __init__(
        self,
        symbols: list[str],
        on_orderbook: Callable[[str, str, list, list], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(exchange_id="okx_futures", symbols=symbols, on_orderbook=on_orderbook)

    # ------------------------------------------------------------------
    # BaseCollector interface
    # ------------------------------------------------------------------

    def _ws_url(self) -> str:
        return self._WS_URL

    def _subscribe_message(self, symbol: str) -> str | dict:
        """Build the OKX subscribe frame for one futures symbol."""
        return {
            "op": "subscribe",
            "args": [
                {
                    "channel": self._CHANNEL,
                    "instId": _normalize_symbol(symbol),
                }
            ],
        }

    def _parse_message(self, data: dict) -> tuple[str, list, list] | None:
        # Subscription ack: {"event": "subscribe", ...} — ignore
        if "event" in data:
            return None

        arg = data.get("arg", {})
        action = data.get("action")

        # Only handle snapshot and update actions
        if action not in ("snapshot", "update"):
            return None

        inst_id: str = arg.get("instId", "")
        if not inst_id:
            return None

        symbol = _denormalize_symbol(inst_id)

        data_list = data.get("data", [])
        if not data_list:
            return None

        entry = data_list[0]
        # OKX level format: [price, qty, deprecated_field, num_orders]
        raw_bids = entry.get("bids", [])
        raw_asks = entry.get("asks", [])

        bids = [[level[0], level[1]] for level in raw_bids]
        asks = [[level[0], level[1]] for level in raw_asks]

        return symbol, bids, asks
