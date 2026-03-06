"""Tests for NativeAdapter base class.

Covers:
- Signing utilities (_sign_hmac_sha256, _sign_hmac_sha512)
- Query string building and timestamp generation
- OrderBook and Trade helper builders
- connect/disconnect lifecycle
- subscribe_orderbook auto-reconnect logic (mocked websockets)
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.models import (
    Balance,
    FeeRate,
    Order,
    OrderBook,
    OrderBookLevel,
    OrderSide,
    OrderType,
    Position,
    Trade,
)
from src.infra.exchange.native_adapter import NativeAdapter
from src.infra.exchange.rate_limiter import RateLimitConfig


# ---------------------------------------------------------------------------
# Minimal concrete subclass for testing
# ---------------------------------------------------------------------------

class _TestAdapter(NativeAdapter):
    """Minimal concrete NativeAdapter for unit tests."""

    def _rest_base_url(self) -> str:
        return "https://test.exchange.com"

    def _default_headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def _auth_headers(self, method, path, params, data) -> dict[str, str]:
        ts = str(self._timestamp_ms())
        sig = self._sign_hmac_sha256(ts + method + path)
        return {"X-API-KEY": self._api_key, "X-SIGNATURE": sig, "X-TIMESTAMP": ts}

    def _ws_orderbook_url(self, symbol: str) -> str:
        sym = symbol.replace("/", "").lower()
        return f"wss://stream.test.exchange.com/{sym}@depth"

    def _ws_subscribe_message(self, symbol: str) -> dict | None:
        return {"method": "SUBSCRIBE", "params": [f"{symbol}@depth"]}

    def _parse_ws_orderbook(self, raw: str | bytes, symbol: str) -> OrderBook | None:
        try:
            data = json.loads(raw)
            if "bids" not in data:
                return None
            return self._build_orderbook(symbol, data["bids"], data["asks"])
        except Exception:
            return None

    async def _rest_get_orderbook(self, symbol: str, depth: int) -> OrderBook:
        return self._build_orderbook(symbol, [["50000", "1.0"]], [["50001", "0.5"]])

    async def _rest_place_order(self, order: Order) -> Trade:
        return self._build_trade(order, "trade-001", Decimal("50000"), order.amount)

    async def _rest_cancel_order(self, order_id: str, symbol: str | None) -> bool:
        return True

    async def _rest_cancel_all_orders(self, symbol: str | None) -> int:
        return 0

    async def _rest_get_balances(self) -> dict[str, Balance]:
        return {"USDT": Balance(currency="USDT", free=Decimal("1000"), used=Decimal("0"), total=Decimal("1000"))}

    async def _rest_get_positions(self) -> list[Position]:
        return []

    async def _rest_get_fee_rate(self, symbol: str) -> FeeRate:
        return FeeRate(maker=Decimal("0.001"), taker=Decimal("0.001"), symbol=symbol, exchange_id=self.exchange_id)


@pytest.fixture
def adapter():
    """NativeAdapter test instance with high rate limits (non-blocking)."""
    rate_limits = {
        "default": RateLimitConfig(requests_per_second=10000, burst=10000),
        "order": RateLimitConfig(requests_per_second=10000, burst=10000),
    }
    return _TestAdapter(
        exchange_id="test_exchange",
        api_key="test_key",
        api_secret="test_secret",
        passphrase="test_pass",
        sandbox=False,
        rate_limits=rate_limits,
    )


# ---------------------------------------------------------------------------
# Signing utilities
# ---------------------------------------------------------------------------

class TestSigningMethods:
    def test_sign_hmac_sha256_correctness(self, adapter):
        message = "1234567890testGET/api/v1/order"
        expected = hmac.new(
            b"test_secret", message.encode(), hashlib.sha256
        ).hexdigest()
        result = adapter._sign_hmac_sha256(message)
        assert result == expected

    def test_sign_hmac_sha256_returns_hex_string(self, adapter):
        sig = adapter._sign_hmac_sha256("hello")
        assert isinstance(sig, str)
        assert len(sig) == 64  # sha256 hex digest is always 64 chars

    def test_sign_hmac_sha512_correctness(self, adapter):
        message = "timestamp=123456&symbol=BTCUSDT"
        expected = hmac.new(
            b"test_secret", message.encode(), hashlib.sha512
        ).hexdigest()
        result = adapter._sign_hmac_sha512(message)
        assert result == expected

    def test_sign_hmac_sha512_returns_hex_string(self, adapter):
        sig = adapter._sign_hmac_sha512("payload")
        assert isinstance(sig, str)
        assert len(sig) == 128  # sha512 hex digest is always 128 chars

    def test_sign_hmac_sha256_differs_from_sha512(self, adapter):
        message = "same_message"
        sig256 = adapter._sign_hmac_sha256(message)
        sig512 = adapter._sign_hmac_sha512(message)
        assert sig256 != sig512

    def test_sign_empty_message(self, adapter):
        sig = adapter._sign_hmac_sha256("")
        assert isinstance(sig, str)
        assert len(sig) == 64

    def test_sign_unicode_message(self, adapter):
        sig = adapter._sign_hmac_sha256("비트코인/USDT")
        assert isinstance(sig, str)
        assert len(sig) == 64

    def test_sign_different_keys_produce_different_sigs(self):
        a1 = _TestAdapter("ex", api_key="k", api_secret="secret1")
        a2 = _TestAdapter("ex", api_key="k", api_secret="secret2")
        assert a1._sign_hmac_sha256("msg") != a2._sign_hmac_sha256("msg")


# ---------------------------------------------------------------------------
# Query string and timestamp
# ---------------------------------------------------------------------------

class TestQueryStringAndTimestamp:
    def test_build_query_string_sorted(self, adapter):
        params = {"symbol": "BTCUSDT", "timestamp": "123", "limit": "20"}
        qs = adapter._build_query_string(params)
        # urllib.parse.urlencode on sorted items
        assert "limit=20" in qs
        assert "symbol=BTCUSDT" in qs
        assert "timestamp=123" in qs
        # Sorted order: limit < symbol < timestamp
        assert qs.index("limit") < qs.index("symbol") < qs.index("timestamp")

    def test_build_query_string_empty(self, adapter):
        qs = adapter._build_query_string({})
        assert qs == ""

    def test_build_query_string_single_param(self, adapter):
        qs = adapter._build_query_string({"recvWindow": "5000"})
        assert qs == "recvWindow=5000"

    def test_build_query_string_url_encodes_special_chars(self, adapter):
        qs = adapter._build_query_string({"symbol": "BTC/USDT"})
        assert "/" not in qs
        assert "BTC" in qs

    def test_timestamp_ms_is_int(self, adapter):
        ts = adapter._timestamp_ms()
        assert isinstance(ts, int)

    def test_timestamp_ms_is_close_to_now(self, adapter):
        before = int(time.time() * 1000)
        ts = adapter._timestamp_ms()
        after = int(time.time() * 1000)
        assert before <= ts <= after + 1

    def test_timestamp_ms_increases_over_time(self, adapter):
        t1 = adapter._timestamp_ms()
        time.sleep(0.01)
        t2 = adapter._timestamp_ms()
        assert t2 >= t1


# ---------------------------------------------------------------------------
# OrderBook builder helper
# ---------------------------------------------------------------------------

class TestBuildOrderbook:
    def test_build_orderbook_basic(self, adapter):
        ob = adapter._build_orderbook(
            "BTC/USDT",
            [["50000", "1.0"], ["49999", "2.0"]],
            [["50001", "0.5"], ["50002", "1.0"]],
        )
        assert isinstance(ob, OrderBook)
        assert ob.symbol == "BTC/USDT"
        assert ob.exchange_id == "test_exchange"
        assert len(ob.bids) == 2
        assert len(ob.asks) == 2

    def test_build_orderbook_prices_are_decimal(self, adapter):
        ob = adapter._build_orderbook("ETH/USDT", [["3000.5", "0.1"]], [["3001.0", "0.2"]])
        assert isinstance(ob.bids[0].price, Decimal)
        assert isinstance(ob.asks[0].price, Decimal)
        assert ob.bids[0].price == Decimal("3000.5")
        assert ob.asks[0].price == Decimal("3001.0")

    def test_build_orderbook_amounts_are_decimal(self, adapter):
        ob = adapter._build_orderbook("BTC/USDT", [["50000", "1.23456789"]], [["50001", "0.5"]])
        assert isinstance(ob.bids[0].amount, Decimal)
        assert ob.bids[0].amount == Decimal("1.23456789")

    def test_build_orderbook_with_sequence(self, adapter):
        ob = adapter._build_orderbook(
            "BTC/USDT",
            [["50000", "1.0"]],
            [["50001", "0.5"]],
            sequence=12345,
        )
        assert ob.sequence == 12345

    def test_build_orderbook_without_sequence(self, adapter):
        ob = adapter._build_orderbook("BTC/USDT", [["50000", "1.0"]], [["50001", "0.5"]])
        assert ob.sequence is None

    def test_build_orderbook_empty_levels(self, adapter):
        ob = adapter._build_orderbook("BTC/USDT", [], [])
        assert ob.bids == []
        assert ob.asks == []

    def test_build_orderbook_numeric_prices(self, adapter):
        # Some exchanges return numbers instead of strings
        ob = adapter._build_orderbook("BTC/USDT", [[50000, 1.0]], [[50001, 0.5]])
        assert ob.bids[0].price == Decimal("50000")
        assert ob.asks[0].price == Decimal("50001")

    def test_build_orderbook_best_bid_ask(self, adapter):
        ob = adapter._build_orderbook(
            "BTC/USDT",
            [["50000", "1.0"], ["49999", "2.0"]],
            [["50001", "0.5"], ["50002", "1.0"]],
        )
        assert ob.best_bid == Decimal("50000")
        assert ob.best_ask == Decimal("50001")


# ---------------------------------------------------------------------------
# Trade builder helper
# ---------------------------------------------------------------------------

class TestBuildTrade:
    def _make_order(self) -> Order:
        return Order(
            exchange_id="test_exchange",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("50000"),
            amount=Decimal("0.001"),
        )

    def test_build_trade_basic(self, adapter):
        order = self._make_order()
        trade = adapter._build_trade(order, "t-001", Decimal("50000"), Decimal("0.001"))
        assert isinstance(trade, Trade)
        assert trade.trade_id == "t-001"
        assert trade.exchange_id == "test_exchange"
        assert trade.symbol == "BTC/USDT"
        assert trade.side == OrderSide.BUY
        assert trade.price == Decimal("50000")
        assert trade.amount == Decimal("0.001")

    def test_build_trade_default_fee_is_zero(self, adapter):
        order = self._make_order()
        trade = adapter._build_trade(order, "t-001", Decimal("50000"), Decimal("0.001"))
        assert trade.fee == Decimal("0")

    def test_build_trade_with_fee(self, adapter):
        order = self._make_order()
        trade = adapter._build_trade(
            order, "t-002", Decimal("50000"), Decimal("0.001"),
            fee=Decimal("0.05"), fee_currency="USDT",
        )
        assert trade.fee == Decimal("0.05")
        assert trade.fee_currency == "USDT"

    def test_build_trade_sell_side(self, adapter):
        order = Order(
            exchange_id="test_exchange",
            symbol="ETH/USDT",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            amount=Decimal("1.0"),
        )
        trade = adapter._build_trade(order, "t-003", Decimal("3000"), Decimal("1.0"))
        assert trade.side == OrderSide.SELL
        assert trade.symbol == "ETH/USDT"

    def test_build_trade_uses_client_order_id_if_no_order_id(self, adapter):
        order = Order(
            exchange_id="test_exchange",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            amount=Decimal("0.001"),
            client_order_id="client-abc",
        )
        trade = adapter._build_trade(order, "t-004", Decimal("50000"), Decimal("0.001"))
        assert trade.order_id == "client-abc"


# ---------------------------------------------------------------------------
# Connect / disconnect lifecycle
# ---------------------------------------------------------------------------

class TestConnectDisconnect:
    @pytest.mark.asyncio
    async def test_connect_creates_http_client(self, adapter):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            await adapter.connect()
            assert mock_client_cls.called
            assert adapter._connected is True
            assert adapter._http is mock_client

    @pytest.mark.asyncio
    async def test_connect_sets_connected_flag(self, adapter):
        assert adapter._connected is False
        with patch("httpx.AsyncClient"):
            await adapter.connect()
        assert adapter._connected is True

    @pytest.mark.asyncio
    async def test_connect_records_ws_connect_in_health(self, adapter):
        with patch("httpx.AsyncClient"):
            await adapter.connect()
        # After connect, health checker should record connected state
        assert adapter._health._metrics.is_connected is True

    @pytest.mark.asyncio
    async def test_disconnect_clears_connected_flag(self, adapter):
        mock_client = AsyncMock()
        adapter._http = mock_client
        adapter._connected = True
        await adapter.disconnect()
        assert adapter._connected is False

    @pytest.mark.asyncio
    async def test_disconnect_closes_http_client(self, adapter):
        mock_client = AsyncMock()
        adapter._http = mock_client
        adapter._connected = True
        await adapter.disconnect()
        mock_client.aclose.assert_called_once()
        assert adapter._http is None

    @pytest.mark.asyncio
    async def test_disconnect_cancels_ws_tasks(self, adapter):
        task1 = MagicMock(spec=asyncio.Task)
        task2 = MagicMock(spec=asyncio.Task)
        adapter._ws_tasks = {"orderbook:BTC/USDT": task1, "ticker:ETH/USDT": task2}
        mock_client = AsyncMock()
        adapter._http = mock_client
        await adapter.disconnect()
        task1.cancel.assert_called_once()
        task2.cancel.assert_called_once()
        assert adapter._ws_tasks == {}

    @pytest.mark.asyncio
    async def test_disconnect_records_ws_disconnect_in_health(self, adapter):
        mock_client = AsyncMock()
        adapter._http = mock_client
        adapter._connected = True
        # First simulate connected state
        adapter._health.record_ws_connect()
        await adapter.disconnect()
        assert adapter._health._metrics.is_connected is False

    @pytest.mark.asyncio
    async def test_health_score_after_connect(self, adapter):
        with patch("httpx.AsyncClient"):
            await adapter.connect()
        score = adapter.health_score
        assert 0.0 <= score <= 1.0
        # Should be > 0 since we just connected
        assert score > 0.0


# ---------------------------------------------------------------------------
# subscribe_orderbook with auto-reconnect
# ---------------------------------------------------------------------------

class TestSubscribeOrderbook:
    def _make_ws_message(self, bids=None, asks=None, symbol="BTC/USDT") -> str:
        return json.dumps({
            "bids": bids or [["50000", "1.0"]],
            "asks": asks or [["50001", "0.5"]],
            "symbol": symbol,
        })

    @pytest.mark.asyncio
    async def test_subscribe_creates_ws_task(self, adapter):
        received = []

        async def mock_ws_context(*args, **kwargs):
            return MagicMock()

        with patch("websockets.connect") as mock_connect:
            # Make the context manager return an async iterator that yields one message then stops
            async def fake_ws():
                yield self._make_ws_message()
                await asyncio.sleep(999)  # block to prevent tight reconnect loop

            mock_ws = MagicMock()
            mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
            mock_ws.__aexit__ = AsyncMock(return_value=False)
            mock_ws.__aiter__ = lambda self_: fake_ws()
            mock_ws.send = AsyncMock()
            mock_connect.return_value = mock_ws

            def callback(ob: OrderBook):
                received.append(ob)

            await adapter.subscribe_orderbook("BTC/USDT", callback)
            assert "orderbook:BTC/USDT" in adapter._ws_tasks
            task = adapter._ws_tasks["orderbook:BTC/USDT"]
            assert isinstance(task, asyncio.Task)
            # Cleanup
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass

    @pytest.mark.asyncio
    async def test_subscribe_idempotent(self, adapter):
        """Calling subscribe twice on same symbol creates only one task."""
        with patch("websockets.connect") as mock_connect:
            mock_ws = MagicMock()
            mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
            mock_ws.__aexit__ = AsyncMock(return_value=False)

            async def block_forever():
                await asyncio.sleep(999)  # block to prevent tight reconnect loop
                yield "{}"  # pragma: no cover

            mock_ws.__aiter__ = lambda s: block_forever()
            mock_ws.send = AsyncMock()
            mock_connect.return_value = mock_ws

            await adapter.subscribe_orderbook("BTC/USDT", lambda ob: None)
            task1 = adapter._ws_tasks.get("orderbook:BTC/USDT")

            await adapter.subscribe_orderbook("BTC/USDT", lambda ob: None)
            task2 = adapter._ws_tasks.get("orderbook:BTC/USDT")

            assert task1 is task2  # same task, no duplication
            task1.cancel()
            try:
                await asyncio.wait_for(task1, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass

    @pytest.mark.asyncio
    async def test_subscribe_sends_subscribe_message(self, adapter):
        """Adapter sends the subscription JSON message on connect."""
        sent_messages = []

        with patch("websockets.connect") as mock_connect:
            mock_ws = MagicMock()
            mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
            mock_ws.__aexit__ = AsyncMock(return_value=False)

            async def send_capture(msg):
                sent_messages.append(msg)

            mock_ws.send = AsyncMock(side_effect=send_capture)

            async def one_message_then_block():
                yield self._make_ws_message()
                await asyncio.sleep(999)  # block to prevent tight reconnect loop

            mock_ws.__aiter__ = lambda s: one_message_then_block()
            mock_connect.return_value = mock_ws

            await adapter.subscribe_orderbook("BTC/USDT", lambda ob: None)
            task = adapter._ws_tasks["orderbook:BTC/USDT"]
            # Allow the task to run briefly
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass

        assert any("SUBSCRIBE" in str(m) for m in sent_messages)

    @pytest.mark.asyncio
    async def test_subscribe_callback_receives_orderbook(self, adapter):
        """Callback is called with a valid OrderBook when WS message arrives."""
        received: list[OrderBook] = []

        with patch("websockets.connect") as mock_connect:
            mock_ws = MagicMock()
            mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
            mock_ws.__aexit__ = AsyncMock(return_value=False)
            mock_ws.send = AsyncMock()

            async def one_message_then_block():
                yield self._make_ws_message(bids=[["49000", "2.0"]], asks=[["49001", "1.0"]])
                await asyncio.sleep(999)  # block to prevent tight reconnect loop

            mock_ws.__aiter__ = lambda s: one_message_then_block()
            mock_connect.return_value = mock_ws

            await adapter.subscribe_orderbook("BTC/USDT", received.append)
            task = adapter._ws_tasks["orderbook:BTC/USDT"]
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass

        assert len(received) >= 1
        ob = received[0]
        assert isinstance(ob, OrderBook)
        assert ob.bids[0].price == Decimal("49000")
        assert ob.asks[0].price == Decimal("49001")

    @pytest.mark.asyncio
    async def test_subscribe_reconnects_on_ws_error(self, adapter):
        """WS loop reconnects after an error (uses asyncio.sleep between attempts)."""
        connect_count = 0
        second_connected = asyncio.Event()
        _real_sleep = asyncio.sleep

        async def fast_backoff(delay, *args, **kwargs):
            """Return instantly for short backoff delays, block for test generators."""
            if delay < 10:
                return  # fast-forward reconnect backoff
            await _real_sleep(delay, *args, **kwargs)

        with patch("websockets.connect") as mock_connect:
            with patch("asyncio.sleep", side_effect=fast_backoff) as mock_sleep:

                def make_ws(*args, **kwargs):
                    nonlocal connect_count
                    connect_count += 1
                    mock_ws = MagicMock()
                    mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
                    mock_ws.__aexit__ = AsyncMock(return_value=False)
                    mock_ws.send = AsyncMock()

                    if connect_count == 1:
                        async def raise_error():
                            raise ConnectionError("Simulated WS drop")
                            yield  # pragma: no cover

                        mock_ws.__aiter__ = lambda s: raise_error()
                    else:
                        # Second+ connection: signal and block (real sleep via fast_backoff)
                        async def signal_and_block():
                            second_connected.set()
                            await asyncio.sleep(999)  # routed to real sleep by fast_backoff
                            yield "{}"  # pragma: no cover

                        mock_ws.__aiter__ = lambda s: signal_and_block()

                    return mock_ws

                mock_connect.side_effect = make_ws
                await adapter.subscribe_orderbook("BTC/USDT", lambda ob: None)
                task = adapter._ws_tasks["orderbook:BTC/USDT"]

                # Wait until the second connection attempt proves reconnect happened
                await asyncio.wait_for(second_connected.wait(), timeout=3.0)

                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                    pass

        assert connect_count >= 2
        # asyncio.sleep was called at least once (backoff between reconnects)
        assert mock_sleep.called

    @pytest.mark.asyncio
    async def test_subscribe_cancelled_task_stops_cleanly(self, adapter):
        """Cancelling the WS task does not raise unhandled errors."""
        with patch("websockets.connect") as mock_connect:
            mock_ws = MagicMock()
            mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
            mock_ws.__aexit__ = AsyncMock(return_value=False)
            mock_ws.send = AsyncMock()

            # Simulate a stream that blocks for a while then yields
            started = asyncio.Event()

            async def blocking_stream():
                started.set()
                await asyncio.sleep(30)  # long wait — will be cancelled
                yield "{}"  # pragma: no cover

            mock_ws.__aiter__ = lambda s: blocking_stream()
            mock_connect.return_value = mock_ws

            await adapter.subscribe_orderbook("BTC/USDT", lambda ob: None)
            task = adapter._ws_tasks["orderbook:BTC/USDT"]

            # Wait until the stream has started
            await asyncio.wait_for(started.wait(), timeout=2.0)

            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass  # expected

    @pytest.mark.asyncio
    async def test_subscribe_skips_non_orderbook_messages(self, adapter):
        """Messages that don't parse to an OrderBook are silently ignored."""
        received: list[OrderBook] = []

        with patch("websockets.connect") as mock_connect:
            mock_ws = MagicMock()
            mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
            mock_ws.__aexit__ = AsyncMock(return_value=False)
            mock_ws.send = AsyncMock()

            async def mixed_messages():
                yield '{"type": "ping"}'                   # no bids → ignored
                yield '{"error": "unknown symbol"}'        # no bids → ignored
                yield self._make_ws_message()              # valid → received
                await asyncio.sleep(999)  # block to prevent tight reconnect loop

            mock_ws.__aiter__ = lambda s: mixed_messages()
            mock_connect.return_value = mock_ws

            await adapter.subscribe_orderbook("BTC/USDT", received.append)
            task = adapter._ws_tasks["orderbook:BTC/USDT"]
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass

        # Only the valid orderbook message should be received
        assert len(received) == 1


