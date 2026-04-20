"""TradingSupervisor — standalone process lifecycle owner (Path-B Day-4).

Parallel module to ``src/main.py:Engine``. Owns the boot/stop sequence in a
decoupled, testable shape so that Day-5 migration can swap ``Engine.run()``
internals without touching FastAPI wiring, API contracts, or CLI flags.

Responsibilities
----------------
1. Initialize DB pool (asyncpg via :class:`src.infra.db.connection.DatabasePool`).
2. Initialize Redis client (:class:`src.infra.redis.client.RedisClient`).
3. Register exchange adapters from ``config.exchanges.active``.
4. Placeholder StrategyManager slot (Day-5 will inject a StrategyRegistry).
5. Build :class:`src.core.universe_matrix.UniverseMatrix`.
6. Start background tasks via :func:`asyncio.create_task` with name tags.
7. Install SIGTERM/SIGINT handlers → graceful shutdown.
8. Graceful stop with :func:`asyncio.gather` bounded by :attr:`SHUTDOWN_TIMEOUT`.

Non-goals
---------
* Does **not** import from ``src/main.py`` (circularity + parallel lane).
* Does **not** perform signal pipeline / risk / strategy wiring yet — that
  wiring still lives in ``Engine._init_*`` until Day-5.
* Does **not** own ``EngineContext`` / FastAPI state — the supervisor is a
  process-lifecycle object, not an API context.

Design notes
------------
* Config is accepted as a pydantic model (``EngineConfig`` from Day-4
  ConfigService when available) or a duck-typed namespace exposing
  ``.exchanges.active`` / ``.redis.url`` / ``.database.url`` — the tests
  pass a plain ``SimpleNamespace``.
* Every major boot step emits a structured INFO log
  (``supervisor.db_pool_ready pool_size=10``) so ops can trace the sequence.
* Any boot failure logs CRITICAL + re-raises. Silent swallow is forbidden.
"""
from __future__ import annotations

import asyncio
import logging
import signal
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Health snapshot
# ---------------------------------------------------------------------------


@dataclass
class SupervisorHealth:
    """Point-in-time supervisor state returned by :meth:`TradingSupervisor.get_health`."""

    is_ready: bool = False
    background_tasks_count: int = 0
    last_heartbeat_ts: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------


