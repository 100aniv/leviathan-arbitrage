"""Additional coverage for BaseCollector — start() loop and _connect_and_listen().

Covers lines not hit by test_base_collector.py:
  - start() retry logic, CancelledError exit, backoff between retries
  - _connect_and_listen(): dict/str subscription, reconnect-delay reset,
    message count, running-flag stop, parse-error swallowing, multi-symbol
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.collectors.base_collector import BaseCollector


# ---------------------------------------------------------------------------
# Concrete collectors for testing
# ---------------------------------------------------------------------------


class _DictSubCollector(BaseCollector):
    """Sends dict subscription messages."""

    def _ws_url(self) -> str:
        return "wss://test.example.com/ws"

    def _subscribe_message(self, symbol: str) -> dict:
        return {"op": "subscribe", "symbol": symbol}

    def _parse_message(self, data: dict) -> tuple[str, list, list] | None:
        if data.get("type") == "orderbook":
            return data["symbol"], data["bids"], data["asks"]
        return None


class _StrSubCollector(BaseCollector):
    """Sends string subscription messages."""

    def _ws_url(self) -> str:
        return "wss://test.example.com/ws"

    def _subscribe_message(self, symbol: str) -> str:
        return f"subscribe:{symbol}"

    def _parse_message(self, data: dict) -> None:
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ws_ctx(messages: list[str] | None = None, send_side_effect=None):
    """Return (context_manager, ws) with optional messages and send side_effect.

    Uses real classes so Python's special-method lookup (type-based) works
    correctly for both async context manager and async iterator protocols.
    """
    msgs = list(messages or [])

    class _MockWS:
        """Async-iterable fake WebSocket."""

        def __init__(self) -> None:
            self.send = AsyncMock(side_effect=send_side_effect)

        def __aiter__(self):
            return self._gen()

        async def _gen(self):
            for msg in msgs:
                yield msg

    class _MockCM:
        """Async context manager that returns a _MockWS."""

        def __init__(self) -> None:
            self._ws = _MockWS()

        async def __aenter__(self):
            return self._ws

        async def __aexit__(self, *_):
            return False

    ctx = _MockCM()
    return ctx, ctx._ws


# ---------------------------------------------------------------------------
# start() loop behaviour
# ---------------------------------------------------------------------------


class TestStartLoop:
    @pytest.mark.asyncio
    async def test_start_exits_cleanly_on_cancelled_error(self):
        collector = _DictSubCollector(exchange_id="binance", symbols=["BTC/USDT"])
        collector._connect_and_listen = AsyncMock(side_effect=asyncio.CancelledError())
        await collector.start()  # must not raise

    @pytest.mark.asyncio
    async def test_start_sets_running_true_on_entry(self):
        collector = _DictSubCollector(exchange_id="binance", symbols=["BTC/USDT"])
        running_on_entry: list[bool] = []

        async def capture_and_stop():
            running_on_entry.append(collector._running)
            collector._running = False

        collector._connect_and_listen = capture_and_stop
        await collector.start()

        assert running_on_entry[0] is True

    @pytest.mark.asyncio
    async def test_start_retries_after_connection_error(self):
        collector = _DictSubCollector(exchange_id="binance", symbols=["BTC/USDT"])
        call_count = 0

        async def fail_then_stop():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("ws failed")
            collector._running = False

        collector._connect_and_listen = fail_then_stop

        with patch("asyncio.sleep", new=AsyncMock()):
            await collector.start()

        assert call_count == 2

    @pytest.mark.asyncio
    async def test_start_stops_immediately_when_not_running_after_exception(self):
        collector = _DictSubCollector(exchange_id="binance", symbols=["BTC/USDT"])
        call_count = 0

        async def fail_and_stop():
            nonlocal call_count
            call_count += 1
            collector._running = False
            raise ConnectionError("already disconnected")

        collector._connect_and_listen = fail_and_stop

        with patch("asyncio.sleep", new=AsyncMock()):
            await collector.start()

        assert call_count == 1  # did not retry

    @pytest.mark.asyncio
    async def test_start_backoff_delay_increases_between_retries(self):
        collector = _DictSubCollector(exchange_id="binance", symbols=["BTC/USDT"])
        slept_delays: list[float] = []

        async def fake_sleep(delay: float):
            slept_delays.append(delay)

        call_count = 0

        async def fail_twice_then_stop():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("retry me")
            collector._running = False

        collector._connect_and_listen = fail_twice_then_stop

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await collector.start()

        assert len(slept_delays) == 2
        assert slept_delays[1] > slept_delays[0]


# ---------------------------------------------------------------------------
# _connect_and_listen() behaviour
# ---------------------------------------------------------------------------


class TestConnectAndListen:
    @pytest.mark.asyncio
    async def test_subscribes_with_json_for_dict_message(self):
        collector = _DictSubCollector(exchange_id="binance", symbols=["BTC/USDT"])
        collector._running = True
        sent: list[str] = []

        cm, ws = _make_ws_ctx()
        ws.send = AsyncMock(side_effect=lambda m: sent.append(m))

        with patch("websockets.connect", return_value=cm):
            await collector._connect_and_listen()

        assert len(sent) == 1
        assert json.loads(sent[0])["symbol"] == "BTC/USDT"

    @pytest.mark.asyncio
    async def test_subscribes_with_raw_string_for_str_message(self):
        collector = _StrSubCollector(exchange_id="test", symbols=["ETH/USDT"])
        collector._running = True
        sent: list[str] = []

        cm, ws = _make_ws_ctx()
        ws.send = AsyncMock(side_effect=lambda m: sent.append(m))

        with patch("websockets.connect", return_value=cm):
            await collector._connect_and_listen()

        assert sent[0] == "subscribe:ETH/USDT"

    @pytest.mark.asyncio
    async def test_sends_one_subscription_per_symbol(self):
        collector = _DictSubCollector(
            exchange_id="binance", symbols=["BTC/USDT", "ETH/USDT", "SOL/USDT"]
        )
        collector._running = True
        sent: list[str] = []

        cm, ws = _make_ws_ctx()
        ws.send = AsyncMock(side_effect=lambda m: sent.append(m))

        with patch("websockets.connect", return_value=cm):
            await collector._connect_and_listen()

        assert len(sent) == 3
        symbols = [json.loads(s)["symbol"] for s in sent]
        assert set(symbols) == {"BTC/USDT", "ETH/USDT", "SOL/USDT"}

    @pytest.mark.asyncio
    async def test_resets_reconnect_delay_after_successful_connect(self):
        collector = _DictSubCollector(exchange_id="binance", symbols=["BTC/USDT"])
        collector._running = True
        collector._reconnect_delay = 32.0

        cm, _ = _make_ws_ctx()

        with patch("websockets.connect", return_value=cm):
            await collector._connect_and_listen()

        assert collector._reconnect_delay == BaseCollector.INITIAL_RECONNECT_DELAY

    @pytest.mark.asyncio
    async def test_increments_message_count_for_each_received_message(self):
        collector = _DictSubCollector(exchange_id="binance", symbols=["BTC/USDT"])
        collector._running = True
        messages = [
            '{"type": "orderbook", "symbol": "BTC/USDT", "bids": [], "asks": []}',
            '{"type": "orderbook", "symbol": "BTC/USDT", "bids": [], "asks": []}',
        ]
        cm, _ = _make_ws_ctx(messages)

        with patch("websockets.connect", return_value=cm):
            await collector._connect_and_listen()

        assert collector._message_count == 2

    @pytest.mark.asyncio
    async def test_stops_processing_when_running_set_false_before_message(self):
        """running=False check fires before _message_count increments."""
        collector = _DictSubCollector(exchange_id="binance", symbols=["BTC/USDT"])
        collector._running = True  # start running; generator will flip it

        class _StoppingCM:
            class _WS:
                send = AsyncMock()

                def __aiter__(self):
                    return self._gen()

                async def _gen(self):
                    collector._running = False  # flip before body executes
                    yield '{"type": "orderbook", "symbol": "BTC/USDT", "bids": [], "asks": []}'

            async def __aenter__(self):
                return self._WS()

            async def __aexit__(self, *_):
                return False

        with patch("websockets.connect", return_value=_StoppingCM()):
            await collector._connect_and_listen()

        assert collector._message_count == 0  # break fired before incrementing

    @pytest.mark.asyncio
    async def test_clears_connected_flag_after_context_exits(self):
        collector = _DictSubCollector(exchange_id="binance", symbols=["BTC/USDT"])
        collector._running = True
        cm, _ = _make_ws_ctx()

        with patch("websockets.connect", return_value=cm):
            await collector._connect_and_listen()

        assert collector._connected is False

    @pytest.mark.asyncio
    async def test_swallows_exceptions_raised_by_handle_message(self):
        """Errors in _handle_message must be caught, not propagated."""
        collector = _DictSubCollector(exchange_id="binance", symbols=["BTC/USDT"])
        collector._running = True

        async def broken_handle(raw):
            raise ValueError("parse boom")

        collector._handle_message = broken_handle  # type: ignore[method-assign]

        messages = ['{"type": "orderbook", "symbol": "BTC/USDT", "bids": [], "asks": []}']
        cm, _ = _make_ws_ctx(messages)

        with patch("websockets.connect", return_value=cm):
            await collector._connect_and_listen()  # must not raise

        # message was received (count incremented) even though handler raised
        assert collector._message_count == 1

    @pytest.mark.asyncio
    async def test_sets_connected_true_during_listen(self):
        collector = _DictSubCollector(exchange_id="binance", symbols=["BTC/USDT"])
        collector._running = True
        connected_during: list[bool] = []

        class _CapturingCM:
            class _WS:
                send = AsyncMock()

                def __aiter__(self):
                    return self._gen()

                async def _gen(self):
                    connected_during.append(collector._connected)
                    return
                    yield  # make it an async generator

            async def __aenter__(self):
                return self._WS()

            async def __aexit__(self, *_):
                return False

        with patch("websockets.connect", return_value=_CapturingCM()):
            await collector._connect_and_listen()

        assert connected_during[0] is True
        assert collector._connected is False  # reset after exit
