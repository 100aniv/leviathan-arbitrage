"""Coverage tests for WebSocket lifecycle manager — targeting uncovered lines 95, 100-136."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.infra.exchange.websocket_manager import (
    ConnectionConfig,
    ConnectionState,
    WebSocketConnection,
    WebSocketManager,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def config():
    return ConnectionConfig(
        exchange_id="binance",
        stale_threshold_seconds=5.0,
        heartbeat_interval=30.0,
        max_reconnect_attempts=3,
        base_reconnect_delay=0.01,
        max_reconnect_delay=0.1,
    )


@pytest.fixture
def state_cb():
    return MagicMock()


@pytest.fixture
def conn(config, state_cb):
    return WebSocketConnection(config, AsyncMock(), state_cb)


# ── _heartbeat_loop (line 95) ─────────────────────────────────────────────────

class TestHeartbeatLoop:
    async def test_heartbeat_loop_logs_debug_and_exits_on_state_change(self, conn):
        """_heartbeat_loop logs heartbeat and exits when state leaves CONNECTED."""
        conn._state = ConnectionState.CONNECTED
        conn.config.heartbeat_interval = 0.01

        with patch("src.infra.exchange.websocket_manager.logger") as mock_log:
            task = asyncio.create_task(conn._heartbeat_loop())
            await asyncio.sleep(0.03)
            conn._state = ConnectionState.DISCONNECTED  # cause loop to exit
            await asyncio.sleep(0.02)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        mock_log.debug.assert_called()

    async def test_heartbeat_loop_exits_immediately_when_not_connected(self, conn):
        """_heartbeat_loop exits without sleeping when state != CONNECTED."""
        conn._state = ConnectionState.DISCONNECTED
        await asyncio.wait_for(conn._heartbeat_loop(), timeout=1.0)


# ── _stale_check_loop (lines 100-109) ────────────────────────────────────────

class TestStaleCheckLoop:
    async def test_stale_check_triggers_reconnect_when_data_is_stale(self, conn):
        """_stale_check_loop calls _trigger_reconnect when staleness exceeds threshold."""
        conn._state = ConnectionState.CONNECTED
        conn._last_message_time = 1.0  # effectively epoch → very old

        conn._trigger_reconnect = AsyncMock()

        sleep_count = 0

        async def fast_sleep(delay):
            nonlocal sleep_count
            sleep_count += 1

        with patch("asyncio.sleep", side_effect=fast_sleep):
            await conn._stale_check_loop()

        conn._trigger_reconnect.assert_called_once()

    async def test_stale_check_no_reconnect_when_last_message_is_zero(self, conn):
        """_stale_check_loop skips reconnect when _last_message_time == 0."""
        conn._state = ConnectionState.CONNECTED
        conn._last_message_time = 0.0  # never received message

        conn._trigger_reconnect = AsyncMock()

        iteration = 0

        async def fast_sleep(delay):
            nonlocal iteration
            iteration += 1
            if iteration >= 2:
                conn._state = ConnectionState.DISCONNECTED

        with patch("asyncio.sleep", side_effect=fast_sleep):
            await conn._stale_check_loop()

        conn._trigger_reconnect.assert_not_called()

    async def test_stale_check_exits_when_state_not_connected(self, conn):
        """_stale_check_loop exits immediately when state != CONNECTED."""
        conn._state = ConnectionState.DISCONNECTED
        await asyncio.wait_for(conn._stale_check_loop(), timeout=1.0)

    async def test_stale_check_no_reconnect_when_fresh(self, conn):
        """_stale_check_loop does not reconnect when data is fresh."""
        conn._state = ConnectionState.CONNECTED
        import time as _time
        conn._last_message_time = _time.monotonic()  # just now — not stale

        conn._trigger_reconnect = AsyncMock()

        iteration = 0

        async def fast_sleep(delay):
            nonlocal iteration
            iteration += 1
            if iteration >= 2:
                conn._state = ConnectionState.DISCONNECTED

        with patch("asyncio.sleep", side_effect=fast_sleep):
            await conn._stale_check_loop()

        conn._trigger_reconnect.assert_not_called()


# ── _trigger_reconnect (lines 111-136) ────────────────────────────────────────

class TestTriggerReconnect:
    async def test_max_attempts_reached_sets_disconnected(self, conn):
        """_trigger_reconnect sets DISCONNECTED when max attempts exhausted."""
        conn._reconnect_attempts = conn.config.max_reconnect_attempts

        await conn._trigger_reconnect()

        assert conn._state == ConnectionState.DISCONNECTED

    async def test_max_attempts_reached_does_not_call_connect(self, conn):
        """_trigger_reconnect does not attempt connect when max attempts reached."""
        conn._reconnect_attempts = conn.config.max_reconnect_attempts
        conn._connect_fn = AsyncMock()

        await conn._trigger_reconnect()

        conn._connect_fn.assert_not_called()

    async def test_reconnect_calls_connect_fn(self, conn):
        """_trigger_reconnect calls connect_fn when attempts < max."""
        conn._reconnect_attempts = 0
        conn._start_monitoring = MagicMock()

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await conn._trigger_reconnect()

        # connect() resets counter to 0 on success; verify it was called
        conn._connect_fn.assert_called_once()

    async def test_reconnect_success_sets_connected_state(self, conn):
        """_trigger_reconnect leaves state as CONNECTED on successful reconnect."""
        conn._reconnect_attempts = 0
        conn._start_monitoring = MagicMock()

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await conn._trigger_reconnect()

        assert conn._state == ConnectionState.CONNECTED

    async def test_reconnect_failure_sets_disconnected(self, conn):
        """_trigger_reconnect sets DISCONNECTED when reconnect raises."""
        conn._reconnect_attempts = 0
        conn._connect_fn = AsyncMock(side_effect=ConnectionError("refused"))

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await conn._trigger_reconnect()

        assert conn._state == ConnectionState.DISCONNECTED

    async def test_reconnect_sets_reconnecting_state_first(self, conn):
        """_trigger_reconnect transitions through RECONNECTING before final state."""
        states_seen = []
        original_cb = conn._on_state_change

        def capture_state(exchange_id, state):
            states_seen.append(state)
            if original_cb:
                original_cb(exchange_id, state)

        conn._on_state_change = capture_state
        conn._reconnect_attempts = conn.config.max_reconnect_attempts

        await conn._trigger_reconnect()

        assert ConnectionState.RECONNECTING in states_seen

    async def test_reconnect_uses_exponential_backoff_capped_at_max(self, conn):
        """_trigger_reconnect delay is capped at max_reconnect_delay."""
        conn._reconnect_attempts = 20  # would be huge without cap
        conn.config.max_reconnect_delay = 0.05
        conn.config.max_reconnect_attempts = 100  # allow reconnect

        conn._start_monitoring = MagicMock()
        delays_used = []

        async def capture_sleep(delay):
            delays_used.append(delay)

        with patch("asyncio.sleep", side_effect=capture_sleep):
            await conn._trigger_reconnect()

        assert delays_used[0] <= 0.05


# ── WebSocketConnection lifecycle ─────────────────────────────────────────────

class TestWebSocketConnectionLifecycle:
    async def test_connect_transitions_to_connected(self, conn):
        """connect() transitions state to CONNECTED on success."""
        conn._start_monitoring = MagicMock()
        await conn.connect()
        assert conn._state == ConnectionState.CONNECTED

    async def test_connect_resets_reconnect_attempts(self, conn):
        """connect() resets reconnect attempts counter to 0."""
        conn._reconnect_attempts = 5
        conn._start_monitoring = MagicMock()
        await conn.connect()
        assert conn._reconnect_attempts == 0

    async def test_connect_failure_sets_disconnected(self, config, state_cb):
        """connect() sets DISCONNECTED when connect_fn raises."""
        conn = WebSocketConnection(config, AsyncMock(side_effect=RuntimeError("fail")), state_cb)
        with pytest.raises(RuntimeError):
            await conn.connect()
        assert conn._state == ConnectionState.DISCONNECTED

    async def test_disconnect_stops_monitoring_and_sets_disconnected(self, conn):
        """disconnect() sets state to DISCONNECTED."""
        conn._start_monitoring = MagicMock()
        await conn.connect()
        await conn.disconnect()
        assert conn._state == ConnectionState.DISCONNECTED

    def test_record_message_updates_timestamp(self, conn):
        """record_message() advances _last_message_time."""
        old_ts = conn._last_message_time
        conn.record_message()
        assert conn._last_message_time > old_ts

    def test_set_state_invokes_callback(self, conn, state_cb):
        """_set_state calls on_state_change with exchange_id and new state."""
        conn._set_state(ConnectionState.CONNECTING)
        state_cb.assert_called_once_with("binance", ConnectionState.CONNECTING)

    def test_set_state_no_callback_is_safe(self, config):
        """_set_state works when on_state_change is None."""
        conn = WebSocketConnection(config, AsyncMock(), on_state_change=None)
        conn._set_state(ConnectionState.CONNECTED)  # must not raise


# ── WebSocketManager pool (lines 151-173) ─────────────────────────────────────

class TestWebSocketManager:
    def test_add_and_get_connection(self, conn):
        """add_connection stores connection; get_connection retrieves it."""
        manager = WebSocketManager()
        manager.add_connection(conn)
        assert manager.get_connection("binance") is conn

    def test_get_unknown_exchange_returns_none(self):
        """get_connection returns None for unregistered exchange."""
        assert WebSocketManager().get_connection("unknown") is None

    async def test_connect_all_connects_each_exchange(self):
        """connect_all calls connect() on all registered connections."""
        manager = WebSocketManager()
        for eid in ["binance", "okx", "bybit"]:
            cfg = ConnectionConfig(exchange_id=eid)
            c = WebSocketConnection(cfg, AsyncMock(), None)
            c._start_monitoring = MagicMock()
            manager.add_connection(c)

        await manager.connect_all()

        for state in manager.get_all_states().values():
            assert state == ConnectionState.CONNECTED

    async def test_disconnect_all_disconnects_each_exchange(self):
        """disconnect_all calls disconnect() on all registered connections."""
        manager = WebSocketManager()
        for eid in ["binance", "okx"]:
            cfg = ConnectionConfig(exchange_id=eid)
            c = WebSocketConnection(cfg, AsyncMock(), None)
            c._start_monitoring = MagicMock()
            manager.add_connection(c)

        await manager.connect_all()
        await manager.disconnect_all()

        for state in manager.get_all_states().values():
            assert state == ConnectionState.DISCONNECTED

    async def test_connect_all_tolerates_partial_failure(self):
        """connect_all continues even when some connections fail (return_exceptions=True)."""
        manager = WebSocketManager()

        good = WebSocketConnection(
            ConnectionConfig(exchange_id="ok"), AsyncMock(), None
        )
        good._start_monitoring = MagicMock()

        bad = WebSocketConnection(
            ConnectionConfig(exchange_id="bad"),
            AsyncMock(side_effect=ConnectionError("refused")),
            None,
        )

        manager.add_connection(good)
        manager.add_connection(bad)

        await manager.connect_all()  # must not raise

        assert good.state == ConnectionState.CONNECTED
        assert bad.state == ConnectionState.DISCONNECTED

    def test_get_all_states_includes_all_exchanges(self):
        """get_all_states returns an entry for every registered connection."""
        manager = WebSocketManager()
        for eid in ["binance", "okx"]:
            cfg = ConnectionConfig(exchange_id=eid)
            c = WebSocketConnection(cfg, AsyncMock(), None)
            manager.add_connection(c)

        states = manager.get_all_states()
        assert set(states.keys()) == {"binance", "okx"}

    def test_connected_count_counts_only_connected_exchanges(self):
        """connected_count returns number of exchanges in CONNECTED state."""
        manager = WebSocketManager()
        for eid, state in [("binance", ConnectionState.CONNECTED),
                            ("okx", ConnectionState.CONNECTED),
                            ("bybit", ConnectionState.DISCONNECTED)]:
            cfg = ConnectionConfig(exchange_id=eid)
            c = WebSocketConnection(cfg, AsyncMock(), None)
            c._state = state
            manager.add_connection(c)

        assert manager.connected_count == 2
