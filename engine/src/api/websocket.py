"""WebSocket connection manager for real-time dashboard feeds."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    Manages active WebSocket connections.

    - Tracks connected clients in a set.
    - Broadcasts JSON messages to all active connections.
    - Automatically removes dead connections on send failure.
    - Sends periodic heartbeats for liveness detection.
    """

    def __init__(self) -> None:
        self.active_connections: set[Any] = set()

    async def connect(self, websocket: Any) -> None:
        """Accept and register a new WebSocket connection."""
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.info("WebSocket connected — total: %d", len(self.active_connections))

    def disconnect(self, websocket: Any) -> None:
        """Remove a WebSocket connection (idempotent)."""
        self.active_connections.discard(websocket)
        logger.info("WebSocket disconnected — total: %d", len(self.active_connections))

    @property
    def connection_count(self) -> int:
        return len(self.active_connections)

    async def broadcast(self, message: dict[str, Any]) -> None:
        """
        Broadcast a JSON message to all connected clients.
        Removes any connection that fails to receive the message.
        """
        dead: list[Any] = []
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception as exc:
                logger.warning("WebSocket send failed, removing connection: %s", exc)
                dead.append(connection)
        for conn in dead:
            self.active_connections.discard(conn)

    async def send_personal(self, websocket: Any, message: dict[str, Any]) -> None:
        """Send a message to a single WebSocket client."""
        await websocket.send_json(message)

    async def send_heartbeat(self) -> None:
        """
        Send a heartbeat ping to all connections.
        Dead connections are pruned automatically.
        """
        await self.broadcast({"type": "heartbeat", "ts": time.time()})
