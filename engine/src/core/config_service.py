"""ConfigService — Pydantic-validated EngineConfig schema + singleton accessor.

Path-B Day 4 (Phoenix): Standalone config service. Opt-in; does NOT replace
``src/core/config_loader.py::get_config`` yet. Day 5 will wire it in.

Design goals:
  - Single typed schema mirror of ``engine/config/engine.json``
  - Validate at boot (numeric bounds, exchange whitelist, cross-field constraints)
  - Dotted-path accessor preserves backwards-compat read patterns
  - ``reload()`` emits an asyncio event consumers can await
  - Tolerant to ``_bug_*_note`` comment keys via ``extra="allow"``

This module MUST NOT import ``main.py``, ``modes.live``, or existing loaders.
"""
from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Known exchange ids — whitelist for validation
# ---------------------------------------------------------------------------

KNOWN_EXCHANGES: frozenset[str] = frozenset(
    {
        "binance",
        "binance_futures",
        "bitget",
        "bitget_futures",
        "bybit",
        "bybit_futures",
        "okx",
        "okx_futures",
        "upbit",
        "bithumb",
        "coinone",
        "gateio",
        "mexc",
        "lbank",
        "orangex",
        "bingx",
    }
)


# ---------------------------------------------------------------------------
# Nested models — each allows extras to accept ``_comment`` / ``_bug_*_note``
# ---------------------------------------------------------------------------

_EXTRA_ALLOW = ConfigDict(extra="allow")


class StrategyAllocation(BaseModel):
    model_config = _EXTRA_ALLOW

    allocation_pct: float = Field(..., ge=0, le=100)


class CapitalTier(BaseModel):
    model_config = _EXTRA_ALLOW

    initial_usd: float | None = Field(default=None, ge=0)
    futures_usd: float | None = Field(default=None, ge=0)
    spot_usd: float | None = Field(default=None, ge=0)
    spot_krw: float | None = Field(default=None, ge=0)


class CapitalConfig(BaseModel):
    model_config = _EXTRA_ALLOW

    allocation_mode: str = "percentage"
    reserve_pct: float = Field(default=20, ge=0, le=100)
    strategies: dict[str, StrategyAllocation] = Field(default_factory=dict)
    tier: str = "step2_1"
    tiers: dict[str, CapitalTier] = Field(default_factory=dict)


class ExchangesConfig(BaseModel):
    model_config = _EXTRA_ALLOW

    active: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_whitelist(self) -> "ExchangesConfig":
        unknown = [e for e in self.active if e not in KNOWN_EXCHANGES]
        if unknown:
            raise ValueError(
                f"exchanges.active contains unknown exchange(s): {unknown}. "
                f"Known: {sorted(KNOWN_EXCHANGES)}"
            )
        return self


class StrategyFiltersConfig(BaseModel):
    model_config = _EXTRA_ALLOW

    krw_usdt_rate: float = Field(default=0.000676, gt=0)
    xe_krw_enabled: bool = True
    enable_holding_timeout: bool = True
    spot_futures_max_hold_seconds: int = Field(default=1800, ge=0)
    futures_max_hold_seconds: int = Field(default=1800, ge=0)
    futures_max_concurrent_positions: int = Field(default=4, ge=0)
    futures_min_spread_bps: float = Field(default=27, ge=0)
    futures_max_position_size_usdt: float = Field(default=20, ge=0)
    futures_adaptive_static_entry_bps: float = Field(default=60, ge=0)
    futures_futures_max_position_usd: float = Field(default=12, ge=0)
    futures_excluded_symbols: list[str] = Field(default_factory=list)
    futures_min_book_depth_usd: float = Field(default=1, ge=0)
    futures_max_book_age_s: float = Field(default=30, ge=0)
    cross_exchange_min_book_depth_usd: float = Field(default=10, ge=0)
    funding_zscore_threshold: float = -1
    funding_min_diff_bps: float = 2.0
    funding_max_entry_adverse_bps: float = -10
    funding_rate_max_positions: int = Field(default=2, ge=0)
    enable_stale_guard: bool = False
    stat_arb_z_threshold: float = 2.0
    stat_arb_cooldown_s: float = Field(default=120, ge=0)
    stat_arb_min_history: int = Field(default=60, ge=0)
    cross_exchange_max_spread_bps: float = Field(default=100, ge=0)
    funding_convergence_weight: float = Field(default=0.3, ge=0, le=1)
    enable_funding_convergence: bool = True
    enable_latency_budget: bool = False
    triangular_max_latency_ms: float = Field(default=500, ge=0)
    futures_min_edge_bps: float = Field(default=10, ge=0)
    max_market_impact_bps: float = Field(default=20, ge=0)


