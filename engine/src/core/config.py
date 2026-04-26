"""LEVIATHAN Engine Configuration.

Loads all settings from environment variables using Pydantic Settings v2.
Supports dev/staging/prod environment switching.

Dynaconf integration: ``ENV_FOR_DYNACONF=shadow|live|test|backtest`` selects
a profile from ``engine/settings.toml``.  Values are injected into
``os.environ`` **only when not already set**, so the priority chain is:

    environment variable  >  .env file  >  settings.toml (dynaconf)  >  hardcoded default
"""
from __future__ import annotations

import json
import logging
import os
import warnings
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Dynaconf profile loader — runs once at module import time
# ---------------------------------------------------------------------------

_ENGINE_ROOT = Path(__file__).resolve().parent.parent.parent  # engine/

_DYNACONF_SETTINGS_FILE = _ENGINE_ROOT / "settings.toml"


def _load_dynaconf_defaults() -> None:
    """Inject dynaconf profile values into ``os.environ`` for keys not already set.

    This makes dynaconf act as a **lowest-priority value provider**.  The full
    priority chain is:

        shell environment variable  >  .env file  >  settings.toml (dynaconf)  >  hardcoded default

    To enforce ``.env`` > ``settings.toml``, we first load ``.env`` into
    ``os.environ`` (without overriding real shell vars) via ``dotenv``, then
    only inject dynaconf values for keys still absent.

    The function is a no-op when ``settings.toml`` is absent (e.g. CI).
    """
    if not _DYNACONF_SETTINGS_FILE.exists():
        return

    try:
        # Step 1: Pre-load .env into os.environ so its values beat dynaconf.
        # override=False means real shell env vars are never clobbered.
        _env_file = _ENGINE_ROOT.parent / ".env"
        if _env_file.exists():
            try:
                from dotenv import load_dotenv  # noqa: PLC0415

                load_dotenv(str(_env_file), override=False)
            except ImportError:
                pass  # python-dotenv absent — .env handled by Pydantic later

        # Step 2: Load dynaconf profile and inject only missing keys.
        from dynaconf import Dynaconf  # noqa: PLC0415

        dc = Dynaconf(
            settings_files=[str(_DYNACONF_SETTINGS_FILE)],
            environments=True,
            env_switcher="ENV_FOR_DYNACONF",
            root_path=str(_ENGINE_ROOT),
        )

        for key in dc.keys():
            env_key = key.upper()
            if env_key not in os.environ:
                val = dc[key]
                # Convert Python booleans to lowercase strings for Pydantic compat
                if isinstance(val, bool):
                    os.environ[env_key] = str(val).lower()
                else:
                    os.environ[env_key] = str(val)
    except Exception:
        # Never block engine startup — dynaconf is a convenience layer
        logging.getLogger(__name__).warning(
            "Failed to load dynaconf defaults from %s", _DYNACONF_SETTINGS_FILE, exc_info=True
        )


_load_dynaconf_defaults()


class ExecutionMode(StrEnum):
    """Engine execution mode (legacy — use EngineMode for new code)."""
    PAPER = "paper"
    SANDBOX = "sandbox"
    LIVE = "live"


class EngineMode(StrEnum):
    """Unified engine mode (Phase H-2 — industry standard 4-stage).

    Backtest → Paper → Live  (Shadow deprecated as of Phase I)

    Strategy/signal code is identical across all modes.
    Only DataFeed and Executor differ per mode.

    Migration guide:
      - EngineMode.SHADOW → EngineMode.LIVE (with small capital / paper execution)
      - Set ENGINE_MODE=live and LIVE_GATE_BYPASS=true for canary testing
    """
    BACKTEST = "backtest"   # Historical data + SimExecutor
    PAPER = "paper"         # Live WS data + SimExecutor (= old "shadow")
    SHADOW = "shadow"       # DEPRECATED: use EngineMode.LIVE with paper execution
    LIVE = "live"           # Live WS data + AtomicExecutor full capital