# ---------------------------------------------------------------------------
# estimate_slippage
# ---------------------------------------------------------------------------

class TestEstimateSlippage:
    @pytest.mark.asyncio
    async def test_returns_decimal(self, adapter):
        result = await adapter.estimate_slippage(OrderSide.BUY, Decimal("1.0"), "BTC/USDT")
        assert isinstance(result, Decimal)

    @pytest.mark.asyncio
    async def test_larger_size_returns_larger_slippage(self, adapter):
        small = await adapter.estimate_slippage(OrderSide.BUY, Decimal("1.0"), "BTC/USDT")
        large = await adapter.estimate_slippage(OrderSide.BUY, Decimal("100.0"), "BTC/USDT")
        assert large > small

    @pytest.mark.asyncio
    async def test_slippage_always_positive(self, adapter):
        result = await adapter.estimate_slippage(OrderSide.SELL, Decimal("0.01"), "ETH/USDT")
        assert result > Decimal("0")

    @pytest.mark.asyncio
    async def test_slippage_formula_correctness(self, adapter):
        # slippage = 0.0001 * 1.0 * size^0.5
        size = Decimal("4.0")
        expected = Decimal("0.0001") * Decimal("1.0") * (size ** Decimal("0.5"))
        result = await adapter.estimate_slippage(OrderSide.BUY, size, "BTC/USDT")
        assert result == expected


