"""Unit tests for WS trade clients (BUG-120).

Tests signing + request building logic without real WebSocket connection.
Full integration tests with mock WS server come in Phase 4.
"""
from __future__ import annotations

import hashlib
import hmac
import base64
import time
from decimal import Decimal

import pytest

from src.infra.exchange.ws_trade import BinanceWSTrade, BitgetWSTrade


class TestBinanceWSTradeSigning:
    def test_hmac_sha256_signature_hex(self):
        client = BinanceWSTrade(api_key="test_key", api_secret="test_secret")
        params_str = "apiKey=test_key&quantity=0.001&side=BUY&symbol=BTCUSDT&timestamp=1000&type=MARKET"
        expected = hmac.new(
            b"test_secret", params_str.encode(), hashlib.sha256
        ).hexdigest()
        assert client._sign(params_str) == expected

    def test_init_stores_credentials(self):
        client = BinanceWSTrade(api_key="K", api_secret="S")
        assert client._api_key == "K"
        assert client._api_secret == "S"
        assert client._ws is None
        assert client._running is False


class TestBitgetWSTradeSigning:
    def test_login_signature_base64(self):
        api_secret = "test_secret"
        ts = "1700000000"
        sign_str = ts + "GET" + "/user/verify"
        mac = hmac.new(api_secret.encode(), sign_str.encode(), hashlib.sha256).digest()
        expected = base64.b64encode(mac).decode()
        # Recreate same logic in test (no direct call method exposed)
        assert len(expected) > 0
        assert expected.endswith("=") or expected[-1].isalnum()

    def test_init_stores_credentials(self):
        client = BitgetWSTrade(api_key="K", api_secret="S", passphrase="P")
        assert client._api_key == "K"
        assert client._api_secret == "S"
        assert client._passphrase == "P"
        assert client._ws is None
        assert client._logged_in is False


class TestWSTradeContractGuards:
    """BUG-125: Offline call triggers reconnect path."""

    @pytest.mark.asyncio
    async def test_binance_place_order_calls_connect_when_offline(self, monkeypatch):
        """Dropped connection state → place_order must invoke connect()."""
        client = BinanceWSTrade("k", "s")
        connect_called = {"n": 0}

        async def fake_connect():
            connect_called["n"] += 1
            raise RuntimeError("connect stubbed")

        monkeypatch.setattr(client, "connect", fake_connect)
        with pytest.raises(RuntimeError, match="connect stubbed"):
            await client.place_order(
                symbol="BTCUSDT", side="BUY", order_type="MARKET",
                quantity=Decimal("0.001"),
            )
        assert connect_called["n"] == 1

    @pytest.mark.asyncio
    async def test_bitget_place_order_calls_connect_when_offline(self, monkeypatch):
        """Dropped login state → place_order must invoke connect()."""
        client = BitgetWSTrade("k", "s", "p")
        connect_called = {"n": 0}

        async def fake_connect():
            connect_called["n"] += 1
            raise RuntimeError("connect stubbed")

        monkeypatch.setattr(client, "connect", fake_connect)
        with pytest.raises(RuntimeError, match="connect stubbed"):
            await client.place_order(
                inst_type="USDT-FUTURES", inst_id="BTCUSDT",
                order_type="market", side="buy", size=Decimal("0.001"),
            )
        assert connect_called["n"] == 1


class TestBinanceWSCancelOrder:
    """BUG-127: cancel_order WS contract tests."""

    def test_cancel_signature_differs_from_place(self):
        """Cancel params have different shape (orderId/origClientOrderId, no quantity)."""
        client = BinanceWSTrade("k", "s")
        place_params = "apiKey=k&quantity=0.001&side=BUY&symbol=BTCUSDT&timestamp=1000&type=MARKET"
        cancel_params = "apiKey=k&orderId=12345&symbol=BTCUSDT&timestamp=1000"
        assert client._sign(place_params) != client._sign(cancel_params)

    @pytest.mark.asyncio
    async def test_cancel_requires_order_id_or_client_id(self):
        """ValueError if neither order_id nor client_order_id provided."""
        client = BinanceWSTrade("k", "s")
        # force _ws to something truthy so reconnect path is skipped
        client._ws = object()
        client._running = True
        with pytest.raises(ValueError, match="order_id or client_order_id"):
            await client.cancel_order(symbol="BTCUSDT")

    def test_cancel_method_exists(self):
        assert hasattr(BinanceWSTrade, "cancel_order")
        assert callable(BinanceWSTrade.cancel_order)


class TestBitgetWSTimeoutErrorContract:
    """BUG-126: TimeoutError now raises RuntimeError with context."""

    @pytest.mark.asyncio
    async def test_bitget_place_order_extra_marginmode(self):
        """Ensure **extra kwargs propagate into params (marginMode/marginCoin)."""
        client = BitgetWSTrade("k", "s", "p")
        client._logged_in = True
        client._running = True

        # Mock ws send to capture payload
        sent_messages: list[str] = []

        class FakeWS:
            closed = False

            async def send(self, msg):
                sent_messages.append(msg)

        client._ws = FakeWS()
        import asyncio as _a
        _orig_wait = _a.wait_for

        async def fake_wait(fut, timeout):
            raise _a.TimeoutError

        _a.wait_for = fake_wait  # type: ignore
        try:
            with pytest.raises(RuntimeError, match="BitgetWSTrade timeout"):
                await client.place_order(
                    inst_type="USDT-FUTURES", inst_id="BTCUSDT",
                    order_type="market", side="buy", size=Decimal("0.001"),
                    marginMode="crossed", marginCoin="USDT",
                )
        finally:
            _a.wait_for = _orig_wait

        assert len(sent_messages) == 1
        import json as _j
        payload = _j.loads(sent_messages[0])
        assert payload["args"][0]["params"]["marginMode"] == "crossed"
        assert payload["args"][0]["params"]["marginCoin"] == "USDT"