def resolve_engine_mode(
    execution_mode: str | None = None,
    data_mode: str | None = None,
    engine_mode: str | None = None,
) -> EngineMode:
    """Resolve EngineMode from legacy or new config (backward compatible).

    Priority: ENGINE_MODE env > engine.json "mode" > EXECUTION_MODE+DATA_MODE

    SAFETY RULE: If engine.json/ENGINE_MODE says "live" but EXECUTION_MODE env
    says "paper", this is a conflict — raise RuntimeError to prevent accidental
    live trading when the user believes they are in simulation mode.

    Legacy mapping:
      paper + synthetic      → BACKTEST
      paper + shadow         → PAPER
      paper + real_public    → PAPER
      live + real_authenticated → LIVE
      sandbox + *            → PAPER
    """
    import os

    # engine.json "mode" 필드가 단일 소스 — .env에 EXECUTION_MODE 없음
    # Priority: engine_mode (engine.json) > ENGINE_MODE env var > legacy fallback
    em = engine_mode or os.getenv("ENGINE_MODE", "")
    if em:
        try:
            resolved = EngineMode(em.lower())
            if resolved == EngineMode.SHADOW:
                raise RuntimeError(
                    "EngineMode.SHADOW is removed. Use mode=paper or mode=live in engine.json."
                )
            return resolved
        except ValueError:
            pass

    # Legacy fallback (EXECUTION_MODE/DATA_MODE — deprecated, use engine.json instead)
    exec_m = (execution_mode or os.getenv("EXECUTION_MODE", "paper")).lower()
    data_m = (data_mode or os.getenv("DATA_MODE", "synthetic")).lower()

    if exec_m == "live":
        return EngineMode.LIVE
    if exec_m == "sandbox":
        return EngineMode.PAPER
    # exec_m == "paper"
    if data_m == "shadow":
        return EngineMode.PAPER
    if data_m in ("real_public", "real_authenticated"):
        return EngineMode.PAPER
    # synthetic or unknown
    return EngineMode.BACKTEST


class CapitalTierConfig(BaseSettings):
    """Capital tier settings for phased deployment."""
    model_config = SettingsConfigDict(env_prefix="CAPITAL_")

    tier: str = Field(default="alpha", description="alpha|beta|production")
    initial_capital: Decimal = Field(
        default=Decimal("70"),
        description="Initial capital per exchange in USD",
    )


class RedisSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REDIS_")

    url: str = Field(default="redis://localhost:6379/0", description="Redis connection URL")
    max_connections: int = Field(default=50, ge=1, description="Connection pool max size")
    socket_timeout: float = Field(default=2.0, ge=0.1, description="Socket timeout in seconds")
    socket_connect_timeout: float = Field(default=2.0, ge=0.1)


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", populate_by_name=True)

    url: str = Field(
        default="postgresql+asyncpg://leviathan:leviathan@localhost:5432/leviathan",
        alias="DATABASE_URL",
    )
    pool_size: int = Field(default=20, ge=1)
    pool_timeout: float = Field(default=5.0, ge=0.1)
    pool_max_overflow: int = Field(default=10, ge=0)


class ExchangeSettings(BaseSettings):
    """Per-exchange configuration."""

    model_config = SettingsConfigDict(env_prefix="", populate_by_name=True)

    # Binance
    binance_api_key: str = Field(default="", alias="BINANCE_API_KEY")
    binance_api_secret: str = Field(default="", alias="BINANCE_API_SECRET")
    binance_testnet: bool = Field(default=False, alias="BINANCE_TESTNET")
    binance_rate_limit: int = Field(default=1200, alias="BINANCE_RATE_LIMIT")  # req/min

    # OKX
    okx_api_key: str = Field(default="", alias="OKX_API_KEY")
    okx_api_secret: str = Field(default="", alias="OKX_API_SECRET")
    okx_passphrase: str = Field(default="", alias="OKX_PASSPHRASE")
    okx_testnet: bool = Field(default=False, alias="OKX_TESTNET")
    okx_rate_limit: int = Field(default=600, alias="OKX_RATE_LIMIT")

    # Bybit
    bybit_api_key: str = Field(default="", alias="BYBIT_API_KEY")
    bybit_api_secret: str = Field(default="", alias="BYBIT_API_SECRET")
    bybit_testnet: bool = Field(default=False, alias="BYBIT_TESTNET")
    bybit_rate_limit: int = Field(default=600, alias="BYBIT_RATE_LIMIT")

    # Bitget (US-359)
    bitget_api_key: str = Field(default="", alias="BITGET_API_KEY")
    bitget_api_secret: str = Field(default="", alias="BITGET_API_SECRET")
    bitget_passphrase: str = Field(default="", alias="BITGET_PASSPHRASE")
    bitget_testnet: bool = Field(default=False, alias="BITGET_TESTNET")

    # Upbit (US-359)
    upbit_access_key: str = Field(default="", alias="UPBIT_ACCESS_KEY")
    upbit_secret_key: str = Field(default="", alias="UPBIT_SECRET_KEY")

    # Bithumb (US-359)
    bithumb_api_key: str = Field(default="", alias="BITHUMB_API_KEY")
    bithumb_api_secret: str = Field(default="", alias="BITHUMB_API_SECRET")

    # Coinone (US-359)
    coinone_access_token: str = Field(default="", alias="COINONE_ACCESS_TOKEN")
    coinone_api_secret: str = Field(default="", alias="COINONE_API_SECRET")

    # Tier4 — API 키 추후 발급, 어댑터는 Phase K에서 미리 구현 (US-359)
    mexc_api_key: str = Field(default="", alias="MEXC_API_KEY")
    mexc_api_secret: str = Field(default="", alias="MEXC_API_SECRET")
    gateio_api_key: str = Field(default="", alias="GATEIO_API_KEY")
    gateio_api_secret: str = Field(default="", alias="GATEIO_API_SECRET")
    bingx_api_key: str = Field(default="", alias="BINGX_API_KEY")
    bingx_api_secret: str = Field(default="", alias="BINGX_API_SECRET")
    lbank_api_key: str = Field(default="", alias="LBANK_API_KEY")
    lbank_api_secret: str = Field(default="", alias="LBANK_API_SECRET")
    orangex_api_key: str = Field(default="", alias="ORANGEX_API_KEY")
    orangex_api_secret: str = Field(default="", alias="ORANGEX_API_SECRET")


class RiskSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RISK_")

    max_position_pct: Decimal = Field(
        default=Decimal("0.10"),
        description="Max % of capital per position (0.10 = 10%)",
    )
    max_drawdown_pct: Decimal = Field(
        default=Decimal("0.02"),
        description="Max drawdown before circuit breaker triggers (0.02 = 2%)",
    )
    max_single_trade_pct: Decimal = Field(
        default=Decimal("0.05"),
        description="Max single trade size as % of capital",
    )
    max_exposure_pct: Decimal = Field(
        default=Decimal("0.30"),
        description="Max total exposure across all positions",
    )
    kill_switch_enabled: bool = Field(default=True)
    circuit_breaker_cooldown_seconds: int = Field(default=300, ge=1)
    circuit_breaker_consecutive_losses: int = Field(default=3, ge=1)
    circuit_breaker_api_error_rate: Decimal = Field(default=Decimal("0.20"))
    exchange_health_threshold: Decimal = Field(default=Decimal("0.9"))
    max_volatility_multiple: Decimal = Field(
        default=Decimal("2.0"),
        description="Skip trade if 1-min vol > N * 24h avg vol",
    )
    max_rollback_threshold: Decimal = Field(
        default=Decimal("0.02"),
        description="Reject if max_rollback_cost > threshold * position_value (Amendment 3C)",
    )

    @field_validator(
        "max_position_pct",
        "max_drawdown_pct",
        "max_single_trade_pct",
        "max_exposure_pct",
        mode="before",
    )
    @classmethod
    def validate_pct(cls, v: str | Decimal) -> Decimal:
        d = Decimal(str(v))
        if not (Decimal("0") < d <= Decimal("1")):
            msg = f"Percentage must be between 0 and 1 (exclusive), got {d}"
            raise ValueError(msg)
        return d


class TradingSettings(BaseSettings):
    """Trading pair and exchange configuration."""
    model_config = SettingsConfigDict(env_prefix="TRADING_")

    symbols: list[str] = Field(
        default=["auto"],
        description="Trading pairs. ['auto'] = dynamic discovery at startup via exchange APIs.",
    )
    symbol_min_exchanges: int = Field(
        default=3,
        description="Min exchanges a symbol must be listed on for auto-discovery (3=~175, 2=~300+)",
    )
    active_exchanges: list[str] = Field(
        default=["binance", "bybit", "okx", "bitget"],
        description="Exchange IDs to connect to",
    )
    shadow_strategy_id: str = Field(
        default="shadow_arb_v1",
        description="Strategy ID for shadow mode",
    )
    use_native_adapters: bool = Field(
        default=False,
        alias="USE_NATIVE_ADAPTERS",
        description="[DEPRECATED BUG-151] Obsolete — native adapters always used. Field retained for backward compat but value ignored.",
    )


