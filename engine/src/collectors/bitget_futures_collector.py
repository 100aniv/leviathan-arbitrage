"""Bitget USDT-M perpetual futures public orderbook collector via native WebSocket.

Endpoint: wss://ws.bitget.com/v2/ws/public  (same as spot)

Uses instType="USDT-FUTURES" with the books15 channel.
Message format is identical to the Bitget spot V2 API:
  {"action": "snapshot"|"update", "arg": {..., "instId": "BTCUSDT"}, "data": [...]}

No API key is required.
"""
from __future__ import annotations

from typing import Callable, Awaitable

import structlog

from src.collectors.base_collector import BaseCollector

logger = structlog.get_logger(__name__)


def _normalize_symbol(symbol: str) -> str:
    """Convert canonical "BTC/USDT" -> Bitget futures instId "BTCUSDT"."""
    return symbol.replace("/", "")


def _denormalize_symbol(inst_id: str) -> str:
    """Convert Bitget instId "BTCUSDT" -> canonical "BTC/USDT".

    Uses longest-match scan over common quote currencies.
    """
    quotes = ["USDT", "USDC", "BUSD", "TUSD", "BTC", "ETH", "BNB", "USD"]
    s = inst_id.upper()
    for q in quotes:
        if s.endswith(q):
            base = s[: -len(q)]
            return f"{base}/{q}"
    return inst_id


class BitgetFuturesCollector(BaseCollector):
    """Collects Bitget USDT-M perpetual futures orderbook data via the public V2 WebSocket.

    Subscribes to the "books15" channel using instType="USDT-FUTURES".
    Both "snapshot" and "update" actions are treated as full orderbook replacements,
    sufficient for cross-exchange arbitrage and funding-rate strategies.

    No API key is required.
    """

    _WS_URL = "wss://ws.bitget.com/v2/ws/public"
    _CHANNEL = "books15"
    _INST_TYPE = "USDT-FUTURES"

    def __init__(
        self,
        symbols: list[str],
        on_orderbook: Callable[[str, str, list, list], Awaitable[None]] | None = None,
    ) -> None:
        # BUG-69: Bitget WS server does not respond to application-level pings.
        # Disable client-initiated pings; server-side keepalive handles the connection.
        # BUG-74: data_timeout_s=60 detects zombie connections where TCP is alive
        # but the Bitget server stopped pushing book updates (seen after order placement).
        super().__init__(
            exchange_id="bitget_futures", symbols=symbols, on_orderbook=on_orderbook,
            ping_interval=None, ping_timeout=None,
            data_timeout_s=60.0,
        )

    # ------------------------------------------------------------------
    # BaseCollector interface
    # ------------------------------------------------------------------

    _BATCH_SIZE = 30  # BUG-84: Bitget disconnects when 181 individual subscribes flood the WS

    def _ws_url(self) -> str:
        return self._WS_URL

    def _subscribe_all_messages(self) -> list[dict] | None:
        """BUG-84: Batch subscribe — Bitget V2 supports multiple args per message.
        Sending 181 individual subscribes floods the WS and causes silent disconnect.
        Batch into groups of 30 to stay within Bitget rate limits.
        """
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
        """Build the Bitget V2 subscribe frame for one futures symbol (fallback)."""
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
