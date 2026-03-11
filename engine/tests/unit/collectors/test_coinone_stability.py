"""Unit tests for CoinoneCollector stability features (US-074).

Covers:
- Exponential backoff jitter in BaseCollector._backoff()
- Data gap watchdog (_data_gap_watchdog)
- Application-level ping loop (_application_ping_loop)
- Symbol staleness detection (is_symbol_stale)
- PONG response handling in _parse_message
"""
from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, patch

import pytest

from src.collectors.base_collector import BaseCollector
from src.collectors.coinone_collector import CoinoneCollector


# ---------------------------------------------------------------------------
# TestBackoffJitter
# ---------------------------------------------------------------------------


class TestBackoffJitter:
    """Verify BaseCollector._backoff() applies ±25% random jitter."""

    async def test_backoff_delay_has_jitter(self):
        """random.uniform is called with (0.75, 1.25) and applied to delay."""
        collector = CoinoneCollector(symbols=["BTC/KRW"])
        with patch("src.collectors.base_collector.random.uniform", return_value=1.1) as mock_rand, \
             patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await collector._backoff()
            mock_rand.assert_called_once_with(0.75, 1.25)
            mock_sleep.assert_called_once_with(1.0 * 1.1)

    async def test_backoff_stays_within_bounds(self):
        """_reconnect_delay never exceeds MAX_RECONNECT_DELAY regardless of jitter."""
        collector = CoinoneCollector(symbols=["BTC/KRW"])
        with patch("src.collectors.base_collector.random.uniform", return_value=1.25), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            for _ in range(15):
                await collector._backoff()
        assert collector._reconnect_delay <= BaseCollector.MAX_RECONNECT_DELAY

    async def test_backoff_increases_exponentially(self):
        """_reconnect_delay doubles on each call (jitter=1.0 for determinism)."""
        collector = CoinoneCollector(symbols=["BTC/KRW"])
        with patch("src.collectors.base_collector.random.uniform", return_value=1.0), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await collector._backoff()
            assert collector._reconnect_delay == 2.0
            await collector._backoff()
            assert collector._reconnect_delay == 4.0
            await collector._backoff()
            assert collector._reconnect_delay == 8.0


# ---------------------------------------------------------------------------
# TestDataGapWatchdog
# ---------------------------------------------------------------------------


class TestDataGapWatchdog:
    """Verify _data_gap_watchdog closes WebSocket on data gaps."""

    async def test_watchdog_closes_ws_on_gap(self):
        """When last_message_time > 120s ago, ws.close() is called."""
        collector = CoinoneCollector(symbols=["BTC/KRW"])
        mock_ws = AsyncMock()
        collector._last_message_time = time.monotonic() - 130.0

        async def fake_sleep(_n):
            pass  # Return immediately

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await collector._data_gap_watchdog(mock_ws)

        mock_ws.close.assert_called_once()

    async def test_watchdog_does_not_trigger_within_threshold(self):
        """When gap < 120s, ws.close() is NOT called."""
        collector = CoinoneCollector(symbols=["BTC/KRW"])
        mock_ws = AsyncMock()
        collector._last_message_time = time.monotonic() - 60.0

        call_count = 0

        async def fake_sleep(_n):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                await collector._data_gap_watchdog(mock_ws)

        mock_ws.close.assert_not_called()

    async def test_watchdog_skips_when_no_messages_yet(self):
        """When _last_message_time == 0.0, watchdog does not close WS."""
        collector = CoinoneCollector(symbols=["BTC/KRW"])
        mock_ws = AsyncMock()
        collector._last_message_time = 0.0  # Default: no messages received

        call_count = 0

        async def fake_sleep(_n):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                await collector._data_gap_watchdog(mock_ws)

        mock_ws.close.assert_not_called()

    async def test_watchdog_exactly_at_threshold_does_not_trigger(self):
        """A gap of 119s does NOT trigger (threshold is strictly > 120s)."""
        collector = CoinoneCollector(symbols=["BTC/KRW"])
        mock_ws = AsyncMock()
        collector._last_message_time = time.monotonic() - 119.0

        call_count = 0

        async def fake_sleep(_n):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                await collector._data_gap_watchdog(mock_ws)

        mock_ws.close.assert_not_called()