# ---------------------------------------------------------------------------
# NativeAdapter constructor and attributes
# ---------------------------------------------------------------------------

class TestNativeAdapterInit:
    def test_exchange_id_stored(self, adapter):
        assert adapter.exchange_id == "test_exchange"

    def test_api_key_stored(self, adapter):
        assert adapter._api_key == "test_key"

    def test_api_secret_stored(self, adapter):
        assert adapter._api_secret == "test_secret"

    def test_passphrase_stored(self, adapter):
        assert adapter._passphrase == "test_pass"

    def test_sandbox_flag(self, adapter):
        assert adapter._sandbox is False

    def test_sandbox_adapter(self):
        a = _TestAdapter("ex", sandbox=True)
        assert a._sandbox is True

    def test_initial_connected_false(self, adapter):
        assert adapter._connected is False

    def test_initial_http_none(self, adapter):
        assert adapter._http is None

    def test_initial_ws_tasks_empty(self, adapter):
        assert adapter._ws_tasks == {}

    def test_health_score_initial_range(self, adapter):
        score = adapter.health_score
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_default_rate_limiter_created(self, adapter):
        assert adapter._rate_limiter is not None

    def test_health_checker_created(self, adapter):
        assert adapter._health is not None
        assert adapter._health.exchange_id == "test_exchange"
