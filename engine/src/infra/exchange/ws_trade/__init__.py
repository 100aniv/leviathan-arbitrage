"""WebSocket-based order placement clients (BUG-120).

Replaces REST order placement with exchange WebSocket trading APIs for
70-75% latency reduction. Each client is standalone and testable.
"""
from src.infra.exchange.ws_trade.binance_user_data import BinanceUserDataStream
from src.infra.exchange.ws_trade.binance_ws_trade import BinanceWSTrade
from src.infra.exchange.ws_trade.bitget_ws_trade import BitgetWSTrade
from src.infra.exchange.ws_trade.bithumb_user_data import BithumbUserDataStream
from src.infra.exchange.ws_trade.coinone_user_data import CoinoneUserDataStream
from src.infra.exchange.ws_trade.upbit_user_data import UpbitUserDataStream

__all__ = [
    "BinanceUserDataStream",
    "BinanceWSTrade",
    "BitgetWSTrade",
    "BithumbUserDataStream",
    "CoinoneUserDataStream",
    "UpbitUserDataStream",
]