# ---------------------------------------------------------------------------
# TestAppPingLoop
# ---------------------------------------------------------------------------


class TestAppPingLoop:
    """Verify _application_ping_loop sends JSON PING messages."""

    async def test_ping_loop_sends_json_ping(self):
        """After sleep, sends {"request_type": "PING"} via ws.send."""
        collector = CoinoneCollector(symbols=["BTC/KRW"])
        mock_ws = AsyncMock()

        call_count = 0

        async def fake_sleep(_n):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                await collector._application_ping_loop(mock_ws)

        mock_ws.send.assert_called_once_with(json.dumps({"request_type": "PING"}))

    async def test_ping_loop_handles_send_failure(self):
        """When ws.send raises, logs warning and returns without re-raising."""
        collector = CoinoneCollector(symbols=["BTC/KRW"])
        mock_ws = AsyncMock()
        mock_ws.send = AsyncMock(side_effect=Exception("connection closed"))

        async def fake_sleep(_n):
            pass  # Return immediately (only called once before send fails)

        with patch("asyncio.sleep", side_effect=fake_sleep):
            # Must complete normally (no exception propagation)
            await collector._application_ping_loop(mock_ws)

        mock_ws.send.assert_called_once()

    async def test_ping_loop_sleeps_app_ping_interval(self):
        """Confirms sleep is called with _APP_PING_INTERVAL_S (1500s)."""
        from src.collectors.coinone_collector import _APP_PING_INTERVAL_S

        collector = CoinoneCollector(symbols=["BTC/KRW"])
        mock_ws = AsyncMock()
        sleep_args = []

        async def fake_sleep(n):
            sleep_args.append(n)
            raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                await collector._application_ping_loop(mock_ws)

        assert sleep_args[0] == _APP_PING_INTERVAL_S


# ---------------------------------------------------------------------------
# TestParseMessage
# ---------------------------------------------------------------------------


class TestParseMessage:
    """Verify _parse_message handles PONG and ORDERBOOK DATA correctly."""

    def test_parse_pong_returns_none(self):
        """response_type=PONG → return None (not an orderbook update)."""
        collector = CoinoneCollector(symbols=["BTC/KRW"])
        result = collector._parse_message({"response_type": "PONG"})
        assert result is None

    def test_parse_unknown_type_returns_none(self):
        """Unknown response_type → return None."""
        collector = CoinoneCollector(symbols=["BTC/KRW"])
        result = collector._parse_message({"response_type": "UNKNOWN"})
        assert result is None

    def test_parse_data_orderbook_valid(self):
        """Valid DATA/ORDERBOOK message returns (symbol, bids, asks)."""
        collector = CoinoneCollector(symbols=["BTC/KRW"])
        data = {
            "response_type": "DATA",
            "channel": "ORDERBOOK",
            "data": {
                "quote_currency": "KRW",
                "target_currency": "BTC",
                "timestamp": 1234567890,
                "bids": [
                    {"price": "50000000", "qty": "0.5"},
                    {"price": "49900000", "qty": "1.0"},
                ],
                "asks": [
                    {"price": "50100000", "qty": "0.3"},
                    {"price": "50200000", "qty": "0.2"},
                ],
            },
        }
        result = collector._parse_message(data)
        assert result is not None
        symbol, bids, asks = result
        assert symbol == "BTC/KRW"
        assert bids[0][0] == "50000000"   # Highest bid first
        assert asks[0][0] == "50100000"   # Lowest ask first

    def test_parse_non_orderbook_channel_returns_none(self):
        """DATA with channel != ORDERBOOK → return None."""
        collector = CoinoneCollector(symbols=["BTC/KRW"])
        result = collector._parse_message({
            "response_type": "DATA",
            "channel": "TRADE",
            "data": {"quote_currency": "KRW", "target_currency": "BTC"},
        })
        assert result is None

    def test_parse_empty_data_payload_returns_none(self):
        """DATA with empty data dict → return None."""
        collector = CoinoneCollector(symbols=["BTC/KRW"])
        result = collector._parse_message({
            "response_type": "DATA",
            "channel": "ORDERBOOK",
            "data": {},
        })
        assert result is None


