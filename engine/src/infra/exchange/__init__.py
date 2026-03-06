"""Exchange connectivity layer — native adapters, WebSocket manager, health checker."""
from src.infra.exchange.base import ExchangeAdapter
from src.infra.exchange.health_checker import HealthChecker
from src.infra.exchange.native_adapter import NativeAdapter
from src.infra.exchange.native_binance import BinanceNativeAdapter
from src.infra.exchange.native_bitget import NativeBitgetAdapter
from src.infra.exchange.native_bithumb import NativeBithumbAdapter
from src.infra.exchange.native_bybit import NativeBybitAdapter
from src.infra.exchange.native_okx import NativeOKXAdapter
from src.infra.exchange.native_upbit import NativeUpbitAdapter
from src.infra.exchange.rate_limiter import ExchangeRateLimiter, RateLimitConfig
from src.infra.exchange.websocket_manager import ConnectionConfig, ConnectionState, WebSocketManager

# Legacy CCXT-based adapters — lazy import to avoid hard ccxt dependency.
# Install with: pip install leviathan-engine[legacy]
try:
    from src.infra.exchange.ccxt_adapter import CCXTAdapter
    from src.infra.exchange.binance import BinanceAdapter
    from src.infra.exchange.bitget import BitgetAdapter
    from src.infra.exchange.bithumb import BithumbAdapter
    from src.infra.exchange.bybit import BybitAdapter
    from src.infra.exchange.coinone import CoinoneAdapter
    from src.infra.exchange.okx import OKXAdapter
    from src.infra.exchange.upbit import UpbitAdapter
    _CCXT_AVAILABLE = True
except ImportError:
    _CCXT_AVAILABLE = False

__all__ = [
    # Base protocols
    "ExchangeAdapter",
    "NativeAdapter",
    # Native adapters (ccxt-free, production)
    "BinanceNativeAdapter",
    "NativeBitgetAdapter",
    "NativeBithumbAdapter",
    "NativeBybitAdapter",
    "NativeOKXAdapter",
    "NativeUpbitAdapter",
    # Infrastructure
    "HealthChecker",
    "ExchangeRateLimiter",
    "RateLimitConfig",
    "WebSocketManager",
    "ConnectionConfig",
    "ConnectionState",
    # Factory
    "create_native_adapter",
]

_NATIVE_ADAPTER_MAP: dict[str, type[NativeAdapter]] = {
    "binance": BinanceNativeAdapter,
    "bybit": NativeBybitAdapter,
    "okx": NativeOKXAdapter,
    "bitget": NativeBitgetAdapter,
    "upbit": NativeUpbitAdapter,
    "bithumb": NativeBithumbAdapter,
}


def create_native_adapter(
    exchange_id: str,
    api_key: str = "",
    api_secret: str = "",
    passphrase: str = "",
    sandbox: bool = False,
) -> NativeAdapter:
    """Factory: return the NativeAdapter subclass for the given exchange_id.

    Args:
        exchange_id: Exchange identifier (e.g. 'binance', 'bybit', 'okx').
        api_key: API key for authenticated endpoints.
        api_secret: API secret for signing requests.
        passphrase: Passphrase (required by OKX and Bitget).
        sandbox: Use testnet/sandbox endpoints if True.

    Raises:
        ValueError: If exchange_id is not supported.
    """
    cls = _NATIVE_ADAPTER_MAP.get(exchange_id.lower())
    if cls is None:
        supported = sorted(_NATIVE_ADAPTER_MAP.keys())
        raise ValueError(
            f"Unsupported exchange '{exchange_id}'. Supported: {supported}"
        )
    return cls(
        api_key=api_key,
        api_secret=api_secret,
        passphrase=passphrase,
        sandbox=sandbox,
    )