class RiskConfig(BaseModel):
    model_config = _EXTRA_ALLOW

    use_percentage: bool = True
    max_position_pct: float = Field(default=6.0, ge=0, le=100)
    max_daily_loss_pct: float = Field(default=50.0, ge=0, le=100)
    max_net_exposure_per_asset: float = Field(default=5000, ge=0)
    min_edge_bps: float = 0
    min_price_usd: float = Field(default=0.1, ge=0)
    max_rollback_threshold: float = Field(default=0.02, ge=0, le=1)
    circuit_breaker_mdd_threshold: float = Field(default=0.02, ge=0, le=1)
    circuit_breaker_consecutive_loss_limit: int = Field(default=5, ge=0)
    circuit_breaker_api_error_rate_threshold: float = Field(default=0.2, ge=0, le=1)
    circuit_breaker_cooldown_seconds: float = Field(default=300.0, ge=0)
    circuit_breaker_half_open_test_count: int = Field(default=3, ge=0)
    correlation_window: int = Field(default=30, ge=0)
    correlation_threshold: float = Field(default=0.7, ge=0, le=1)
    flash_guard_threshold_pct: float = Field(default=3.0, ge=0, le=100)
    flash_guard_window_s: float = Field(default=300, ge=0)
    flash_guard_cooldown_s: float = Field(default=60, ge=0)
    max_cumulative_slippage_bps: float = Field(default=100, ge=0)
    slippage_window_trades: int = Field(default=20, ge=0)
    warmup_seconds: float = Field(default=120.0, ge=0)
    max_net_exposure_pct: float = Field(default=30, ge=0, le=100)
    per_strategy_daily_loss_budget_pct: float = Field(default=2.0, ge=0, le=100)
    per_strategy_daily_loss_budget: dict[str, float] = Field(default_factory=dict)


class DynamicRiskConfig(BaseModel):
    model_config = _EXTRA_ALLOW

    base_position_pct: float = Field(default=5.0, ge=0, le=100)


class ExecutionConfig(BaseModel):
    model_config = _EXTRA_ALLOW

    bitget_account_mode: Literal["classic", "unified"] = "unified"
    preflight_auto_close_enabled: bool = True
    ws_order_enabled: bool = True
    bitget_ws_order_enabled: bool = True
    min_trade_notional_usd: float = Field(default=5, ge=1)
    default_futures_leverage: float = Field(default=5, ge=1)
    leg_timeout_ms: int = Field(default=15000, ge=0)
    max_concurrent_trades: int = Field(default=2, ge=0)
    symbol_cooldown_s: float = Field(default=30, ge=0)
    split_threshold_usd: float = Field(default=50, ge=0)
    split_max_chunks: int = Field(default=3, ge=1)
    split_delay_ms: int = Field(default=200, ge=0)
    limit_fallback_spread_bps: float = Field(default=30, ge=0)
    limit_fallback_timeout_ms: int = Field(default=5000, ge=0)


class BacktestConfig(BaseModel):
    model_config = _EXTRA_ALLOW

    latency_ms: int = Field(default=500, ge=0)


class LiveConfig(BaseModel):
    model_config = _EXTRA_ALLOW

    max_daily_loss_pct: float = Field(default=5.0, ge=0, le=100)


class LiveGateConfig(BaseModel):
    model_config = _EXTRA_ALLOW

    bypass: bool = True
    sharpe_threshold: float = 0.0
    mdd_threshold: float = Field(default=0.05, ge=0, le=1)
    min_signals_per_day: int = Field(default=0, ge=0)
    evaluation_days: int = Field(default=1, ge=0)
    continuous_enabled: bool = True