# ---------------------------------------------------------------------------
# TestSymbolStale
# ---------------------------------------------------------------------------


class TestSymbolStale:
    """Verify is_symbol_stale() and _last_symbol_time tracking."""

    def test_is_symbol_stale_no_data(self):
        """Symbol with no data received → stale=True."""
        collector = CoinoneCollector(symbols=["BTC/KRW"])
        assert collector.is_symbol_stale("BTC/KRW") is True

    def test_is_symbol_stale_fresh_data(self):
        """Symbol updated 1s ago → stale=False (default threshold=300s)."""
        collector = CoinoneCollector(symbols=["BTC/KRW"])
        collector._last_symbol_time["BTC/KRW"] = time.monotonic() - 1.0
        assert collector.is_symbol_stale("BTC/KRW") is False

    def test_is_symbol_stale_old_data(self):
        """Symbol updated 400s ago → stale=True."""
        collector = CoinoneCollector(symbols=["BTC/KRW"])
        collector._last_symbol_time["BTC/KRW"] = time.monotonic() - 400.0
        assert collector.is_symbol_stale("BTC/KRW") is True

    def test_is_symbol_stale_custom_threshold(self):
        """Custom threshold: 100s old with threshold=120 → not stale."""
        collector = CoinoneCollector(symbols=["BTC/KRW"])
        collector._last_symbol_time["BTC/KRW"] = time.monotonic() - 100.0
        assert collector.is_symbol_stale("BTC/KRW", max_age_s=120.0) is False
        assert collector.is_symbol_stale("BTC/KRW", max_age_s=90.0) is True

    def test_last_symbol_time_updated_on_parse(self):
        """After _parse_message(), _last_symbol_time is set for the symbol."""
        collector = CoinoneCollector(symbols=["BTC/KRW"])
        data = {
            "response_type": "DATA",
            "channel": "ORDERBOOK",
            "data": {
                "quote_currency": "KRW",
                "target_currency": "BTC",
                "bids": [{"price": "50000000", "qty": "0.5"}],
                "asks": [{"price": "50100000", "qty": "0.3"}],
            },
        }
        before = time.monotonic()
        collector._parse_message(data)
        after = time.monotonic()
        assert "BTC/KRW" in collector._last_symbol_time
        assert before <= collector._last_symbol_time["BTC/KRW"] <= after

    def test_pong_does_not_update_symbol_time(self):
        """PONG responses do not modify _last_symbol_time."""
        collector = CoinoneCollector(symbols=["BTC/KRW"])
        collector._parse_message({"response_type": "PONG"})
        assert "BTC/KRW" not in collector._last_symbol_time

    def test_multiple_symbols_tracked_independently(self):
        """Each symbol has its own timestamp entry."""
        collector = CoinoneCollector(symbols=["BTC/KRW", "ETH/KRW"])
        collector._last_symbol_time["BTC/KRW"] = time.monotonic() - 1.0
        # ETH not yet received
        assert collector.is_symbol_stale("BTC/KRW") is False
        assert collector.is_symbol_stale("ETH/KRW") is True


# ---------------------------------------------------------------------------
# TestPongResponseHandling
# ---------------------------------------------------------------------------


class TestPongResponseHandling:
    """Verify PONG frame is properly handled."""

    def test_pong_response_returns_none(self):
        """PONG returns None → _handle_message will skip callback."""
        collector = CoinoneCollector(symbols=["BTC/KRW"])
        assert collector._parse_message({"response_type": "PONG"}) is None

    def test_subscribe_ack_returns_none(self):
        """Subscription acknowledgment (SUBSCRIBE response type) → None."""
        collector = CoinoneCollector(symbols=["BTC/KRW"])
        result = collector._parse_message({"response_type": "SUBSCRIBE", "channel": "ORDERBOOK"})
        assert result is None