class LiveGateSettings(BaseSettings):
    """Live gate evaluation thresholds."""
    model_config = SettingsConfigDict(env_prefix="LIVE_GATE_")

    sharpe_threshold: Decimal = Field(
        default=Decimal("2.5"),
        description="Minimum 7-day rolling Sharpe ratio",
    )
    mdd_threshold: Decimal = Field(
        default=Decimal("0.05"),
        description="Maximum drawdown fraction (0.05 = 5%)",
    )
    min_signals_per_day: int = Field(
        default=100,
        description="Minimum signals per day for live eligibility",
    )
    evaluation_days: int = Field(
        default=7,
        description="Number of days for walk-forward evaluation",
    )
    min_exchange_health: Decimal = Field(
        default=Decimal("0.95"),
        description="Minimum exchange health score",
    )
    reevaluation_interval_hours: int = Field(
        default=24,
        description="Hours between auto-evaluations",
    )
    bypass: bool = Field(
        default=False,
        description="Bypass LiveGate for small-amount testing. Set LIVE_GATE_BYPASS=true.",
    )


class ExecutionSettings(BaseSettings):
    """Atomic executor timing configuration."""
    model_config = SettingsConfigDict(env_prefix="", populate_by_name=True)

    leg_timeout_ms: int = Field(
        default=5000,
        alias="LEG_TIMEOUT_MS",
        description="Timeout for each leg fill confirmation (ms). Must exceed Binance futures polling (3×200ms + REST = ~1500ms)",
    )
    rollback_timeout_ms: int = Field(
        default=2000,
        alias="ROLLBACK_TIMEOUT_MS",
        description="Timeout for rollback market order (ms)",
    )
    reconciliation_interval_s: int = Field(
        default=5,
        alias="RECONCILIATION_INTERVAL_S",
        description="Post-trade reconciliation delay (seconds)",
    )


class MonitoringSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MONITORING_")

    prometheus_port: int = Field(default=8000, ge=1024, le=65535)
    log_level: str = Field(default="INFO")
    log_format: Literal["json", "console"] = Field(default="json")

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid:
            msg = f"log_level must be one of {valid}, got {v!r}"
            raise ValueError(msg)
        return upper


