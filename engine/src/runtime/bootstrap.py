"""Engine bootstrap — Phase 4-2 main.py 모듈화 2단계 (2026-04-26).

Extracted from main.py:388-810 (423 LOC):
- setup_signal_handlers / handle_signal
- apply_trading_json_defaults (static)
- init_config / validate_config / resolve_symbols
- init_infrastructure / init_database / init_telegram / init_rust_bridge
- init_tuner

각 함수는 ``engine: "Engine"`` 첫 인자. ``Engine`` 메서드는 thin wrapper.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.main import Engine

from src.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


def setup_signal_handlers(engine: "Engine") -> None:
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda: handle_signal(engine))
        except (NotImplementedError, RuntimeError):
            pass


def handle_signal(engine: "Engine") -> None:
    logger.warning("Shutdown signal received")
    engine._shutdown_event.set()


def apply_trading_json_defaults(cfg: dict) -> None:
    """Inject trading.json values as env var defaults (env vars take priority)."""

    def _setdefault(key: str, value: object) -> None:
        if key not in os.environ:
            os.environ[key] = json.dumps(value) if isinstance(value, list) else str(value)

    if "active_exchanges" in cfg:
        _setdefault("TRADING_ACTIVE_EXCHANGES", cfg["active_exchanges"])

    sym = cfg.get("symbol_discovery", {})
    if "min_exchanges" in sym:
        _setdefault("TRADING_SYMBOL_MIN_EXCHANGES", sym["min_exchanges"])

    exe = cfg.get("execution", {})
    for _k, _env in [
        ("leg_timeout_ms", "LEG_TIMEOUT_MS"),
        ("rollback_timeout_ms", "ROLLBACK_TIMEOUT_MS"),
        ("reconciliation_interval_s", "RECONCILIATION_INTERVAL_S"),
    ]:
        if _k in exe:
            _setdefault(_env, exe[_k])

    pg = cfg.get("phase_gates", {})
    if "phase" in pg:
        _setdefault("CAPITAL_TIER", pg["phase"])
    if "alpha_capital_per_exchange" in pg:
        _setdefault("CAPITAL_INITIAL_CAPITAL", pg["alpha_capital_per_exchange"])

    risk = cfg.get("risk", {})
    if "max_rollback_threshold" in risk:
        _setdefault("RISK_MAX_ROLLBACK_THRESHOLD", risk["max_rollback_threshold"])

    if "min_volume_usd" in cfg:
        _setdefault("SIGNAL_MIN_VOLUME_USD", cfg["min_volume_usd"])

    if "disabled_strategies" in cfg:
        _setdefault("PAPER_DISABLED_STRATEGIES", ",".join(cfg["disabled_strategies"]))
    if "max_single_loss_usd" in cfg:
        _setdefault("PAPER_MAX_LOSS_PER_TRADE_USD", cfg["max_single_loss_usd"])


async def init_config(engine: "Engine") -> None:
    from src.core.config import load_engine_config as _lec_cfg
    _ecfg = _lec_cfg()
    if _ecfg:
        apply_trading_json_defaults(_ecfg)

    raw_symbols = os.environ.get("TRADING_SYMBOLS", "").strip()
    if raw_symbols.lower() == "auto":
        os.environ["TRADING_SYMBOLS"] = '["auto"]'

    try:
        engine._settings = get_settings()
        engine.context.environment = engine._settings.engine_env
        engine.context.execution_mode = engine._settings.execution_mode
        from src.core.config import load_engine_config as _lec
        _actual_mode = _lec().get("mode", "paper")
        logger.info(
            "Config loaded — env=%s engine_mode=%s (EXECUTION_MODE env=%s) capital_tier=%s",
            engine._settings.engine_env,
            _actual_mode,
            engine._settings.execution_mode,
            engine._settings.capital.tier,
        )
    except Exception as exc:
        logger.warning("Config load failed (using defaults): %s", exc)
        engine._settings = Settings()
        engine.context.environment = "dev"
        engine.context.execution_mode = "paper"

    validate_config(engine)
    await resolve_symbols(engine)


def validate_config(engine: "Engine") -> None:
    """Validate config consistency at startup. WARNING for non-fatal, SystemExit for fatal."""
    from src.core.config import load_engine_config

    ecfg = load_engine_config()
    if not ecfg:
        return

    engine_mode = ecfg.get("mode", "paper")
    env_engine_mode = os.environ.get("ENGINE_MODE", "")
    env_execution_mode = os.environ.get("EXECUTION_MODE", "")
    if env_engine_mode and env_engine_mode != engine_mode:
        logger.warning(
            "CONFIG CONFLICT: ENGINE_MODE env='%s' differs from engine.json mode='%s' — engine.json wins",
            env_engine_mode, engine_mode,
        )
    if env_execution_mode and env_execution_mode != engine_mode:
        logger.warning(
            "CONFIG CONFLICT: EXECUTION_MODE env='%s' differs from engine.json mode='%s' — engine.json wins",
            env_execution_mode, engine_mode,
        )

    active_exchanges = ecfg.get("exchanges", {}).get("active", [])
    if not active_exchanges:
        logger.critical("FATAL: exchanges.active is empty in engine.json — cannot start without exchanges")
        raise SystemExit(1)

    risk = ecfg.get("risk", {})
    for key, label in [("max_position_pct", "max_position_pct"), ("max_daily_loss_pct", "max_daily_loss_pct")]:
        val = risk.get(key)
        if val is not None and not (0 <= float(val) <= 100):
            logger.warning(
                "CONFIG: risk.%s=%s is outside valid range [0, 100]", label, val,
            )

    for section in ("strategy_filters", "execution", "risk"):
        if section not in ecfg:
            logger.warning("CONFIG: required section '%s' missing from engine.json", section)


async def resolve_symbols(engine: "Engine") -> None:
    """Resolve 'auto' symbols to actual trading pairs via exchange API discovery."""
    if not engine._settings or engine._settings.trading.symbols != ["auto"]:
        return

    from src.collectors.symbol_discovery import discover_common_symbols
    from src.core.config import load_engine_config

    min_ex = engine._settings.trading.symbol_min_exchanges
    _ecfg = load_engine_config()
    _exchange_exclusions: dict[str, list[str]] = (
        _ecfg.get("exchanges", {}).get("symbol_exclusions_per_exchange", {})
    )
    try:
        symbols = await discover_common_symbols(
            min_exchanges=min_ex,
            exchange_exclusions=_exchange_exclusions or None,
        )
        if symbols:
            engine._settings.trading.symbols = symbols
            logger.info(
                "Auto-discovered %d trading symbols (min_exchanges=%d)",
                len(symbols), min_ex,
            )
        else:
            engine._settings.trading.symbols = ["BTC/USDT", "ETH/USDT", "XRP/USDT"]
            logger.warning("Symbol auto-discovery returned empty — using fallback 3 symbols")
    except Exception as exc:
        engine._settings.trading.symbols = ["BTC/USDT", "ETH/USDT", "XRP/USDT"]
        logger.warning("Symbol auto-discovery failed (using fallback): %s", exc)

    _op = getattr(engine._settings, "operational", None)
    cross_pairs_env = getattr(_op, "triangular_cross_pairs", None) if _op else None
    if cross_pairs_env and engine._settings:
        cross_pairs = [p.strip() for p in cross_pairs_env.split(",") if p.strip()]
        existing = set(engine._settings.trading.symbols)
        added = []
        for cp in cross_pairs:
            if cp not in existing:
                engine._settings.trading.symbols.append(cp)
                existing.add(cp)
                added.append(cp)
        if added:
            logger.info("US-241: Added %d triangular cross-pairs: %s", len(added), added)


async def init_infrastructure(engine: "Engine") -> None:
    from src.core.config import EngineMode, resolve_engine_mode, load_engine_config

    _engine_cfg = load_engine_config()
    engine._engine_mode = resolve_engine_mode(
        engine_mode=_engine_cfg.get("mode"),
    )

    import httpx
    engine._http_client = httpx.AsyncClient(timeout=10.0)

    if engine._engine_mode == EngineMode.LIVE:
        try:
            from urllib.parse import urlparse
            from src.infra.redis.client import RedisClient, RedisConfig
            from src.infra.redis.event_bus import EventBus
            _parsed = urlparse(engine._settings.redis.url)
            redis_config = RedisConfig(
                host=_parsed.hostname or "localhost",
                port=_parsed.port or 6379,
                db=int((_parsed.path or "/0").lstrip("/") or "0"),
                password=_parsed.password,
                max_connections=engine._settings.redis.max_connections,
            )
            redis_client = RedisClient(redis_config)
            await redis_client.connect()
            engine._redis_client = redis_client
            engine._event_bus = EventBus(redis_client)
            logger.info("Redis EventBus initialized (live mode)")
            try:
                await redis_client.delete("leviathan:trade_requests")
                logger.info("startup_flush: leviathan:trade_requests deleted")
            except Exception as _flush_exc:
                logger.warning("startup_flush_failed: %s", _flush_exc)
        except Exception as exc:
            logger.warning("Redis init failed, falling back to InMemoryEventBus: %s", exc)
            from src.infra.redis.memory_bus import InMemoryEventBus
            engine._event_bus = InMemoryEventBus()
    else:
        from src.infra.redis.memory_bus import InMemoryEventBus
        engine._event_bus = InMemoryEventBus()
        logger.info("InMemoryEventBus initialized (engine_mode=%s)", engine._engine_mode)

    # Phase 4-2: engine method 경유 호출 (test mock 호환). engine method는 thin wrapper.
    await engine._init_database()
    engine._init_telegram()
    engine._init_rust_bridge()


async def init_database(engine: "Engine") -> None:
    """Initialize TimescaleDB connection pool, run schema migration, start MarketRecorder."""
    dsn = get_settings().operational.database_url
    if not dsn:
        logger.warning("DATABASE_URL not set — using default dev credentials")
        dsn = "postgresql://leviathan:leviathan@localhost:5432/leviathan"
    asyncpg_dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")

    try:
        from src.infra.db.connection import DatabasePool
        engine._db_pool = DatabasePool(dsn=asyncpg_dsn, min_size=2, max_size=10)
        await engine._db_pool.initialize()
        logger.info("TimescaleDB connection pool initialized")

        try:
            from src.infra.db.migration_runner import run_migrations
            await run_migrations(engine._db_pool.pool)
            logger.info("TimescaleDB schema migration applied")
        except Exception as exc:
            logger.warning("Schema migration failed (non-fatal): %s", exc)

        try:
            from src.modes.preflight import PreflightChecker
            PreflightChecker()._check_env_sync()
        except Exception:
            pass

        try:
            from src.infra.db.market_recorder import MarketRecorder
            engine._market_recorder = MarketRecorder(pool=engine._db_pool.pool)
            await engine._market_recorder.start()
            logger.info("MarketRecorder started (flush=%dms, buffer=%d)",
                        MarketRecorder.FLUSH_INTERVAL_MS, MarketRecorder.MAX_BUFFER_SIZE)
        except Exception as exc:
            logger.warning("MarketRecorder init failed (non-fatal): %s", exc)

        try:
            from src.analysis.attribution import PerformanceAttribution
            engine._attribution = PerformanceAttribution()
            await engine._attribution.load_from_db(engine._db_pool.pool)
        except Exception as exc:
            logger.warning("PerformanceAttribution init failed (non-fatal): %s", exc)

        _op = get_settings().operational
        if _op.capital_allocator_enabled:
            try:
                from src.core.capital_allocator import CapitalAllocator
                _max_pos = _op.max_position_usd
                engine._capital_allocator = CapitalAllocator(total_capital=_max_pos * 10)
                logger.info("CapitalAllocator initialized: total_capital=%.0f", _max_pos * 10)
            except Exception as exc:
                logger.warning("CapitalAllocator init failed (non-fatal): %s", exc)

        if _op.portfolio_risk_enabled:
            try:
                from src.core.portfolio_risk import PortfolioRiskManager
                engine._portfolio_risk = PortfolioRiskManager()
                logger.info("PortfolioRiskManager initialized")
            except Exception as exc:
                logger.warning("PortfolioRiskManager init failed (non-fatal): %s", exc)
    except Exception as exc:
        from src.core.config import load_engine_config as _lec_db
        _mode = _lec_db().get("mode", "paper")
        if _mode == "live":
            logger.critical(
                "TimescaleDB init FAILED in LIVE mode — aborting. "
                "Fix DB before running live. Error: %s", exc,
            )
            raise SystemExit(
                f"FATAL: Cannot run live mode without DB. Fix TimescaleDB. Error: {exc}"
            ) from exc
        logger.warning("TimescaleDB init failed (non-fatal in paper mode): %s", exc)


def init_telegram(engine: "Engine") -> None:
    """Initialize 3-Bot Telegram system (Trade/Infra/Dev) from environment variables."""
    try:
        from src.infra.telegram_trade_bot import TradeTelegramBot
        engine._trade_bot = TradeTelegramBot(engine_context=engine)
        engine._telegram = engine._trade_bot
        if engine._trade_bot.enabled:
            logger.info("TradeTelegramBot enabled")
        else:
            logger.info("TradeTelegramBot disabled")
    except Exception as exc:
        logger.warning("TradeTelegramBot init failed (non-fatal): %s", exc)

    logger.info("InfraBot/DevBot → bot-gateway (독립 프로세스)")


def init_rust_bridge(engine: "Engine") -> None:
    """Log Rust PyO3 feature flag status."""
    try:
        from src.core.rust_bridge import get_feature_flags
        flags = get_feature_flags()
        logger.info("Rust bridge flags: %s", flags)
    except Exception as exc:
        logger.warning("Rust bridge init failed (non-fatal): %s", exc)


async def init_tuner(engine: "Engine") -> None:
    """Initialize ScheduledTuner if ENABLE_INLINE_TUNER is set (US-146)."""
    try:
        from src.tuning.scheduled_tuner import ScheduledTuner
    except ImportError:
        logger.info("ScheduledTuner not available (optuna/apscheduler not installed)")
        return
    if get_settings().operational.enable_inline_tuner.lower() not in ("true", "1", "yes"):
        logger.info("Inline tuner disabled (ENABLE_INLINE_TUNER not set)")
        return
    try:
        engine._scheduled_tuner = ScheduledTuner()

        def _tuner_reload_callback() -> None:
            try:
                import json
                import pathlib
                params_path = pathlib.Path(__file__).parent.parent.parent / "config" / "strategy_params.json"
                if params_path.exists() and engine._signal_generator is not None:
                    params = json.loads(params_path.read_text())
                    ce = params.get("cross_exchange", {})
                    if ce.get("status") in ("READY", "MONITOR") and "min_spread_bps" in ce:
                        new_edge = Decimal(str(ce["min_spread_bps"])) / Decimal("10000")
                        if hasattr(engine._signal_generator, "_config"):
                            engine._signal_generator._config.min_edge = new_edge
                            logger.info(
                                "ScheduledTuner hot-reload: min_edge updated to %.2f bps",
                                float(ce["min_spread_bps"]),
                            )
                    if ce.get("slippage_buffer_bps") is not None:
                        if hasattr(engine._signal_generator, "_config"):
                            engine._signal_generator._config.slippage_buffer_bps = Decimal(
                                str(ce["slippage_buffer_bps"])
                            )
            except Exception as exc:
                logger.warning("ScheduledTuner hot-reload failed: %s", exc)

        engine._scheduled_tuner._reload_callback = _tuner_reload_callback
        engine._scheduled_tuner.start_scheduler()
        logger.info("Scheduled tuner started (with hot-reload callback)")
    except Exception as exc:
        logger.warning("Failed to start scheduled tuner: %s", exc)
