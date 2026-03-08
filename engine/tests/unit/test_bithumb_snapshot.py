"""Tests for Bithumb REST snapshot functionality."""
from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.collectors.bithumb_collector import (
    BithumbCollector,
    _coin_from_symbol,
    _normalize_symbol,
    _denormalize_symbol,
)


class TestCoinFromSymbol:
    def test_slash_format(self):
        assert _coin_from_symbol("BTC/KRW") == "BTC"

    def test_underscore_format(self):
        assert _coin_from_symbol("ETH_KRW") == "ETH"

    def test_complex_coin(self):
        assert _coin_from_symbol("DOGE/KRW") == "DOGE"


class TestBithumbSnapshotInit:
    def test_has_last_update_dict(self):
        c = BithumbCollector(symbols=["BTC/KRW"])
        assert c._last_update == {}
        assert c._snapshot_fetched is False


class TestBithumbRestSnapshot:
    @pytest.mark.asyncio
    async def test_fetch_initial_snapshots_success(self):
        """REST snapshot fetches and delivers orderbook via callback."""
        callback = AsyncMock()
        c = BithumbCollector(symbols=["BTC/KRW"], on_orderbook=callback)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "status": "0000",
            "data": {
                "bids": [
                    {"price": "89990000", "quantity": "0.1"},
                    {"price": "89980000", "quantity": "0.2"},
                ],
                "asks": [
                    {"price": "90010000", "quantity": "0.15"},
                    {"price": "90020000", "quantity": "0.25"},
                ],
            },
        }

        with patch("src.collectors.bithumb_collector.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await c._fetch_initial_snapshots()

        assert c._snapshot_fetched is True
        assert callback.call_count == 1
        call_args = callback.call_args
        assert call_args[0][0] == "bithumb"
        assert call_args[0][1] == "BTC/KRW"
        assert len(call_args[0][2]) == 2  # bids
        assert len(call_args[0][3]) == 2  # asks

    @pytest.mark.asyncio
    async def test_fetch_initial_snapshots_bad_status(self):
        """REST snapshot skips symbol on non-0000 status."""
        callback = AsyncMock()
        c = BithumbCollector(symbols=["NOM/KRW"], on_orderbook=callback)

        mock_response = MagicMock()
        mock_response.json.return_value = {"status": "5100", "message": "Bad Request"}

        with patch("src.collectors.bithumb_collector.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await c._fetch_initial_snapshots()

        assert c._snapshot_fetched is True
        assert callback.call_count == 0  # Skipped due to bad status

    @pytest.mark.asyncio
    async def test_fetch_initial_snapshots_price_insanity(self):
        """REST snapshot filters out symbols with insane bid/ask ratio."""
        callback = AsyncMock()
        c = BithumbCollector(symbols=["SXP/KRW"], on_orderbook=callback)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "status": "0000",
            "data": {
                "bids": [{"price": "500000", "quantity": "100"}],  # 500K
                "asks": [{"price": "10", "quantity": "100"}],       # 10 — 50000x spread
            },
        }

        with patch("src.collectors.bithumb_collector.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await c._fetch_initial_snapshots()

        assert c._snapshot_fetched is True
        assert callback.call_count == 0  # Filtered out due to insane prices

    @pytest.mark.asyncio
    async def test_fetch_initial_snapshots_empty_book(self):
        """REST snapshot skips symbols with empty bids/asks."""
        callback = AsyncMock()
        c = BithumbCollector(symbols=["TINY/KRW"], on_orderbook=callback)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "status": "0000",
            "data": {"bids": [], "asks": []},
        }

        with patch("src.collectors.bithumb_collector.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await c._fetch_initial_snapshots()

        assert callback.call_count == 0

    @pytest.mark.asyncio
    async def test_fetch_snapshots_not_repeated(self):
        """REST snapshot only fetches once."""
        c = BithumbCollector(symbols=["BTC/KRW"])
        c._snapshot_fetched = True

        with patch("src.collectors.bithumb_collector.httpx.AsyncClient") as mock_client_cls:
            await c._fetch_initial_snapshots()
            mock_client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_snapshots_network_error(self):
        """REST snapshot handles network errors gracefully."""
        callback = AsyncMock()
        c = BithumbCollector(symbols=["BTC/KRW"], on_orderbook=callback)

        with patch("src.collectors.bithumb_collector.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.ConnectError("Connection refused")
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            # Should not raise
            await c._fetch_initial_snapshots()

        assert c._snapshot_fetched is True
        assert callback.call_count == 0


class TestBithumbStaleness:
    def test_symbol_stale_no_data(self):
        c = BithumbCollector(symbols=["BTC/KRW"])
        assert c.is_symbol_stale("BTC/KRW") is True

    def test_symbol_not_stale(self):
        c = BithumbCollector(symbols=["BTC/KRW"])
        c._last_update["BTC/KRW"] = time.monotonic()
        assert c.is_symbol_stale("BTC/KRW") is False

    def test_symbol_stale_old_data(self):
        c = BithumbCollector(symbols=["BTC/KRW"])
        c._last_update["BTC/KRW"] = time.monotonic() - 600  # 10 minutes ago
        assert c.is_symbol_stale("BTC/KRW", max_age_s=300) is True

    def test_parse_updates_last_update(self):
        c = BithumbCollector(symbols=["BTC/KRW"])
        data = {
            "type": "orderbookdepth",
            "content": {
                "list": [
                    {"symbol": "BTC_KRW", "orderType": "bid", "price": "89990000", "quantity": "0.1"},
                    {"symbol": "BTC_KRW", "orderType": "ask", "price": "90010000", "quantity": "0.15"},
                ]
            }
        }
        result = c._parse_message(data)
        assert result is not None
        assert "BTC/KRW" in c._last_update


class TestBithumbMultiSymbolSnapshot:
    @pytest.mark.asyncio
    async def test_multiple_symbols_rate_limited(self):
        """Fetching multiple symbols should include rate-limit delays."""
        callback = AsyncMock()
        c = BithumbCollector(symbols=["BTC/KRW", "ETH/KRW"], on_orderbook=callback)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "status": "0000",
            "data": {
                "bids": [{"price": "1000", "quantity": "1"}],
                "asks": [{"price": "1001", "quantity": "1"}],
            },
        }

        with patch("src.collectors.bithumb_collector.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("src.collectors.bithumb_collector.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                await c._fetch_initial_snapshots()

            assert callback.call_count == 2
            assert mock_sleep.call_count == 2  # One sleep per symbol