class DBConfig(BaseModel):
    model_config = _EXTRA_ALLOW

    host: str = "localhost"
    port: int = Field(default=5432, ge=0, le=65535)
    name: str = "leviathan"
    user: str = "leviathan"
    pool_size: int = Field(default=10, ge=0)
    max_overflow: int = Field(default=20, ge=0)
    flush_interval_ms: int = Field(default=100, ge=0)
    market_buffer_size: int = Field(default=1000, ge=0)


class MonitoringConfig(BaseModel):
    model_config = _EXTRA_ALLOW

    prometheus_port: int = Field(default=8000, ge=0, le=65535)
    monitor_interval_sec: float = Field(default=300, ge=0)


class SecurityConfig(BaseModel):
    model_config = _EXTRA_ALLOW

    dashboard_user: str = "admin"
    trusted_proxies: str = "127.0.0.1"
    allowed_ips: str = "127.0.0.1,::1"


class SlippageConfig(BaseModel):
    model_config = _EXTRA_ALLOW

    k_default: float = Field(default=1.0, ge=0)
    conservative_multiplier: float = Field(default=1.5, ge=0)
    gamma: float = Field(default=0.5, ge=0)
    gamma_calibrated: bool = False
    t0: float = Field(default=60.0, ge=0)


class ParallelCombination(BaseModel):
    model_config = _EXTRA_ALLOW

    id: str
    strategy: str
    capital_usd: float = Field(..., ge=0)
    description: str = ""
    exchange: str | None = None
    exchanges: list[str] | None = None


class ParallelRiskConfig(BaseModel):
    model_config = _EXTRA_ALLOW

    total_capital_usd: float = Field(default=30, ge=0)
    max_mdd_pct: float = Field(default=5.0, ge=0, le=100)
    kill_switch_enabled: bool = True
    cb_enabled: bool = True


class ParallelConfig(BaseModel):
    model_config = _EXTRA_ALLOW

    enabled: bool = True
    combinations: list[ParallelCombination] = Field(default_factory=list)
    risk: ParallelRiskConfig = Field(default_factory=ParallelRiskConfig)


# ---------------------------------------------------------------------------
# Top-level EngineConfig
# ---------------------------------------------------------------------------