class OperationalSettings(BaseSettings):
    """Fine-grained operational parameters loaded from environment variables.

    Centralises all os.getenv() calls that previously appeared inline across
    main.py, modes/, and core/ — collected here so every module can call
    ``get_settings().operational.<field>`` instead of raw ``os.getenv()``.
    """

    model_config = SettingsConfigDict(env_prefix="", populate_by_name=True, extra="ignore")

    # API server
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="PORT")

    # BTC reference price (USD) for USDT→BTC conversion
    btc_reference_price: Decimal = Field(default=Decimal("50000"), alias="BTC_REFERENCE_PRICE")

    # Database
    database_url: str = Field(
        default="postgresql://leviathan:leviathan@localhost:5432/leviathan",
        alias="DATABASE_URL",
    )

    # Signal pipeline
    min_edge_bps: int = Field(default=5, alias="MIN_EDGE_BPS")
    max_spread_pct: float = Field(default=0.05, alias="MAX_SPREAD_PCT")
    signal_cooldown_sec: float = Field(default=2.0, alias="SIGNAL_COOLDOWN_SEC")
    min_price_usd: Decimal = Field(default=Decimal("0.10"), alias="MIN_PRICE_USD")
    signal_min_volume_usd: Decimal = Field(default=Decimal("0"), alias="SIGNAL_MIN_VOLUME_USD")

    # Stale orderbook detection
    stale_cross_deviation_pct: float = Field(default=0.10, alias="STALE_CROSS_DEVIATION_PCT")
    stale_blacklist_ttl_s: float = Field(default=300.0, alias="STALE_BLACKLIST_TTL_S")

    # Shadow mode — slippage & balance
    powerlaw_slippage_k: float = Field(default=0.0, alias="POWERLAW_SLIPPAGE_K")
    paper_fallback_slippage_bps: Decimal = Field(default=Decimal("10"), alias="PAPER_FALLBACK_SLIPPAGE_BPS")
    paper_depth_penalty_multiplier: float = Field(default=2.0, alias="PAPER_DEPTH_PENALTY_MULTIPLIER")
    paper_initial_balance_usdt: Decimal = Field(default=Decimal("10000000"), alias="PAPER_INITIAL_BALANCE_USDT")
    paper_rebalance_threshold_pct: Decimal = Field(default=Decimal("0.10"), alias="PAPER_REBALANCE_THRESHOLD_PCT")
    paper_depth_fraction: Decimal = Field(default=Decimal("1.0"), alias="PAPER_DEPTH_FRACTION")
    paper_max_trade_size: Decimal = Field(default=Decimal("100"), alias="PAPER_MAX_TRADE_SIZE")
    paper_max_loss_per_trade_usd: Decimal = Field(default=Decimal("10"), alias="PAPER_MAX_LOSS_PER_TRADE_USD")
    strategy_loss_cap_json: str = Field(default="", alias="STRATEGY_LOSS_CAP_JSON")
    paper_single_loss_disable_seconds: float = Field(default=0.0, alias="PAPER_SINGLE_LOSS_DISABLE_SECONDS")
    paper_reconcile_interval_s: float = Field(default=60.0, alias="PAPER_RECONCILE_INTERVAL_S")
    paper_disabled_strategies: str = Field(default="", alias="PAPER_DISABLED_STRATEGIES")
    paper_strategy_filter: str = Field(default="", alias="PAPER_STRATEGY_FILTER")
    paper_mock_dex: bool = Field(default=False, alias="PAPER_MOCK_DEX")
    paper_progressive: bool = Field(default=False, alias="PAPER_PROGRESSIVE")
    paper_partial_fill_rate: Decimal = Field(default=Decimal("0.05"), alias="PAPER_PARTIAL_FILL_RATE")
    paper_rejection_rate: Decimal = Field(default=Decimal("0.02"), alias="PAPER_REJECTION_RATE")
    paper_leg_delay_min_ms: float = Field(default=50.0, alias="PAPER_LEG_DELAY_MIN_MS")
    paper_leg_delay_max_ms: float = Field(default=300.0, alias="PAPER_LEG_DELAY_MAX_MS")

    # KRW/USDT rate
    krw_usdt_rate: float = Field(default=1380.0, alias="KRW_USDT_RATE")

    # DEX
    dex_rpc_url: str = Field(default="", alias="DEX_RPC_URL")
    dex_pool_address: str = Field(default="", alias="DEX_POOL_ADDRESS")

    # Execution
    execution_mode: str = Field(default="paper", alias="EXECUTION_MODE")
    data_mode: str = Field(default="synthetic", alias="DATA_MODE")
    strategy_validation: bool = Field(default=False, alias="STRATEGY_VALIDATION")

    # Capital allocator
    capital_allocator_enabled: bool = Field(default=True, alias="CAPITAL_ALLOCATOR_ENABLED")
    max_position_usd: float = Field(default=10000.0, alias="MAX_POSITION_USD")
    portfolio_risk_enabled: bool = Field(default=True, alias="PORTFOLIO_RISK_ENABLED")

    # Inline tuner
    enable_inline_tuner: str = Field(default="", alias="ENABLE_INLINE_TUNER")

    # Inventory rebalancer
    rebalancer_deviation_threshold: float = Field(default=0.30, alias="REBALANCER_DEVIATION_THRESHOLD")
    rebalancer_check_interval_s: float = Field(default=14400.0, alias="REBALANCER_CHECK_INTERVAL_S")
    rebalancer_min_transfer_usd: float = Field(default=50.0, alias="REBALANCER_MIN_TRANSFER_USD")

    # Adaptive threshold
    adaptive_threshold_interval_s: float = Field(default=3600.0, alias="ADAPTIVE_THRESHOLD_INTERVAL_S")

    # Reconciliation
    reconciliation_interval_s: float = Field(default=5.0, alias="RECONCILIATION_INTERVAL_S")

    # Bithumb refresh
    bithumb_refresh_interval_s: float = Field(default=60.0, alias="BITHUMB_REFRESH_INTERVAL_S")

    # Funding rate
    funding_rate_interval_s: float = Field(default=60.0, alias="FUNDING_RATE_INTERVAL_S")

    # Live mode
    live_single_loss_disable_seconds: float = Field(default=600.0, alias="LIVE_SINGLE_LOSS_DISABLE_SECONDS")
    live_max_loss_per_trade_usd: Decimal = Field(default=Decimal("10"), alias="LIVE_MAX_LOSS_PER_TRADE_USD")

    # LiveGate continuous monitor
    live_gate_continuous_enabled: bool = Field(default=True, alias="LIVE_GATE_CONTINUOUS_ENABLED")
    live_gate_monitor_interval_s: int = Field(default=60, alias="LIVE_GATE_MONITOR_INTERVAL_S")
    live_gate_pause_threshold: int = Field(default=3, alias="LIVE_GATE_PAUSE_THRESHOLD")

    # Market impact
    market_impact_eta: str = Field(default="", alias="MARKET_IMPACT_ETA")
    market_impact_enabled: bool = Field(default=True, alias="MARKET_IMPACT_ENABLED")
    market_impact_min_adv: float = Field(default=1000.0, alias="MARKET_IMPACT_MIN_ADV")

    # Data quality (freshness thresholds)
    freshness_futures_s: float = Field(default=0.5, alias="FRESHNESS_FUTURES_S")
    freshness_default_s: float = Field(default=1.0, alias="FRESHNESS_DEFAULT_S")
    freshness_korean_s: float = Field(default=2.0, alias="FRESHNESS_KOREAN_S")
    freshness_bithumb_s: float = Field(default=1.0, alias="FRESHNESS_BITHUMB_S")

    # Data quality (Bithumb-specific)
    bithumb_deviation_pct: float = Field(default=0.05, alias="BITHUMB_DEVIATION_PCT")
    bithumb_large_deviation_mult: float = Field(default=1.0, alias="BITHUMB_LARGE_DEVIATION_MULT")
    bithumb_blacklist_ttl_s: float = Field(default=600.0, alias="BITHUMB_BLACKLIST_TTL_S")

    # Anomaly detection
    anomaly_window: int = Field(default=100, alias="ANOMALY_WINDOW")
    anomaly_z_threshold: float = Field(default=4.0, alias="ANOMALY_Z_THRESHOLD")
    anomaly_isolation_s: float = Field(default=3.0, alias="ANOMALY_ISOLATION_S")
    anomaly_warmup: int = Field(default=10, alias="ANOMALY_WARMUP")

    # Strategy validation
    strategy_validation_duration_s: int = Field(default=600, alias="STRATEGY_VALIDATION_DURATION_S")
    strategy_validation_combined_duration_s: int = Field(default=600, alias="STRATEGY_VALIDATION_COMBINED_DURATION_S")
    strategy_validation_min_trades: int = Field(default=5, alias="STRATEGY_VALIDATION_MIN_TRADES")
    strategy_validation_hydration_s: int = Field(default=30, alias="STRATEGY_VALIDATION_HYDRATION_S")
    strategy_activation_path: str = Field(default="config/strategy_activation.json", alias="STRATEGY_ACTIVATION_PATH")

    # Engine core loop intervals
    engine_reconcile_interval: int = Field(default=60, alias="ENGINE_RECONCILE_INTERVAL")
    engine_health_check_interval: int = Field(default=10, alias="ENGINE_HEALTH_CHECK_INTERVAL")
    engine_heartbeat_interval: int = Field(default=5, alias="ENGINE_HEARTBEAT_INTERVAL")
    engine_shutdown_timeout: int = Field(default=10, alias="ENGINE_SHUTDOWN_TIMEOUT")

    # Atomic executor (US-275)
    partial_fill_timeout_s: float = Field(default=30.0, alias="PARTIAL_FILL_TIMEOUT_S")
    max_loss_pct: float = Field(default=2.0, alias="MAX_LOSS_PCT")
    enable_partial_fill_stop: bool = Field(default=True, alias="ENABLE_PARTIAL_FILL_STOP")
    enable_depth_sizing: bool = Field(default=True, alias="ENABLE_DEPTH_SIZING")
    min_order_size: str = Field(default="0.001", alias="MIN_ORDER_SIZE")

    # Triangular scanner
    enable_triangular_cost: bool = Field(default=False, alias="ENABLE_TRIANGULAR_COST")
    triangular_cross_pairs: str = Field(default="ETH/BTC,SOL/BTC,SOL/ETH", alias="TRIANGULAR_CROSS_PAIRS")

    # API server
    cors_origins: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000",
        alias="CORS_ORIGINS",
    )

    # LiveGate continuous (raw string for env override)
    live_gate_continuous_raw: str = Field(default="1", alias="LIVE_GATE_CONTINUOUS")

    # Capital allocator
    regime_aware_allocation_enabled: bool = Field(default=True, alias="REGIME_AWARE_ALLOCATION_ENABLED")

    # Signal producer
    # BUG-46: 1.5s too tight for Bitget event-driven books15 WS (quiet periods up to 3s normal).
    # 3s freshness guard (book.last_update_time) is already the primary stale-book protection.
    # FF stale gate fix (2026-04-26): 5s caused 100% futures_futures drop (stale==pairs sigs=0).
    # 30s acts as reconnect-detection fallback (per-exchange global last_update); per-book 3s remains primary.
    exchange_stale_threshold_s: float = Field(default=30.0, alias="EXCHANGE_STALE_THRESHOLD_S")
    spot_futures_min_basis_bps: float = Field(default=5.0, alias="SPOT_FUTURES_MIN_BASIS_BPS")


