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
        _env_file = _ENGINE_ROOT / ".env"
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
    """Engine execution mode."""
    PAPER = "paper"       # No API keys, synthetic data, InMemoryEventBus
    SANDBOX = "sandbox"   # Testnet API keys, real data, paper execution
    LIVE = "live"         # Real API keys, real data, real execution


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
        description="Use native (ccxt-free) exchange adapters when True",
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


class ExecutionSettings(BaseSettings):
    """Atomic executor timing configuration."""
    model_config = SettingsConfigDict(env_prefix="", populate_by_name=True)

    leg_timeout_ms: int = Field(
        default=1000,
        alias="LEG_TIMEOUT_MS",
        description="Timeout for each leg fill confirmation (ms)",
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


class Settings(BaseSettings):
    """Top-level settings — loads all sub-settings from environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    engine_env: Literal["dev", "staging", "prod", "test"] = Field(
        default="dev", alias="ENGINE_ENV"
    )
    execution_mode: ExecutionMode = Field(
        default=ExecutionMode.PAPER, alias="EXECUTION_MODE"
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


def get_settings() -> Settings:
    """Return cached settings singleton."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


# Path: engine/config/trading.json (two levels up from this file's src/core/)
_TRADING_JSON_PATH = Path(__file__).parent.parent.parent / "config" / "trading.json"


def load_trading_config() -> dict:
    """Load non-sensitive trading config from engine/config/trading.json.

    Returns empty dict if the file is absent or malformed — engine falls
    back to .env values and hardcoded defaults (backward compatible).
    Priority: env var > trading.json > hardcoded default.
    """
    if not _TRADING_JSON_PATH.exists():
        return {}
    try:
        with _TRADING_JSON_PATH.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        import logging
        logging.getLogger(__name__).warning("Failed to load trading.json: %s", exc)
        return {}
