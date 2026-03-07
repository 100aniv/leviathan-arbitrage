"""WebSocket lifecycle manager — connection pool, heartbeat, stale detection, reconnect."""
from __future__ import annotations

import asyncio
import logging
import time as _time
from dataclasses import dataclass
from enum import StrEnum
from typing import Callable

logger = logging.getLogger(__name__)


class ConnectionState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


@dataclass
class ConnectionConfig:
    exchange_id: str
    stale_threshold_seconds: float = 5.0
    heartbeat_interval: float = 30.0
    max_reconnect_attempts: int = 10
    base_reconnect_delay: float = 1.0
    max_reconnect_delay: float = 60.0


class WebSocketConnection:
    """Manages a single WebSocket connection with heartbeat monitoring and auto-reconnect."""

    def __init__(
        self,
        config: ConnectionConfig,
        connect_fn: Callable,
        on_state_change: Callable[[str, ConnectionState], None] | None = None,
    ) -> None:
        self.config = config
        self._connect_fn = connect_fn
        self._on_state_change = on_state_change
        self._state = ConnectionState.DISCONNECTED
        self._last_message_time: float = 0.0
        self._reconnect_attempts: int = 0
        self._heartbeat_task: asyncio.Task | None = None
        self._stale_check_task: asyncio.Task | None = None

    @property
    def state(self) -> ConnectionState:
        return self._state

    def _set_state(self, state: ConnectionState) -> None:
        self._state = state
        if self._on_state_change:
            self._on_state_change(self.config.exchange_id, state)

    async def connect(self) -> None:
        """Connect and start monitoring. Raises on failure."""
        self._set_state(ConnectionState.CONNECTING)
        try:
            await self._connect_fn()
            self._state = ConnectionState.CONNECTED
            self._reconnect_attempts = 0
            self._last_message_time = _time.monotonic()
            self._start_monitoring()
        except Exception:
            self._set_state(ConnectionState.DISCONNECTED)
            raise

    async def disconnect(self) -> None:
        """Stop monitoring and mark disconnected."""
        self._stop_monitoring()
        self._set_state(ConnectionState.DISCONNECTED)

    def record_message(self) -> None:
        """Call this whenever a message is received to reset staleness timer."""
        self._last_message_time = _time.monotonic()

    def _start_monitoring(self) -> None:
        loop = asyncio.get_running_loop()
        self._heartbeat_task = loop.create_task(self._heartbeat_loop())
        self._stale_check_task = loop.create_task(self._stale_check_loop())

    def _stop_monitoring(self) -> None:
        for task in (self._heartbeat_task, self._stale_check_task):
            if task and not task.done():
                task.cancel()
        self._heartbeat_task = None
        self._stale_check_task = None

    async def _heartbeat_loop(self) -> None:
        while self._state == ConnectionState.CONNECTED:
            await asyncio.sleep(self.config.heartbeat_interval)
            logger.debug("Heartbeat: %s", self.config.exchange_id)

    async def _stale_check_loop(self) -> None:
        while self._state == ConnectionState.CONNECTED:
            await asyncio.sleep(1.0)
            if self._last_message_time > 0:
                staleness = _time.monotonic() - self._last_message_time
                if staleness > self.config.stale_threshold_seconds:
                    logger.warning(
                        "Stale data for %s: %.1fs since last message",
                        self.config.exchange_id,
                        staleness,
                    )
                    await self._trigger_reconnect()
                    return

    async def _trigger_reconnect(self) -> None:
        self._stop_monitoring()
        self._set_state(ConnectionState.RECONNECTING)

        if self._reconnect_attempts >= self.config.max_reconnect_attempts:
            logger.error("Max reconnect attempts reached for %s", self.config.exchange_id)
            self._set_state(ConnectionState.DISCONNECTED)
            return

        delay = min(
            self.config.base_reconnect_delay * (2**self._reconnect_attempts),
            self.config.max_reconnect_delay,
        )
        self._reconnect_attempts += 1
        logger.info(
            "Reconnecting %s in %.1fs (attempt %d)",
            self.config.exchange_id,
            delay,
            self._reconnect_attempts,
        )
        await asyncio.sleep(delay)
        try:
            await self.connect()
        except Exception as e:
            logger.error("Reconnect failed for %s: %s", self.config.exchange_id, e)
            self._set_state(ConnectionState.DISCONNECTED)


class WebSocketManager:
    """Manages a pool of WebSocket connections across multiple exchanges."""

    def __init__(self) -> None:
        self._connections: dict[str, WebSocketConnection] = {}

    def add_connection(self, connection: WebSocketConnection) -> None:
        self._connections[connection.config.exchange_id] = connection

    def get_connection(self, exchange_id: str) -> WebSocketConnection | None:
        return self._connections.get(exchange_id)

    async def connect_all(self) -> None:
        """Connect all managed exchanges concurrently."""
        await asyncio.gather(
            *[conn.connect() for conn in self._connections.values()],
            return_exceptions=True,
        )

    async def disconnect_all(self) -> None:
        """Disconnect all managed exchanges concurrently."""
        await asyncio.gather(
            *[conn.disconnect() for conn in self._connections.values()],
            return_exceptions=True,
        )

    def get_all_states(self) -> dict[str, ConnectionState]:
        return {eid: conn.state for eid, conn in self._connections.items()}

    @property
    def connected_count(self) -> int:
        return sum(
            1 for conn in self._connections.values()
            if conn.state == ConnectionState.CONNECTED
        )