class TradingSupervisor:
    """Owns process lifecycle for the LEVIATHAN trading engine.

    Boot order is intentionally explicit:

        start() → DB pool → Redis → exchanges → strategy manager
                 → UniverseMatrix → background tasks → signal handlers
                 → ready

    Stop sequence mirrors start in reverse, bounded by
    :attr:`SHUTDOWN_TIMEOUT` (30s) to avoid hung shutdowns.

    The constructor accepts any config object exposing:

    * ``config.exchanges.active`` — list[str] of exchange ids to register.
    * ``config.database.url`` (or ``.database_url``) — optional; falls back
      to pydantic ``Settings().operational.database_url``.
    * ``config.redis.url`` — optional; falls back to ``Settings().redis.url``.

    The goal is forward-compatibility with Day-4 ``EngineConfig`` (pydantic)
    while remaining testable with ``types.SimpleNamespace`` stubs.
    """

    SHUTDOWN_TIMEOUT: float = 30.0
    _TASK_NAME_PREFIX: str = "leviathan_"

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, config: Any) -> None:
        self.config = config

        # Subsystem references — all None until start() populates them.
        self._db_pool: Any = None
        self._redis_client: Any = None
        self._exchanges: dict[str, Any] = {}
        self._strategy_manager: Any = None  # Day-5 will inject StrategyRegistry
        self._universe_matrix: Any = None

        # Lifecycle state
        self._background_tasks: list[asyncio.Task[Any]] = []
        self._shutdown_requested: asyncio.Event = asyncio.Event()
        self._is_ready: bool = False
        self._errors: list[str] = []
        self._started_at: Optional[datetime] = None
        self._stopped: bool = False

        # Installed signal handlers (so we can remove them on stop)
        self._signal_loop: Optional[asyncio.AbstractEventLoop] = None
        self._installed_signals: list[int] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Orchestrate the full boot sequence.

        Raises:
            Any exception from a boot step after logging CRITICAL. The caller
            is expected to call :meth:`stop` in a ``finally`` block.
        """
        logger.info("supervisor.start initiated")
        self._started_at = datetime.now(timezone.utc)

        try:
            await self._init_db_pool()
            await self._init_redis()
            await self._register_exchanges()
            self._init_strategy_manager()
            await self._build_universe_matrix()
            await self._start_background_tasks()
            self._install_signal_handlers()
            self._register_halt_forwarder()  # Day 15: STRANDED → halt_request()

            self._is_ready = True
            logger.info(
                "supervisor.ready exchanges=%d background_tasks=%d",
                len(self._exchanges),
                len(self._background_tasks),
            )
        except Exception as exc:
            msg = f"supervisor.boot_failed step={self._current_boot_step()} error={exc!r}"
            self._errors.append(msg)
            logger.critical(msg, exc_info=True)
            raise

    async def stop(self) -> None:
        """Graceful shutdown — cancels tasks, closes Redis/DB, removes handlers.

        Bounded by :attr:`SHUTDOWN_TIMEOUT` (30s). Idempotent — calling
        ``stop()`` twice is a no-op.
        """
        if self._stopped:
            logger.debug("supervisor.stop noop already_stopped")
            return
        self._stopped = True
        self._is_ready = False
        self._shutdown_requested.set()

        logger.info(
            "supervisor.stop initiated tasks=%d timeout=%.0fs",
            len(self._background_tasks),
            self.SHUTDOWN_TIMEOUT,
        )

        # 1. Cancel all background tasks.
        pending = [t for t in self._background_tasks if not t.done()]
        for task in pending:
            task.cancel()

        # 2. Gather with timeout — swallow CancelledError, surface real errors.
        if pending:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*pending, return_exceptions=True),
                    timeout=self.SHUTDOWN_TIMEOUT,
                )
                logger.info(
                    "supervisor.tasks_cancelled count=%d", len(pending)
                )
            except asyncio.TimeoutError:
                still_pending = [t for t in pending if not t.done()]
                logger.warning(
                    "supervisor.shutdown_timeout_exceeded still_pending=%d timeout=%.0fs",
                    len(still_pending),
                    self.SHUTDOWN_TIMEOUT,
                )
        self._background_tasks.clear()

        # 3. Close Redis.
        if self._redis_client is not None:
            try:
                await self._redis_client.disconnect()
                logger.info("supervisor.redis_closed")
            except Exception as exc:
                logger.warning("supervisor.redis_close_failed error=%r", exc)

        # 4. Close DB pool.
        if self._db_pool is not None:
            try:
                await self._db_pool.close()
                logger.info("supervisor.db_pool_closed")
            except Exception as exc:
                logger.warning("supervisor.db_pool_close_failed error=%r", exc)

        # 5. Disconnect exchanges.
        for eid, adapter in list(self._exchanges.items()):
            try:
                disconnect = getattr(adapter, "disconnect", None)
                if disconnect is not None:
                    result = disconnect()
                    if asyncio.iscoroutine(result):
                        await result
            except Exception as exc:
                logger.warning(
                    "supervisor.exchange_disconnect_failed exchange=%s error=%r",
                    eid, exc,
                )
        self._exchanges.clear()

        # 6. Remove signal handlers + Day 15 halt forwarder.
        self._remove_signal_handlers()
        self._deregister_halt_forwarder()

        # 7. Flush logs (best-effort).
        for handler in logging.getLogger().handlers:
            try:
                handler.flush()
            except Exception:
                pass

        logger.info("supervisor.stopped")

    async def register_background_task(
        self, name: str, coro: Awaitable[Any]
    ) -> asyncio.Task[Any]:
        """Wrap ``coro`` in an asyncio.Task with a ``leviathan_<name>`` tag.

        Args:
            name: Short task identifier (e.g. ``"reconcile"``).
            coro: The awaitable to schedule.

        Returns:
            The created :class:`asyncio.Task`.
        """
        task = asyncio.create_task(coro, name=f"{self._TASK_NAME_PREFIX}{name}")
        self._background_tasks.append(task)
        logger.debug(
            "supervisor.task_registered name=%s task_name=%s total=%d",
            name, task.get_name(), len(self._background_tasks),
        )
        return task

    def get_health(self) -> SupervisorHealth:
        """Return a :class:`SupervisorHealth` snapshot."""
        alive = [t for t in self._background_tasks if not t.done()]
        return SupervisorHealth(
            is_ready=self._is_ready,
            background_tasks_count=len(alive),
            last_heartbeat_ts=datetime.now(timezone.utc),
            errors=list(self._errors),
        )

    async def shutdown_request(self) -> None:
        """External callers can request graceful shutdown without holding
        a reference to the asyncio event loop."""
        logger.info("supervisor.shutdown_requested")
        self._shutdown_requested.set()
        await self.stop()

    def halt_request(self, reason: str = "unspecified") -> None:
        """Fire-and-forget halt request (Day-15) — safe to call from sync
        code paths (e.g. :class:`StrandedPositionTracker` threshold breach).

        Sets the shutdown event immediately and schedules ``stop()`` on the
        running event loop. Never awaits; never raises. If no loop is
        running, logs a warning and returns — callers that need guaranteed
        stop semantics should use :meth:`shutdown_request` instead.
        """
        logger.warning("supervisor.halt_requested reason=%s", reason)
        self._shutdown_requested.set()
        try:
            loop = asyncio.get_event_loop()
            loop.create_task(
                self.stop(), name=f"{self._TASK_NAME_PREFIX}halt_stop"
            )
        except RuntimeError:
            logger.warning(
                "supervisor.halt_no_loop reason=%s skipping_async_stop", reason
            )

    @property
    def is_ready(self) -> bool:
        return self._is_ready

    @property
    def background_tasks(self) -> list[asyncio.Task[Any]]:
        return list(self._background_tasks)

    @property
    def shutdown_event(self) -> asyncio.Event:
        """Expose the shutdown event for callers that want to ``await`` it."""
        return self._shutdown_requested

    # ------------------------------------------------------------------
    # Internal — boot steps
    # ------------------------------------------------------------------

    async def _init_db_pool(self) -> None:
        """Initialize asyncpg pool via :class:`DatabasePool`.

        DSN priority:
            config.database.url → config.database_url → Settings().operational.database_url
        """
        dsn = self._resolve_database_url()
        if not dsn:
            raise RuntimeError(
                "supervisor.db_init_failed reason=no_dsn "
                "(config.database.url or operational.database_url required)"
            )

        # Normalize SQLAlchemy-style DSN to asyncpg-native.
        asyncpg_dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")

        from src.infra.db.connection import DatabasePool  # noqa: PLC0415

        pool_cls = self._db_pool_cls()  # injectable for tests
        self._db_pool = pool_cls(dsn=asyncpg_dsn, min_size=2, max_size=10)
        await self._db_pool.initialize()
        logger.info("supervisor.db_pool_ready pool_size=10")

    async def _init_redis(self) -> None:
        """Initialize :class:`RedisClient` using config.redis.url."""
        url = self._resolve_redis_url()
        if not url:
            raise RuntimeError(
                "supervisor.redis_init_failed reason=no_url "
                "(config.redis.url or settings.redis.url required)"
            )

        from urllib.parse import urlparse  # noqa: PLC0415

        from src.infra.redis.client import RedisClient, RedisConfig  # noqa: PLC0415

        parsed = urlparse(url)
        redis_cfg = RedisConfig(
            host=parsed.hostname or "localhost",
            port=parsed.port or 6379,
            db=int((parsed.path or "/0").lstrip("/") or "0"),
            password=parsed.password,
            max_connections=self._resolve_redis_max_connections(),
        )

        client_cls = self._redis_client_cls()
        self._redis_client = client_cls(redis_cfg)
        await self._redis_client.connect()
        logger.info(
            "supervisor.redis_ready host=%s port=%d db=%d",
            redis_cfg.host, redis_cfg.port, redis_cfg.db,
        )

    async def _register_exchanges(self) -> None:
        """Instantiate native adapters for ``config.exchanges.active``."""
        active = self._resolve_active_exchanges()
        if not active:
            raise RuntimeError(
                "supervisor.exchanges_empty reason=no_active_exchanges "
                "(config.exchanges.active must be non-empty)"
            )

        from src.infra.exchange import create_native_adapter  # noqa: PLC0415

        factory = self._exchange_factory() or create_native_adapter
        creds = self._resolve_exchange_credentials()

        for eid in active:
            base_eid = eid.removesuffix("_futures") if eid.endswith("_futures") else eid
            api_key, api_secret, passphrase = creds.get(
                base_eid, ("", "", "")
            )
            try:
                adapter = factory(
                    exchange_id=eid,
                    api_key=api_key,
                    api_secret=api_secret,
                    passphrase=passphrase,
                    sandbox=False,
                )
                connect = getattr(adapter, "connect", None)
                if connect is not None:
                    result = connect()
                    if asyncio.iscoroutine(result):
                        await result
                self._exchanges[eid] = adapter
                logger.info("supervisor.exchange_registered id=%s", eid)
            except Exception as exc:
                logger.critical(
                    "supervisor.exchange_register_failed id=%s error=%r",
                    eid, exc,
                )
                raise

        logger.info(
            "supervisor.exchanges_ready count=%d ids=%s",
            len(self._exchanges), sorted(self._exchanges.keys()),
        )

    def _init_strategy_manager(self) -> None:
        """Day-5 placeholder — StrategyRegistry will be injected later.

        This step is intentionally a no-op so the boot order and logging
        remain stable before Day-5 migration.
        """
        logger.info(
            "supervisor.strategy_manager_placeholder note=day5_migration_pending"
        )

    async def _build_universe_matrix(self) -> None:
        """Construct :class:`UniverseMatrix` and build against registered
        exchanges.  The matrix stays empty if no strategies are injected yet
        (Day-5 will populate ``strategy_registry``)."""
        from src.core.universe_matrix import UniverseMatrix  # noqa: PLC0415

        matrix_cls = self._universe_matrix_cls() or UniverseMatrix
        self._universe_matrix = matrix_cls()

        # Day-5: strategy_registry will be populated by StrategyRegistry.
        # For now, build with an empty iterable so the matrix bookkeeping
        # (built=True) is consistent.
        await self._universe_matrix.build(
            exchange_registry=dict(self._exchanges),
            strategy_registry=(),
        )
        logger.info("supervisor.universe_matrix_ready")

    async def _start_background_tasks(self) -> None:
        """Register the lifecycle-owned background tasks.

        The concrete task set is injected via :meth:`_background_task_factories`
        for testability. Subclasses / Day-5 migration can override.
        """
        factories = self._background_task_factories()
        for name, factory in factories:
            coro = factory()
            await self.register_background_task(name, coro)
        logger.info(
            "supervisor.background_tasks_started count=%d names=%s",
            len(self._background_tasks),
            [t.get_name() for t in self._background_tasks],
        )

    def _background_task_factories(
        self,
    ) -> list[tuple[str, Callable[[], Awaitable[Any]]]]:
        """Return (name, coroutine_factory) pairs. Default is empty —
        tests and Day-5 migration override this."""
        return []

    # ------------------------------------------------------------------
    # Internal — signal handling
    # ------------------------------------------------------------------

    def _register_halt_forwarder(self) -> None:
        """Day 15: install stranded-threshold → halt_request() forwarder."""
        try:
            from src.execution.stranded import register_halt_forwarder  # noqa: PLC0415
            register_halt_forwarder(self.halt_request)
        except Exception as exc:
            logger.warning("supervisor.halt_forwarder_register_failed err=%r", exc)

    def _deregister_halt_forwarder(self) -> None:
        try:
            from src.execution.stranded import register_halt_forwarder  # noqa: PLC0415
            register_halt_forwarder(None)
        except Exception:
            pass

    def _install_signal_handlers(self) -> None:
        """Install SIGTERM + SIGINT → stop(). Non-fatal if loop doesn't
        support signal handlers (e.g. Windows / sub-event-loops in tests)."""
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            logger.debug("supervisor.signal_handlers_skipped reason=no_loop")
            return

        self._signal_loop = loop
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._on_signal)
                self._installed_signals.append(sig)
            except (NotImplementedError, RuntimeError) as exc:
                logger.debug(
                    "supervisor.signal_handler_skipped sig=%s reason=%s",
                    sig.name, exc,
                )

    def _remove_signal_handlers(self) -> None:
        if self._signal_loop is None:
            return
        for sig in self._installed_signals:
            try:
                self._signal_loop.remove_signal_handler(sig)
            except (NotImplementedError, RuntimeError, ValueError):
                pass
        self._installed_signals.clear()
        self._signal_loop = None

    def _on_signal(self) -> None:
        """Signal handler — schedule :meth:`stop` and mark shutdown requested."""
        logger.warning("supervisor.signal_received triggering_stop=true")
        self._shutdown_requested.set()
        try:
            loop = asyncio.get_event_loop()
            loop.create_task(self.stop(), name=f"{self._TASK_NAME_PREFIX}signal_stop")
        except RuntimeError:
            # No running loop — best effort synchronous stop attempt.
            logger.warning("supervisor.signal_no_loop skipping_async_stop")

    # ------------------------------------------------------------------
    # Internal — config resolution (duck-typed)
    # ------------------------------------------------------------------

    def _resolve_database_url(self) -> Optional[str]:
        db = getattr(self.config, "database", None)
        if db is not None:
            url = getattr(db, "url", None)
            if url:
                return str(url)
        direct = getattr(self.config, "database_url", None)
        if direct:
            return str(direct)
        try:
            from src.core.config import get_settings  # noqa: PLC0415
            s = get_settings()
            return getattr(getattr(s, "operational", None), "database_url", None)
        except Exception:
            return None

    def _resolve_redis_url(self) -> Optional[str]:
        redis_cfg = getattr(self.config, "redis", None)
        if redis_cfg is not None:
            url = getattr(redis_cfg, "url", None)
            if url:
                return str(url)
        try:
            from src.core.config import get_settings  # noqa: PLC0415
            s = get_settings()
            return getattr(getattr(s, "redis", None), "url", None)
        except Exception:
            return None

    def _resolve_redis_max_connections(self) -> int:
        redis_cfg = getattr(self.config, "redis", None)
        if redis_cfg is not None:
            mc = getattr(redis_cfg, "max_connections", None)
            if mc:
                return int(mc)
        try:
            from src.core.config import get_settings  # noqa: PLC0415
            s = get_settings()
            return int(getattr(getattr(s, "redis", None), "max_connections", 100))
        except Exception:
            return 100

    def _resolve_active_exchanges(self) -> list[str]:
        ex = getattr(self.config, "exchanges", None)
        if ex is not None:
            active = getattr(ex, "active", None)
            if active is not None:
                return list(active)
        try:
            from src.core.config import load_engine_config  # noqa: PLC0415
            cfg = load_engine_config() or {}
            return list(cfg.get("exchanges", {}).get("active", []))
        except Exception:
            return []

    def _resolve_exchange_credentials(self) -> dict[str, tuple[str, str, str]]:
        """Return ``{base_exchange_id: (api_key, api_secret, passphrase)}``.

        Looks up Settings.exchange.<eid>_api_key etc. Returns empty creds if
        config is absent (paper mode).
        """
        creds: dict[str, tuple[str, str, str]] = {}
        field_map = {
            "upbit": ("upbit_access_key", "upbit_secret_key"),
            "coinone": ("coinone_access_token", "coinone_api_secret"),
        }
        ex_cfg: Any = None
        try:
            from src.core.config import get_settings  # noqa: PLC0415
            ex_cfg = getattr(get_settings(), "exchange", None)
        except Exception:
            ex_cfg = None

        if ex_cfg is None:
            return creds

        active = self._resolve_active_exchanges()
        for eid in active:
            base = eid.removesuffix("_futures") if eid.endswith("_futures") else eid
            key_f, secret_f = field_map.get(
                base, (f"{base}_api_key", f"{base}_api_secret")
            )
            api_key = getattr(ex_cfg, key_f, "") or ""
            api_secret = getattr(ex_cfg, secret_f, "") or ""
            passphrase = getattr(ex_cfg, f"{base}_passphrase", "") or ""
            creds[base] = (api_key, api_secret, passphrase)
        return creds

    # ------------------------------------------------------------------
    # Internal — injection seams (test overrides)
    # ------------------------------------------------------------------

    def _db_pool_cls(self) -> Any:
        """Injection seam for tests to substitute a mock DatabasePool."""
        from src.infra.db.connection import DatabasePool  # noqa: PLC0415
        return DatabasePool

    def _redis_client_cls(self) -> Any:
        from src.infra.redis.client import RedisClient  # noqa: PLC0415
        return RedisClient

    def _exchange_factory(self) -> Optional[Callable[..., Any]]:
        """Return a callable matching ``create_native_adapter`` signature,
        or None to use the default factory."""
        return None

    def _universe_matrix_cls(self) -> Optional[type]:
        return None

    # ------------------------------------------------------------------
    # Internal — diagnostics
    # ------------------------------------------------------------------

    def _current_boot_step(self) -> str:
        if self._db_pool is None:
            return "db_pool"
        if self._redis_client is None:
            return "redis"
        if not self._exchanges:
            return "exchanges"
        if self._universe_matrix is None:
            return "universe_matrix"
        if not self._background_tasks:
            return "background_tasks"
        return "signal_handlers"


__all__ = ["SupervisorHealth", "TradingSupervisor"]
