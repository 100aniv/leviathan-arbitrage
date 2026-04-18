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
from src.infra.exchange.native_mexc import NativeMEXCAdapter
from src.infra.exchange.native_gateio import NativeGateIOAdapter
from src.infra.exchange.native_bingx import NativeBingXAdapter
from src.infra.exchange.native_lbank import NativeLBankAdapter
from src.infra.exchange.native_orangex import NativeOrangeXAdapter
from src.infra.exchange.native_coinone import NativeCoinoneAdapter
from src.infra.exchange.rate_limiter import ExchangeRateLimiter, RateLimitConfig
from src.infra.exchange.websocket_manager import ConnectionConfig, ConnectionState, WebSocketManager

# BUG-128: ccxt legacy adapters removed from default import chain.
# Live mode uses create_native_adapter only. Sandbox CLI (sandbox_paper_runner /
# sandbox_verify) may still import CCXTAdapter directly when needed.
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
    "NativeMEXCAdapter",
    "NativeGateIOAdapter",
    "NativeBingXAdapter",
    "NativeLBankAdapter",
    "NativeOrangeXAdapter",
    "NativeCoinoneAdapter",
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
    "mexc": NativeMEXCAdapter,
    "gateio": NativeGateIOAdapter,
    "bingx": NativeBingXAdapter,
    "lbank": NativeLBankAdapter,
    "orangex": NativeOrangeXAdapter,
    "coinone": NativeCoinoneAdapter,
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
    eid = exchange_id.lower()
    # Phase H-2: futures exchanges (e.g. "binance_futures") → base adapter + market_type
    market_type = "spot"
    base_eid = eid
    if eid.endswith("_futures"):
        base_eid = eid.removesuffix("_futures")
        market_type = "futures"

    cls = _NATIVE_ADAPTER_MAP.get(base_eid)
    if cls is None:
        supported = sorted(_NATIVE_ADAPTER_MAP.keys())
        raise ValueError(
            f"Unsupported exchange '{exchange_id}'. Supported: {supported}"
        )
    adapter = cls(
        api_key=api_key,
        api_secret=api_secret,
        passphrase=passphrase,
        sandbox=sandbox,
    )
    # Set market_type for futures (adapter must support it)
    if market_type == "futures" and hasattr(adapter, "_market_type"):
        adapter._market_type = market_type
    # Pass full exchange_id (e.g. "bitget_futures") lowercased for correct logging
    adapter.exchange_id = eid
    return adapter
