"""Tests for WebSocket connection manager."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.api.websocket import ConnectionManager


class TestConnectionManager:
    def test_initial_state_empty(self):
        mgr = ConnectionManager()
        assert len(mgr.active_connections) == 0

    @pytest.mark.asyncio
    async def test_connect_adds_connection(self):
        mgr = ConnectionManager()
        ws = AsyncMock()
        ws.accept = AsyncMock()
        await mgr.connect(ws)
        assert ws in mgr.active_connections

    @pytest.mark.asyncio
    async def test_connect_calls_accept(self):
        mgr = ConnectionManager()
        ws = AsyncMock()
        ws.accept = AsyncMock()
        await mgr.connect(ws)
        ws.accept.assert_called_once()

    def test_disconnect_removes_connection(self):
        mgr = ConnectionManager()
        ws = MagicMock()
        mgr.active_connections.add(ws)
        mgr.disconnect(ws)
        assert ws not in mgr.active_connections

    def test_disconnect_nonexistent_is_noop(self):
        mgr = ConnectionManager()
        ws = MagicMock()
        mgr.disconnect(ws)  # should not raise

    @pytest.mark.asyncio
    async def test_broadcast_sends_to_all(self):
        mgr = ConnectionManager()
        ws1, ws2 = AsyncMock(), AsyncMock()
        ws1.accept = AsyncMock()
        ws2.accept = AsyncMock()
        await mgr.connect(ws1)
        await mgr.connect(ws2)
        await mgr.broadcast({"type": "test", "data": "hello"})
        ws1.send_json.assert_called_once()
        ws2.send_json.assert_called_once()

    @pytest.mark.asyncio
    async def test_broadcast_removes_failed_connections(self):
        mgr = ConnectionManager()
        ws_ok = AsyncMock()
        ws_ok.accept = AsyncMock()
        ws_fail = AsyncMock()
        ws_fail.accept = AsyncMock()
        ws_fail.send_json = AsyncMock(side_effect=Exception("disconnected"))
        await mgr.connect(ws_ok)
        await mgr.connect(ws_fail)
        await mgr.broadcast({"type": "test"})
        assert ws_fail not in mgr.active_connections
        assert ws_ok in mgr.active_connections

    @pytest.mark.asyncio
    async def test_send_personal_message(self):
        mgr = ConnectionManager()
        ws = AsyncMock()
        ws.accept = AsyncMock()
        await mgr.connect(ws)
        await mgr.send_personal(ws, {"type": "personal"})
        ws.send_json.assert_called_once_with({"type": "personal"})

    def test_connection_count(self):
        mgr = ConnectionManager()
        ws1, ws2 = MagicMock(), MagicMock()
        mgr.active_connections.add(ws1)
        mgr.active_connections.add(ws2)
        assert mgr.connection_count == 2


class TestConnectionManagerHeartbeat:
    @pytest.mark.asyncio
    async def test_heartbeat_sends_ping(self):
        mgr = ConnectionManager()
        ws = AsyncMock()
        ws.accept = AsyncMock()
        await mgr.connect(ws)
        await mgr.send_heartbeat()
        ws.send_json.assert_called_once()
        call_data = ws.send_json.call_args[0][0]
        assert call_data.get("type") == "heartbeat"

    @pytest.mark.asyncio
    async def test_heartbeat_removes_dead_connections(self):
        mgr = ConnectionManager()
        ws_dead = AsyncMock()
        ws_dead.accept = AsyncMock()
        ws_dead.send_json = AsyncMock(side_effect=Exception("dead"))
        await mgr.connect(ws_dead)
        await mgr.send_heartbeat()
        assert ws_dead not in mgr.active_connections
