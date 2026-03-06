"""Tests for WebSocket Manager."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.infra.exchange.websocket_manager import (
    ConnectionConfig,
    ConnectionState,
    WebSocketConnection,
    WebSocketManager,
)


@pytest.fixture
def config():
    return ConnectionConfig(
        exchange_id="test_exchange",
        stale_threshold_seconds=5.0,
        heartbeat_interval=30.0,
        max_reconnect_attempts=3,
        base_reconnect_delay=0.05,  # fast for tests
        max_reconnect_delay=0.5,
    )


class TestWebSocketConnection:
    @pytest.mark.asyncio
    async def test_initial_state_is_disconnected(self, config):
        conn = WebSocketConnection(config, AsyncMock())
        assert conn.state == ConnectionState.DISCONNECTED

    @pytest.mark.asyncio
    async def test_connect_success(self, config):
        connect_fn = AsyncMock()
        conn = WebSocketConnection(config, connect_fn)
        await conn.connect()
        assert conn.state == ConnectionState.CONNECTED
        connect_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_connect_failure_leaves_disconnected(self, config):
        connect_fn = AsyncMock(side_effect=ConnectionError("refused"))
        conn = WebSocketConnection(config, connect_fn)
        with pytest.raises(ConnectionError):
            await conn.connect()
        assert conn.state == ConnectionState.DISCONNECTED

    @pytest.mark.asyncio
    async def test_disconnect_after_connect(self, config):
        conn = WebSocketConnection(config, AsyncMock())
        await conn.connect()
        await conn.disconnect()
        assert conn.state == ConnectionState.DISCONNECTED

    @pytest.mark.asyncio
    async def test_state_change_callback_called(self, config):
        states: list[ConnectionState] = []

        def on_change(exchange_id: str, state: ConnectionState) -> None:
            states.append(state)

        conn = WebSocketConnection(config, AsyncMock(), on_state_change=on_change)
        await conn.connect()
        await conn.disconnect()

        assert ConnectionState.CONNECTING in states
        assert ConnectionState.DISCONNECTED in states

    def test_record_message_updates_timestamp(self, config):
        conn = WebSocketConnection(config, AsyncMock())
        conn.record_message()
        assert conn._last_message_time > 0

    @pytest.mark.asyncio
    async def test_reconnect_attempts_reset_on_success(self, config):
        connect_fn = AsyncMock()
        conn = WebSocketConnection(config, connect_fn)
        await conn.connect()
        assert conn._reconnect_attempts == 0

    @pytest.mark.asyncio
    async def test_no_state_change_callback_is_fine(self, config):
        """Should not raise when no callback provided."""
        conn = WebSocketConnection(config, AsyncMock(), on_state_change=None)
        await conn.connect()
        assert conn.state == ConnectionState.CONNECTED


class TestWebSocketManager:
    @pytest.mark.asyncio
    async def test_add_and_retrieve_connection(self, config):
        manager = WebSocketManager()
        conn = WebSocketConnection(config, AsyncMock())
        manager.add_connection(conn)
        assert manager.get_connection("test_exchange") is conn

    @pytest.mark.asyncio
    async def test_get_nonexistent_connection_returns_none(self):
        manager = WebSocketManager()
        assert manager.get_connection("nonexistent") is None

    @pytest.mark.asyncio
    async def test_connect_all(self):
        manager = WebSocketManager()
        for exchange in ["ex_a", "ex_b", "ex_c"]:
            cfg = ConnectionConfig(exchange_id=exchange)
            manager.add_connection(WebSocketConnection(cfg, AsyncMock()))

        await manager.connect_all()

        states = manager.get_all_states()
        assert all(s == ConnectionState.CONNECTED for s in states.values())

    @pytest.mark.asyncio
    async def test_connected_count(self):
        manager = WebSocketManager()
        for exchange in ["ex_a", "ex_b"]:
            cfg = ConnectionConfig(exchange_id=exchange)
            manager.add_connection(WebSocketConnection(cfg, AsyncMock()))

        await manager.connect_all()
        assert manager.connected_count == 2

    @pytest.mark.asyncio
    async def test_connected_count_zero_initially(self):
        manager = WebSocketManager()
        cfg = ConnectionConfig(exchange_id="test")
        manager.add_connection(WebSocketConnection(cfg, AsyncMock()))
        assert manager.connected_count == 0

    @pytest.mark.asyncio
    async def test_disconnect_all(self):
        manager = WebSocketManager()
        cfg = ConnectionConfig(exchange_id="test")
        manager.add_connection(WebSocketConnection(cfg, AsyncMock()))

        await manager.connect_all()
        assert manager.connected_count == 1

        await manager.disconnect_all()
        assert manager.connected_count == 0

    @pytest.mark.asyncio
    async def test_get_all_states(self):
        manager = WebSocketManager()
        for exchange in ["ex_a", "ex_b"]:
            cfg = ConnectionConfig(exchange_id=exchange)
            manager.add_connection(WebSocketConnection(cfg, AsyncMock()))

        await manager.connect_all()
        states = manager.get_all_states()
        assert set(states.keys()) == {"ex_a", "ex_b"}
        assert all(isinstance(s, ConnectionState) for s in states.values())

    @pytest.mark.asyncio
    async def test_connect_all_with_one_failure(self):
        """connect_all should not raise even if one connection fails."""
        manager = WebSocketManager()
        good_cfg = ConnectionConfig(exchange_id="good")
        bad_cfg = ConnectionConfig(exchange_id="bad")
        manager.add_connection(WebSocketConnection(good_cfg, AsyncMock()))
        manager.add_connection(
            WebSocketConnection(bad_cfg, AsyncMock(side_effect=ConnectionError("refused")))
        )

        # Should not raise; failures are collected via return_exceptions=True
        await manager.connect_all()

        states = manager.get_all_states()
        assert states["good"] == ConnectionState.CONNECTED
        assert states["bad"] == ConnectionState.DISCONNECTED
