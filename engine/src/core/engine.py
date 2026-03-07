"""LEVIATHAN Engine — main async loop with Protocol-based dependency injection.

Wires together:
  SignalProcessor → RiskChecker → Executor → StrategyManager → KillSwitch

All subsystem dependencies are expressed as Protocols so the engine
can be tested with mocks and swapped without touching the orchestrator.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Optional, Protocol, runtime_checkable

from src.risk.kill_switch import halt_local, is_halted

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Engine status
# ---------------------------------------------------------------------------

class EngineStatus(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    HALTED = "halted"
    STOPPING = "stopping"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class EngineConfig:
    reconcile_interval: int = int(os.getenv("ENGINE_RECONCILE_INTERVAL", "60"))
    health_check_interval: int = int(os.getenv("ENGINE_HEALTH_CHECK_INTERVAL", "10"))
    heartbeat_interval: int = int(os.getenv("ENGINE_HEARTBEAT_INTERVAL", "5"))
    shutdown_timeout: int = int(os.getenv("ENGINE_SHUTDOWN_TIMEOUT", "10"))


# ---------------------------------------------------------------------------
# Protocol interfaces (dependency inversion)
# ---------------------------------------------------------------------------

@runtime_checkable
class ISignalProcessor(Protocol):
    async def process(self, *args: Any, **kwargs: Any) -> Any: ...


@runtime_checkable
class IRiskChecker(Protocol):
    async def check(self, *args: Any, **kwargs: Any) -> Any: ...


@runtime_checkable
class IExecutor(Protocol):
    async def execute(self, *args: Any, **kwargs: Any) -> Any: ...


@runtime_checkable
class IStrategyManager(Protocol):
    def list_strategies(self) -> list[str]: ...
    def get_strategy(self, strategy_id: str) -> Any: ...
    async def start_strategy(self, strategy_id: str) -> None: ...
    async def stop_strategy(self, strategy_id: str) -> None: ...


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class LEVIATHANEngine:
    """
    Main engine orchestrator.

    Lifecycle:
      start() → background loops (health, reconcile, heartbeat)
               → await shutdown
               → stop() gracefully cancels all tasks

    Kill switch:
      trigger_kill_switch(reason) sets Tier 1 halt flag immediately (< 1ms)
      and marks status = HALTED. No new orders can be submitted after this.

    Strategy management:
      toggle_strategy(id) starts or stops a strategy based on its current state.
    """

    def __init__(
        self,
        signal_processor: ISignalProcessor | None = None,
        risk_checker: IRiskChecker | None = None,
        executor: IExecutor | None = None,
        strategy_manager: IStrategyManager | None = None,
        config: EngineConfig | None = None,
    ) -> None:
        self.signal_processor = signal_processor
        self.risk_checker = risk_checker
        self.executor = executor
        self.strategy_manager = strategy_manager
        self.config = config or EngineConfig()

        self.status: EngineStatus = EngineStatus.STOPPED
        self.kill_switch_active: bool = False
        self.kill_switch_reason: str = ""
        self._started_at: float | None = None
        self._shutdown: asyncio.Event = asyncio.Event()
        self._tasks: list[asyncio.Task[Any]] = []

    # ------------------------------------------------------------------
    # Public API (used by FastAPI routes and tests)
    # ------------------------------------------------------------------

    @property
    def uptime_seconds(self) -> float:
        if self._started_at is None:
            return 0.0
        return time.monotonic() - self._started_at

    def trigger_kill_switch(self, reason: str) -> None:
        """
        Tier 1 kill switch: set in-process halt flag immediately.

        Idempotent — first caller's reason is preserved.
        Does NOT affect ongoing tasks (Tier 2/3 handled by AtomicExecutor).
        """
        if self.kill_switch_active:
            return  # idempotent

        self.kill_switch_active = True
        self.kill_switch_reason = reason
        self.status = EngineStatus.HALTED
        halt_local()
        logger.critical("KILL SWITCH triggered — reason: %s", reason)

    def list_strategies(self) -> list[str]:
        """Return IDs of all registered strategies."""
        if self.strategy_manager is None:
            return []
        return self.strategy_manager.list_strategies()

    async def toggle_strategy(self, strategy_id: str) -> None:
        """
        Enable or disable a strategy by ID.

        Raises KeyError if strategy is not registered.
        """
        if self.strategy_manager is None:
            raise KeyError(f"No strategy manager — cannot toggle {strategy_id!r}")

        strategy = self.strategy_manager.get_strategy(strategy_id)
        if strategy is None:
            raise KeyError(f"Strategy {strategy_id!r} not registered")

        if strategy.is_active:
            await self.strategy_manager.stop_strategy(strategy_id)
            logger.info("Strategy %s stopped via toggle", strategy_id)
        else:
            await self.strategy_manager.start_strategy(strategy_id)
            logger.info("Strategy %s started via toggle", strategy_id)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start all background loops and mark engine as running."""
        if self.status == EngineStatus.RUNNING:
            return

        self.status = EngineStatus.STARTING
        self._started_at = time.monotonic()
        self._shutdown.clear()

        self._tasks = [
            asyncio.create_task(self._health_check_loop(), name="health_check"),
            asyncio.create_task(self._reconcile_loop(), name="reconcile"),
        ]

        self.status = EngineStatus.RUNNING
        logger.info("LEVIATHANEngine running")

    async def stop(self) -> None:
        """Graceful shutdown — cancel all background tasks."""
        if self.status == EngineStatus.STOPPED:
            return

        self.status = EngineStatus.STOPPING
        self._shutdown.set()

        for task in self._tasks:
            if not task.done():
                task.cancel()

        if self._tasks:
            await asyncio.wait(self._tasks, timeout=self.config.shutdown_timeout)

        self._tasks.clear()
        self.status = EngineStatus.STOPPED
        logger.info("LEVIATHANEngine stopped")

    async def run_until_shutdown(self) -> None:
        """Start engine and block until shutdown signal."""
        await self.start()
        await self._shutdown.wait()
        await self.stop()

    # ------------------------------------------------------------------
    # Background loops
    # ------------------------------------------------------------------

    async def _health_check_loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                await asyncio.sleep(self.config.health_check_interval)
                if is_halted():
                    logger.warning("Halt flag detected in health check")
                    break
                logger.debug("Health check OK — uptime=%.1fs", self.uptime_seconds)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Health check error: %s", exc)

    async def _reconcile_loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                await asyncio.sleep(self.config.reconcile_interval)
                logger.debug("Position reconciliation tick")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Reconcile error: %s", exc)