class EngineConfig(BaseModel):
    """Strongly-typed root schema for ``engine/config/engine.json``."""

    model_config = _EXTRA_ALLOW

    mode: Literal["backtest", "paper", "live"]
    env: Literal["dev", "staging", "prod", "test"]
    capital: CapitalConfig
    exchanges: ExchangesConfig
    strategy_filters: StrategyFiltersConfig = Field(default_factory=StrategyFiltersConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    dynamic_risk: DynamicRiskConfig = Field(default_factory=DynamicRiskConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)
    live: LiveConfig = Field(default_factory=LiveConfig)
    live_gate: LiveGateConfig = Field(default_factory=LiveGateConfig)
    strategy_params: dict[str, Any] = Field(default_factory=dict)
    db: DBConfig = Field(default_factory=DBConfig)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    slippage: SlippageConfig = Field(default_factory=SlippageConfig)
    parallel: ParallelConfig | None = None

    @model_validator(mode="after")
    def validate_cross_field(self) -> "EngineConfig":
        """Enforce cross-field invariants that single-field bounds can't express."""
        # 1) risk floor must cover dynamic_risk base allocation
        if self.risk.max_position_pct < self.dynamic_risk.base_position_pct:
            raise ValueError(
                f"risk.max_position_pct ({self.risk.max_position_pct}) must be >= "
                f"dynamic_risk.base_position_pct ({self.dynamic_risk.base_position_pct}); "
                "dynamic base allocation cannot exceed the hard risk ceiling."
            )

        # 2) Strategy allocation budget must fit inside 100%
        total_alloc = sum(
            s.allocation_pct for s in self.capital.strategies.values()
        )
        if total_alloc > 100 + 1e-6:
            raise ValueError(
                f"Sum of capital.strategies[*].allocation_pct ({total_alloc:.2f}) exceeds 100%"
            )

        # 3) Execution min notional safety floor
        if self.execution.min_trade_notional_usd < 1:
            raise ValueError(
                f"execution.min_trade_notional_usd ({self.execution.min_trade_notional_usd}) "
                "must be >= 1 USD"
            )

        return self


# ---------------------------------------------------------------------------
# ConfigService — singleton loader with reload event
# ---------------------------------------------------------------------------


class ConfigService:
    """File-backed config loader with typed access + reload event.

    Usage::

        svc = ConfigService(Path("config/engine.json"))
        cfg = svc.load()                           # EngineConfig
        mdd = svc.get("risk.max_position_pct")     # dotted path
        await svc.on_change.wait()                 # consumer waits for reload
    """

    def __init__(self, config_path: Path) -> None:
        self._path = Path(config_path)
        self._current: EngineConfig | None = None
        self._raw: dict[str, Any] = {}
        self._lock = threading.Lock()
        # Lazily-created; asyncio.Event() requires a running loop on some builds.
        self._on_change: asyncio.Event | None = None

    @property
    def on_change(self) -> asyncio.Event:
        """Asyncio event set after each successful ``reload()``.

        Created lazily on first access so the service can be instantiated
        outside a running event loop (e.g. at import time).
        """
        if self._on_change is None:
            self._on_change = asyncio.Event()
        return self._on_change

    @property
    def current(self) -> EngineConfig:
        if self._current is None:
            return self.load()
        return self._current

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> EngineConfig:
        """Read + validate the config file. Returns the parsed EngineConfig."""
        with self._lock:
            text = self._path.read_text(encoding="utf-8")
            raw = json.loads(text)
            cfg = EngineConfig.model_validate(raw)
            self._raw = raw
            self._current = cfg
            logger.debug(
                "config_service.loaded",
                path=str(self._path),
                mode=cfg.mode,
                env=cfg.env,
            )
            return cfg

    def reload(self) -> EngineConfig:
        """Re-read config from disk and signal ``on_change`` consumers."""
        cfg = self.load()
        ev = self._on_change
        if ev is not None:
            try:
                ev.set()
            except RuntimeError:
                # No running loop — signal deferred until awaited.
                logger.debug("config_service.on_change_no_loop")
        logger.info("config_service.reloaded", path=str(self._path))
        return cfg

    def get(self, path: str, default: Any = None) -> Any:
        """Dotted-path lookup against the raw JSON tree (back-compat).

        ``svc.get("risk.max_position_pct")`` → ``6.0``
        ``svc.get("missing.nested.key", 42)`` → ``42``
        """
        if self._current is None:
            self.load()

        node: Any = self._raw
        for part in path.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node


# ---------------------------------------------------------------------------
# Global singleton accessor
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent.parent / "config" / "engine.json"
)

_singleton: ConfigService | None = None
_singleton_lock = threading.Lock()


def get_config_service(config_path: Path | None = None) -> ConfigService:
    """Return the process-wide ``ConfigService`` singleton.

    First call may override the config path; subsequent calls return the same
    instance regardless of path argument. Use ``reset_config_service()`` in
    tests to rebind.
    """
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = ConfigService(config_path or _DEFAULT_CONFIG_PATH)
                _singleton.load()
    return _singleton


def reset_config_service() -> None:
    """Clear the singleton (test-only helper)."""
    global _singleton
    with _singleton_lock:
        _singleton = None


__all__ = [
    "KNOWN_EXCHANGES",
    "EngineConfig",
    "CapitalConfig",
    "ExchangesConfig",
    "StrategyFiltersConfig",
    "RiskConfig",
    "DynamicRiskConfig",
    "ExecutionConfig",
    "BacktestConfig",
    "LiveConfig",
    "LiveGateConfig",
    "DBConfig",
    "MonitoringConfig",
    "SecurityConfig",
    "SlippageConfig",
    "ParallelConfig",
    "ConfigService",
    "get_config_service",
    "reset_config_service",
]
