"""OKX public orderbook collector via native WebSocket."""
from __future__ import annotations

from typing import Callable, Awaitable

import structlog

from src.collectors.base_collector import BaseCollector

logger = structlog.get_logger(__name__)


def _normalize_symbol(symbol: str) -> str:
    """Convert canonical "BTC/USDT" -> OKX instId "BTC-USDT"."""
    return symbol.replace("/", "-")


def _denormalize_symbol(inst_id: str) -> str:
    """Convert OKX instId "BTC-USDT" -> canonical "BTC/USDT"."""
    return inst_id.replace("-", "/")


class OKXCollector(BaseCollector):
    """Collects OKX spot orderbook data via the public V5 WebSocket.

    Endpoint: wss://ws.okx.com:8443/ws/v5/public

    Subscribes to the "books5" channel (top-5 levels, public, no auth required).
    The books5 channel sends data without an "action" field; messages contain
    "arg" + "data" keys directly.

    No API key is required.
    """

    _WS_URL = "wss://ws.okx.com:8443/ws/v5/public"
    _CHANNEL = "books5"

    def __init__(
        self,
        symbols: list[str],
        on_orderbook: Callable[[str, str, list, list], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(exchange_id="okx", symbols=symbols, on_orderbook=on_orderbook)

    # ------------------------------------------------------------------
    # BaseCollector interface
    # ------------------------------------------------------------------

    def _ws_url(self) -> str:
        return self._WS_URL

    def _subscribe_message(self, symbol: str) -> str | dict:
        """Build the OKX subscribe frame for one symbol."""
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
        # Subscription ack / error events — ignore
        if "event" in data:
            return None

        arg = data.get("arg", {})
        action = data.get("action")

        # books5 sends no "action" field; books-l2 variants send "snapshot"/"update".
        # Reject only messages with an explicit unrecognised action.
        if action is not None and action not in ("snapshot", "update"):
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
        # We only use the first two elements.
        raw_bids = entry.get("bids", [])
        raw_asks = entry.get("asks", [])

        bids = [[level[0], level[1]] for level in raw_bids]
        asks = [[level[0], level[1]] for level in raw_asks]

        return symbol, bids, asks
