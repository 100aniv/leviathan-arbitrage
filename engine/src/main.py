"""LEVIATHAN Engine Entry Point.

Engine lifecycle (ralplan Step 1.7):
  1. Load configuration (Settings from env vars)
  2. Initialize infrastructure (Redis, DB, Metrics, Logger)
  3. Initialize exchange adapters (connect, subscribe)
  4. Initialize strategies (load from config)
  5. Initialize risk guardian and circuit breaker
  6. Start API server (REST + WebSocket)
  7. Start signal processing loop
  8. Start position reconciliation loop (every 60s)
  9. Start health check loop (every 10s)
 10. Await shutdown signal (SIGTERM, SIGINT, Kill Switch)
 11. Graceful shutdown: stop strategies → cancel orders → close connections → flush logs
"""
from __future__ import annotations

import asyncio
import logging
import signal
from dataclasses import dataclass, field
from typing import Any

import uvicorn

from src.api.server import EngineContext, create_app

logger = logging.getLogger(__name__)


@dataclass
class EngineState:
    """Internal engine lifecycle state."""
    running: bool = False
    kill_switch_active: bool = False
    background_tasks: list[Any] = field(default_factory=list)


class Engine:
    """
    LEVIATHAN engine orchestrator.

    Manages startup, background loops, and graceful shutdown.
    All subsystem references are stored on the shared EngineContext
    so the API can expose live data.
    """

    RECONCILE_INTERVAL = 60   # seconds
    HEALTH_CHECK_INTERVAL = 10  # seconds
    HEARTBEAT_INTERVAL = 5    # seconds
    SHUTDOWN_TIMEOUT = 10     # seconds

    def __init__(self, context: EngineContext | None = None) -> None:
        self.context = context or EngineContext()
        self.state = EngineState()
        self._shutdown_event = asyncio.Event()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """
        Full engine startup sequence.
        Blocks until shutdown signal received.
        """
        logger.info("LEVIATHAN engine starting…")
        self._setup_signal_handlers()

        try:
            await self._init_config()
            await self._start_background_tasks()
            self.state.running = True
            self.context.running = True

            logger.info("Engine running — waiting for shutdown signal")
            await self._shutdown_event.wait()
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Graceful shutdown — cancel background tasks within SHUTDOWN_TIMEOUT."""
        if not self.state.running and not self.state.background_tasks:
            return

        logger.info("Engine shutting down…")
        self.state.running = False
        self.context.running = False
        self._shutdown_event.set()

        # Cancel all background tasks
        for task in self.state.background_tasks:
            if not task.done():
                task.cancel()

        if self.state.background_tasks:
            await asyncio.wait(
                self.state.background_tasks,
                timeout=self.SHUTDOWN_TIMEOUT,
            )

        self.state.background_tasks.clear()
        logger.info("Engine shutdown complete")

    # ------------------------------------------------------------------
    # Signal handlers
    # ------------------------------------------------------------------

    def _setup_signal_handlers(self) -> None:
        """Register SIGTERM/SIGINT handlers for graceful shutdown."""
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._handle_signal)
            except (NotImplementedError, RuntimeError):
                # Windows or non-main-thread — skip
                pass

    def _handle_signal(self) -> None:
        logger.warning("Shutdown signal received")
        self._shutdown_event.set()

    # ------------------------------------------------------------------
    # Initialization steps
    # ------------------------------------------------------------------

    async def _init_config(self) -> None:
        """Load settings and populate context environment."""
        try:
            from src.core.config import get_settings
            settings = get_settings()
            self.context.environment = settings.engine_env
            logger.info("Config loaded — env=%s", settings.engine_env)
        except Exception as exc:
            logger.warning("Config load failed (using defaults): %s", exc)
            self.context.environment = "dev"

    # ------------------------------------------------------------------
    # Background task loops
    # ------------------------------------------------------------------

    async def _start_background_tasks(self) -> None:
        """Launch all background loops as asyncio tasks."""
        tasks = [
            asyncio.create_task(self._health_check_loop(), name="health_check"),
            asyncio.create_task(self._reconcile_loop(), name="reconcile"),
            asyncio.create_task(self._heartbeat_loop(), name="ws_heartbeat"),
        ]
        self.state.background_tasks.extend(tasks)

    async def _health_check_loop(self) -> None:
        """Periodic health check — runs every HEALTH_CHECK_INTERVAL seconds."""
        while self.state.running:
            try:
                await self._run_health_check()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Health check error: %s", exc)
            await asyncio.sleep(self.HEALTH_CHECK_INTERVAL)

    async def _run_health_check(self) -> None:
        """Single health check cycle (overridable in tests)."""
        logger.debug("Health check OK")

    async def _reconcile_loop(self) -> None:
        """Position reconciliation — runs every RECONCILE_INTERVAL seconds."""
        while self.state.running:
            try:
                await asyncio.sleep(self.RECONCILE_INTERVAL)
                logger.debug("Position reconciliation tick")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Reconcile error: %s", exc)

    async def _heartbeat_loop(self) -> None:
        """WebSocket heartbeat — broadcasts ping to all connected clients."""
        while self.state.running:
            try:
                await asyncio.sleep(self.HEARTBEAT_INTERVAL)
                if self.context.ws_manager:
                    await self.context.ws_manager.send_heartbeat()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Heartbeat error: %s", exc)


def build_app() -> Any:
    """Build FastAPI app for use with uvicorn (called by ASGI server)."""
    context = EngineContext()
    return create_app(context)


async def main() -> None:
    """Async entry point for direct execution."""
    context = EngineContext()
    app = create_app(context)
    engine = Engine(context=context)

    server_config = uvicorn.Config(
        app=app,
        host="0.0.0.0",
        port=8080,
        log_level="info",
    )
    server = uvicorn.Server(server_config)

    await asyncio.gather(
        engine.run(),
        server.serve(),
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
