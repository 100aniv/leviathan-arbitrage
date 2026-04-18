"""WebSocket-based order placement clients (BUG-120).

Replaces REST order placement with exchange WebSocket trading APIs for
70-75% latency reduction. Each client is standalone and testable.
"""
from src.infra.exchange.ws_trade.binance_ws_trade import BinanceWSTrade
from src.infra.exchange.ws_trade.bitget_ws_trade import BitgetWSTrade

__all__ = ["BinanceWSTrade", "BitgetWSTrade"]
