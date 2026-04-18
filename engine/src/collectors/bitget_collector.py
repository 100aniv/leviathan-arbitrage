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
    """Collects Bitget spot orderbook data via UTA V3 public WebSocket.

    Endpoint: wss://ws.bitget.com/v3/ws/public (BUG-181/182: UTA V3 migration).
    V3 payload: topic (not channel), symbol (not instId), instType lowercase.
    V3 supports books1/books5/books50/books; NOT books15 (V2-only).
    Response fields: b (bids), a (asks) vs V2 bids/asks.

    No API key is required.
    """

    _WS_URL = "wss://ws.bitget.com/v3/ws/public"
    _TOPIC = "books5"
    _INST_TYPE = "spot"

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
        """BUG-84: Batch subscribe. BUG-182: V3 uses topic/symbol/lowercase instType."""
        messages = []
        for i in range(0, len(self.symbols), self._BATCH_SIZE):
            batch = self.symbols[i : i + self._BATCH_SIZE]
            messages.append({
                "op": "subscribe",
                "args": [
                    {
                        "instType": self._INST_TYPE,
                        "topic": self._TOPIC,
                        "symbol": _normalize_symbol(sym),
                    }
                    for sym in batch
                ],
            })
        return messages

    def _subscribe_message(self, symbol: str) -> str | dict:
        """Build V3 subscribe frame for one symbol (fallback)."""
        return {
            "op": "subscribe",
            "args": [
                {
                    "instType": self._INST_TYPE,
                    "topic": self._TOPIC,
                    "symbol": _normalize_symbol(symbol),
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
        # BUG-182: V3 response uses 'symbol' (V2 used 'instId')
        inst_id: str = arg.get("symbol") or arg.get("instId", "")
        if not inst_id:
            return None

        symbol = _denormalize_symbol(inst_id)

        data_list = data.get("data", [])
        if not data_list:
            return None

        entry = data_list[0]
        # BUG-182: V3 response uses 'b'/'a' (V2 used 'bids'/'asks')
        bids: list = entry.get("b") or entry.get("bids", [])
        asks: list = entry.get("a") or entry.get("asks", [])

        return symbol, bids, asks