class Settings(BaseSettings):
    """Top-level settings — loads all sub-settings from environment."""

    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parents[3] / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    engine_env: Literal["dev", "staging", "prod", "test"] = Field(
        default="dev", alias="ENGINE_ENV"
    )
    execution_mode: str = Field(
        default="paper", alias="EXECUTION_MODE",
        description="Legacy — use engine_mode from config/engine.json instead."
    )
    # Phase H-2: Unified engine mode (backtest/paper/shadow/live)
    engine_mode: EngineMode | None = Field(
        default=None, alias="ENGINE_MODE",
        description="Unified mode from config/engine.json. The single source of truth for mode."
    )
    capital: CapitalTierConfig = Field(default_factory=CapitalTierConfig)

    redis: RedisSettings = Field(default_factory=RedisSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    exchange: ExchangeSettings = Field(default_factory=ExchangeSettings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
    trading: TradingSettings = Field(default_factory=TradingSettings)
    live_gate: LiveGateSettings = Field(default_factory=LiveGateSettings)
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)
    monitoring: MonitoringSettings = Field(default_factory=MonitoringSettings)
    operational: OperationalSettings = Field(default_factory=OperationalSettings)

    @model_validator(mode="after")
    def validate_prod_keys(self) -> "Settings":
        if self.engine_env == "prod":
            missing = []
            if not self.exchange.binance_api_key:
                missing.append("BINANCE_API_KEY")
            if not self.exchange.okx_api_key:
                missing.append("OKX_API_KEY")
            if missing:
                msg = f"Production requires exchange API keys: {missing}"
                raise ValueError(msg)
        return self


_settings: Settings | None = None


def _apply_engine_json_overrides(settings: Settings) -> None:
    """Override Pydantic defaults with engine.json runtime values.

    engine.json is the single source of truth for runtime configuration.
    .env / Pydantic handles secrets (API keys) only.
    Logs every override for traceability.
    """
    ecfg = load_engine_config()
    if not ecfg:
        return

    _log = logging.getLogger(__name__)
    _overrides: list[str] = []

    # --- exchanges.active → trading.active_exchanges ---
    _exchanges_active = ecfg.get("exchanges", {}).get("active")
    if _exchanges_active and _exchanges_active != settings.trading.active_exchanges:
        _overrides.append(
            f"trading.active_exchanges: {settings.trading.active_exchanges} → {_exchanges_active}"
        )
        settings.trading.active_exchanges = _exchanges_active

    # --- risk section ---
    _risk = ecfg.get("risk", {})
    _use_pct = _risk.get("use_percentage", False)

    if _use_pct and "max_position_pct" in _risk:
        _new = Decimal(str(_risk["max_position_pct"])) / Decimal("100")
        if _new != settings.risk.max_position_pct:
            _overrides.append(f"risk.max_position_pct: {settings.risk.max_position_pct} → {_new}")
            settings.risk.max_position_pct = _new

    if _use_pct and "max_daily_loss_pct" in _risk:
        _new = Decimal(str(_risk["max_daily_loss_pct"])) / Decimal("100")
        if _new != settings.risk.max_drawdown_pct:
            _overrides.append(f"risk.max_drawdown_pct: {settings.risk.max_drawdown_pct} → {_new}")
            settings.risk.max_drawdown_pct = _new

    if "circuit_breaker_consecutive_loss_limit" in _risk:
        _new = int(_risk["circuit_breaker_consecutive_loss_limit"])
        if _new != settings.risk.circuit_breaker_consecutive_losses:
            _overrides.append(
                f"risk.circuit_breaker_consecutive_losses: "
                f"{settings.risk.circuit_breaker_consecutive_losses} → {_new}"
            )
            settings.risk.circuit_breaker_consecutive_losses = _new

    if "min_edge_bps" in _risk:
        _new = int(_risk["min_edge_bps"])
        if _new != settings.operational.min_edge_bps:
            _overrides.append(f"operational.min_edge_bps: {settings.operational.min_edge_bps} → {_new}")
            settings.operational.min_edge_bps = _new

    # WS-1.4: 3 missing overrides — circuit_breaker_cooldown, api_error_rate, mdd_threshold
    if "circuit_breaker_cooldown_seconds" in _risk:
        _new = int(float(_risk["circuit_breaker_cooldown_seconds"]))
        if _new != settings.risk.circuit_breaker_cooldown_seconds:
            _overrides.append(
                f"risk.circuit_breaker_cooldown_seconds: "
                f"{settings.risk.circuit_breaker_cooldown_seconds} → {_new}"
            )
            settings.risk.circuit_breaker_cooldown_seconds = _new

    if "circuit_breaker_api_error_rate_threshold" in _risk:
        _new = Decimal(str(_risk["circuit_breaker_api_error_rate_threshold"]))
        if _new != settings.risk.circuit_breaker_api_error_rate:
            _overrides.append(
                f"risk.circuit_breaker_api_error_rate: "
                f"{settings.risk.circuit_breaker_api_error_rate} → {_new}"
            )
            settings.risk.circuit_breaker_api_error_rate = _new

    # --- live_gate section ---
    _lg = ecfg.get("live_gate", {})

    if "bypass" in _lg and _lg["bypass"] != settings.live_gate.bypass:
        _overrides.append(f"live_gate.bypass: {settings.live_gate.bypass} → {_lg['bypass']}")
        settings.live_gate.bypass = _lg["bypass"]

    if "sharpe_threshold" in _lg:
        _new = Decimal(str(_lg["sharpe_threshold"]))
        if _new != settings.live_gate.sharpe_threshold:
            _overrides.append(f"live_gate.sharpe_threshold: {settings.live_gate.sharpe_threshold} → {_new}")
            settings.live_gate.sharpe_threshold = _new

    if "min_signals_per_day" in _lg:
        _new = int(_lg["min_signals_per_day"])
        if _new != settings.live_gate.min_signals_per_day:
            _overrides.append(
                f"live_gate.min_signals_per_day: {settings.live_gate.min_signals_per_day} → {_new}"
            )
            settings.live_gate.min_signals_per_day = _new

    if "evaluation_days" in _lg:
        _new = int(_lg["evaluation_days"])
        if _new != settings.live_gate.evaluation_days:
            _overrides.append(f"live_gate.evaluation_days: {settings.live_gate.evaluation_days} → {_new}")
            settings.live_gate.evaluation_days = _new

    if "mdd_threshold" in _lg:
        _new = Decimal(str(_lg["mdd_threshold"]))
        if _new != settings.live_gate.mdd_threshold:
            _overrides.append(f"live_gate.mdd_threshold: {settings.live_gate.mdd_threshold} → {_new}")
            settings.live_gate.mdd_threshold = _new

    if "continuous_enabled" in _lg:
        _new_bool = bool(_lg["continuous_enabled"])
        if _new_bool != settings.operational.live_gate_continuous_enabled:
            _overrides.append(
                f"operational.live_gate_continuous_enabled: "
                f"{settings.operational.live_gate_continuous_enabled} → {_new_bool}"
            )
            settings.operational.live_gate_continuous_enabled = _new_bool

    # --- capital.tier ---
    _cap_tier = ecfg.get("capital", {}).get("tier")
    if _cap_tier and _cap_tier != settings.capital.tier:
        _overrides.append(f"capital.tier: {settings.capital.tier} → {_cap_tier}")
        settings.capital.tier = _cap_tier

    # --- execution.leg_timeout_ms ---
    _exec = ecfg.get("execution", {})
    if "leg_timeout_ms" in _exec:
        _new = int(_exec["leg_timeout_ms"])
        if _new != settings.execution.leg_timeout_ms:
            _overrides.append(f"execution.leg_timeout_ms: {settings.execution.leg_timeout_ms} → {_new}")
            settings.execution.leg_timeout_ms = _new

    if _overrides:
        _log.info(
            "engine.json overrides applied (%d): %s",
            len(_overrides),
            "; ".join(_overrides),
        )


def get_settings() -> Settings:
    """Return cached settings singleton with engine.json overrides applied."""
    global _settings
    if _settings is None:
        _settings = Settings()
        _apply_engine_json_overrides(_settings)
    return _settings


# Path: engine/config/engine.json — WS-1 단일 소스 (MAJOR-3: load_trading_config 제거, trading.json 폐기)
_ENGINE_JSON_PATH = Path(__file__).parent.parent.parent / "config" / "engine.json"


def load_engine_config() -> dict:
    """Load engine runtime config from config/engine.json.

    Contains mode, risk params, exchange list, strategy params — non-secret.
    Returns empty dict if missing (backward compatible with .env-only setup).
    """
    if not _ENGINE_JSON_PATH.exists():
        return {}
    try:
        with _ENGINE_JSON_PATH.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        import logging
        logging.getLogger(__name__).warning("Failed to load engine.json: %s", exc)
        return {}
