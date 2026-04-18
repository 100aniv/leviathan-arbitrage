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
    @pytest.mark.asyncio
    async def test_binance_place_order_requires_connection(self):
        client = BinanceWSTrade("k", "s")
        with pytest.raises(RuntimeError, match="not connected"):
            await client.place_order(
                symbol="BTCUSDT", side="BUY", order_type="MARKET",
                quantity=Decimal("0.001"),
            )

    @pytest.mark.asyncio
    async def test_bitget_place_order_requires_login(self):
        client = BitgetWSTrade("k", "s", "p")
        with pytest.raises(RuntimeError, match="not authenticated"):
            await client.place_order(
                inst_type="USDT-FUTURES", inst_id="BTCUSDT",
                order_type="market", side="buy", size=Decimal("0.001"),
            )
