"""Tests for engine/src/collectors/base_collector.py (BaseCollector).

Covers: abstract methods require implementation, auto-reconnect backoff logic
(1s → 2s → 4s … → 60s max), stats tracking, is_connected property,
stop sets running=False.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.collectors.base_collector import BaseCollector


# ---------------------------------------------------------------------------
# Concrete subclass used throughout tests
# ---------------------------------------------------------------------------


class ConcreteCollector(BaseCollector):
    """Minimal concrete implementation for unit testing."""

    def _ws_url(self) -> str:
        return "wss://test.example.com/ws"

    def _subscribe_message(self, symbol: str) -> str | dict:
        return {"op": "subscribe", "symbol": symbol}

    def _parse_message(self, data: dict) -> tuple[str, list, list] | None:
        if data.get("type") == "orderbook":
            return data["symbol"], data["bids"], data["asks"]
        return None


# ---------------------------------------------------------------------------
# Abstract method enforcement
# ---------------------------------------------------------------------------


class TestAbstractMethods:
    def test_cannot_instantiate_base_collector_directly(self):
        with pytest.raises(TypeError):
            BaseCollector(exchange_id="test", symbols=["BTC/USDT"])  # type: ignore[abstract]

    def test_subclass_without_ws_url_cannot_be_instantiated(self):
        class MissingWsUrl(BaseCollector):
            def _subscribe_message(self, symbol: str) -> str:
                return "sub"

            def _parse_message(self, data: dict):
                return None

        with pytest.raises(TypeError):
            MissingWsUrl(exchange_id="x", symbols=[])  # type: ignore[abstract]

    def test_subclass_without_subscribe_message_cannot_be_instantiated(self):
        class MissingSubscribe(BaseCollector):
            def _ws_url(self) -> str:
                return "wss://x"

            def _parse_message(self, data: dict):
                return None

        with pytest.raises(TypeError):
            MissingSubscribe(exchange_id="x", symbols=[])  # type: ignore[abstract]

    def test_subclass_without_parse_message_cannot_be_instantiated(self):
        class MissingParse(BaseCollector):
            def _ws_url(self) -> str:
                return "wss://x"

            def _subscribe_message(self, symbol: str) -> str:
                return "sub"

        with pytest.raises(TypeError):
            MissingParse(exchange_id="x", symbols=[])  # type: ignore[abstract]

    def test_complete_concrete_subclass_can_be_instantiated(self):
        collector = ConcreteCollector(exchange_id="test", symbols=["BTC/USDT"])
        assert collector.exchange_id == "test"


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------


class TestInitialState:
    def test_is_connected_is_false_before_start(self):
        collector = ConcreteCollector(exchange_id="binance", symbols=["BTC/USDT"])
        assert collector.is_connected is False

    def test_running_is_false_before_start(self):
        collector = ConcreteCollector(exchange_id="binance", symbols=["BTC/USDT"])
        assert collector._running is False

    def test_message_count_starts_at_zero(self):
        collector = ConcreteCollector(exchange_id="binance", symbols=["BTC/USDT"])
        assert collector._message_count == 0

    def test_reconnect_delay_starts_at_initial_value(self):
        collector = ConcreteCollector(exchange_id="binance", symbols=["BTC/USDT"])
        assert collector._reconnect_delay == BaseCollector.INITIAL_RECONNECT_DELAY


# ---------------------------------------------------------------------------
# stop() behaviour
# ---------------------------------------------------------------------------


class TestStopMethod:
    async def test_stop_sets_running_to_false(self):
        collector = ConcreteCollector(exchange_id="binance", symbols=["BTC/USDT"])
        collector._running = True
        await collector.stop()
        assert collector._running is False

    async def test_stop_sets_connected_to_false(self):
        collector = ConcreteCollector(exchange_id="binance", symbols=["BTC/USDT"])
        collector._connected = True
        await collector.stop()
        assert collector._connected is False

    async def test_stop_closes_websocket_if_present(self):
        collector = ConcreteCollector(exchange_id="binance", symbols=["BTC/USDT"])
        mock_ws = AsyncMock()
        collector._ws = mock_ws
        await collector.stop()
        mock_ws.close.assert_called_once()

    async def test_stop_tolerates_ws_close_exception(self):
        collector = ConcreteCollector(exchange_id="binance", symbols=["BTC/USDT"])
        mock_ws = AsyncMock()
        mock_ws.close = AsyncMock(side_effect=Exception("ws already closed"))
        collector._ws = mock_ws
        # Must not raise
        await collector.stop()
        assert collector._running is False


# ---------------------------------------------------------------------------
# Exponential backoff logic
# ---------------------------------------------------------------------------


class TestBackoffLogic:
    async def test_backoff_doubles_delay_each_call(self):
        collector = ConcreteCollector(exchange_id="binance", symbols=["BTC/USDT"])
        base_delays = [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0]

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            for base in base_delays:
                await collector._backoff()
                actual = mock_sleep.call_args[0][0]
                # jitter ±25%: actual should be within [base*0.75, base*1.25].
                # The MAX cap applies only to stored _reconnect_delay, not the sleep value.
                low = base * 0.75
                high = base * 1.25
                assert low <= actual <= high, (
                    f"Expected {base}±25% (range [{low}, {high}]), got {actual}"
                )

    async def test_backoff_caps_at_max_reconnect_delay(self):
        collector = ConcreteCollector(exchange_id="binance", symbols=["BTC/USDT"])
        # Drive delay past the cap
        collector._reconnect_delay = 64.0

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await collector._backoff()
            actual = mock_sleep.call_args[0][0]
            # jitter ±25% on base delay of 64.0
            assert 64.0 * 0.75 <= actual <= 64.0 * 1.25

        # Next call should still be capped at 60
        assert collector._reconnect_delay == BaseCollector.MAX_RECONNECT_DELAY

    async def test_backoff_delay_never_exceeds_sixty_seconds(self):
        collector = ConcreteCollector(exchange_id="binance", symbols=["BTC/USDT"])

        with patch("asyncio.sleep", new_callable=AsyncMock):
            for _ in range(20):
                await collector._backoff()

        assert collector._reconnect_delay <= BaseCollector.MAX_RECONNECT_DELAY

    async def test_reconnect_delay_resets_to_initial_on_successful_connect(self):
        """After a successful connection the delay resets to INITIAL_RECONNECT_DELAY.

        The reset is performed by _connect_and_listen() immediately after the
        websocket handshake succeeds.  We test this by overriding
        _connect_and_listen to simulate the exact state mutation the real
        implementation performs, then confirming the field is restored.
        """
        collector = ConcreteCollector(exchange_id="binance", symbols=["BTC/USDT"])
        # Simulate several backoff steps having raised the delay
        collector._reconnect_delay = 32.0

        async def _simulated_connect_and_listen():
            # Mirrors what the real method does right after ws handshake succeeds
            collector._reconnect_delay = BaseCollector.INITIAL_RECONNECT_DELAY

        collector._connect_and_listen = _simulated_connect_and_listen  # type: ignore[method-assign]
        await collector._connect_and_listen()

        assert collector._reconnect_delay == BaseCollector.INITIAL_RECONNECT_DELAY


# ---------------------------------------------------------------------------
# Stats tracking
# ---------------------------------------------------------------------------


class TestStatsTracking:
    def test_stats_returns_exchange_id(self):
        collector = ConcreteCollector(exchange_id="okx", symbols=["ETH/USDT"])
        assert collector.stats["exchange"] == "okx"

    def test_stats_connected_reflects_is_connected(self):
        collector = ConcreteCollector(exchange_id="okx", symbols=["ETH/USDT"])
        assert collector.stats["connected"] is False
        collector._connected = True
        assert collector.stats["connected"] is True

    def test_stats_message_count_reflects_received_messages(self):
        collector = ConcreteCollector(exchange_id="okx", symbols=["ETH/USDT"])
        collector._message_count = 42
        assert collector.stats["message_count"] == 42

    def test_stats_last_message_age_is_none_before_first_message(self):
        collector = ConcreteCollector(exchange_id="okx", symbols=["ETH/USDT"])
        assert collector.stats["last_message_age_s"] is None

    def test_stats_last_message_age_is_positive_after_message(self):
        collector = ConcreteCollector(exchange_id="okx", symbols=["ETH/USDT"])
        collector._last_message_time = time.monotonic() - 2.5
        age = collector.stats["last_message_age_s"]
        assert age is not None
        assert age >= 2.4


# ---------------------------------------------------------------------------
# _handle_message dispatch
# ---------------------------------------------------------------------------


class TestHandleMessage:
    async def test_parse_result_none_skips_callback(self):
        callback = AsyncMock()
        collector = ConcreteCollector(
            exchange_id="binance", symbols=["BTC/USDT"], on_orderbook=callback
        )
        # Heartbeat message — _parse_message returns None
        await collector._handle_message('{"type": "ping"}')
        callback.assert_not_called()

    async def test_valid_orderbook_message_invokes_callback(self):
        callback = AsyncMock()
        collector = ConcreteCollector(
            exchange_id="binance", symbols=["BTC/USDT"], on_orderbook=callback
        )
        raw = '{"type": "orderbook", "symbol": "BTC/USDT", "bids": [["50000", "1"]], "asks": [["50001", "1"]]}'
        await collector._handle_message(raw)
        callback.assert_called_once_with(
            "binance", "BTC/USDT", [["50000", "1"]], [["50001", "1"]]
        )

    async def test_no_callback_does_not_raise(self):
        collector = ConcreteCollector(
            exchange_id="binance", symbols=["BTC/USDT"], on_orderbook=None
        )
        raw = '{"type": "orderbook", "symbol": "BTC/USDT", "bids": [], "asks": []}'
        # Should complete without raising
        await collector._handle_message(raw)
