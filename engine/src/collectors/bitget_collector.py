"""Bitget public orderbook collector via native WebSocket."""
from __future__ import annotations

from typing import Callable, Awaitable

import structlog

from src.collectors.base_collector import BaseCollector

logger = structlog.get_logger(__name__)


def _normalize_symbol(symbol: str) -> str:
    """Convert canonical "BTC/USDT" -> Bitget instId "BTCUSDT"."""
    return symbol.replace("/", "")


def _denormalize_symbol(inst_id: str) -> str:
    """Convert Bitget instId "BTCUSDT" -> canonical "BTC/USDT".

    Uses a longest-match scan over common quote currencies.
    """
    quotes = ["USDT", "USDC", "BUSD", "TUSD", "BTC", "ETH", "BNB", "USD"]
    s = inst_id.upper()
    for q in quotes:
        if s.endswith(q):
            base = s[: -len(q)]
            return f"{base}/{q}"
    # Fallback: return unchanged
    return inst_id


class BitgetCollector(BaseCollector):
    """Collects Bitget spot orderbook data via the public V2 WebSocket.

    Endpoint: wss://ws.bitget.com/v2/ws/public

    Subscribes to the "books15" channel (top-15 levels) for SPOT market type.
    Both "snapshot" and "update" actions are forwarded as a full replace of
    local state, which is sufficient for cross-exchange arbitrage.

    No API key is required.
    """

    _WS_URL = "wss://ws.bitget.com/v2/ws/public"
    _CHANNEL = "books15"
    _INST_TYPE = "SPOT"

    def __init__(
        self,
        symbols: list[str],
        on_orderbook: Callable[[str, str, list, list], Awaitable[None]] | None = None,
    ) -> None:
        # BUG-69: Bitget WS server does not respond to application-level pings.
        # Disable client-initiated pings; server-side keepalive handles the connection.
        super().__init__(
            exchange_id="bitget", symbols=symbols, on_orderbook=on_orderbook,
            ping_interval=None, ping_timeout=None,
        )

    # ------------------------------------------------------------------
    # BaseCollector interface
    # ------------------------------------------------------------------

    _BATCH_SIZE = 30  # BUG-84: Bitget disconnects when many individual subscribes flood the WS

    def _ws_url(self) -> str:
        return self._WS_URL

    def _subscribe_all_messages(self) -> list[dict] | None:
        """BUG-84: Batch subscribe — Bitget V2 supports multiple args per message."""
        messages = []
        for i in range(0, len(self.symbols), self._BATCH_SIZE):
            batch = self.symbols[i : i + self._BATCH_SIZE]
            messages.append({
                "op": "subscribe",
                "args": [
                    {
                        "instType": self._INST_TYPE,
                        "channel": self._CHANNEL,
                        "instId": _normalize_symbol(sym),
                    }
                    for sym in batch
                ],
            })
        return messages

    def _subscribe_message(self, symbol: str) -> str | dict:
        """Build the Bitget V2 subscribe frame for one symbol (fallback)."""
        return {
            "op": "subscribe",
            "args": [
                {
                    "instType": self._INST_TYPE,
                    "channel": self._CHANNEL,
                    "instId": _normalize_symbol(symbol),
                }
            ],
        }

    def _parse_message(self, data: dict) -> tuple[str, list, list] | None:
        # Subscription ack: {"event": "subscribe", ...} — ignore
        if "event" in data:
            return None

        action = data.get("action")

        # Only handle snapshot and update actions
        if action not in ("snapshot", "update"):
            return None

        arg = data.get("arg", {})
        inst_id: str = arg.get("instId", "")
        if not inst_id:
            return None

        symbol = _denormalize_symbol(inst_id)

        data_list = data.get("data", [])
        if not data_list:
            return None

        entry = data_list[0]
        # Bitget level format: [price_str, qty_str]
        bids: list = entry.get("bids", [])
        asks: list = entry.get("asks", [])

        return symbol, bids, asks
