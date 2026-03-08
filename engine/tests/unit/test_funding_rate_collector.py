"""Unit tests for FundingRateCollector and FundingRateStore."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.collectors.funding_rate_collector import (
    DEFAULT_EXCHANGES,
    DEFAULT_SYMBOLS,
    FundingRateCollector,
    FundingRateEntry,
    FundingRateStore,
)


# ---------------------------------------------------------------------------
# FundingRateStore tests
# ---------------------------------------------------------------------------


class TestFundingRateStore:
    def test_set_and_get_rate(self):
        store = FundingRateStore()
        store.set_rate("binance_futures", "BTC/USDT", 0.0001, 1710979200.0)
        entry = store.get_rate("binance_futures", "BTC/USDT")
        assert entry is not None
        assert entry.rate == pytest.approx(0.0001)
        assert entry.next_funding_time == pytest.approx(1710979200.0)

    def test_get_rate_missing_exchange(self):
        store = FundingRateStore()
        assert store.get_rate("nonexistent", "BTC/USDT") is None

    def test_get_rate_missing_symbol(self):
        store = FundingRateStore()
        store.set_rate("binance_futures", "BTC/USDT", 0.0001)
        assert store.get_rate("binance_futures", "ETH/USDT") is None

    def test_set_rate_updates_existing(self):
        store = FundingRateStore()
        store.set_rate("bybit", "BTC/USDT", 0.0001)
        store.set_rate("bybit", "BTC/USDT", 0.0002)
        entry = store.get_rate("bybit", "BTC/USDT")
        assert entry.rate == pytest.approx(0.0002)

    def test_get_all_rates(self):
        store = FundingRateStore()
        store.set_rate("binance_futures", "BTC/USDT", 0.0001)
        store.set_rate("bybit", "BTC/USDT", 0.0002)
        all_rates = store.get_all_rates()
        assert "binance_futures" in all_rates
        assert "bybit" in all_rates
        assert "BTC/USDT" in all_rates["binance_futures"]

    def test_get_rate_diff(self):
        store = FundingRateStore()
        store.set_rate("binance_futures", "BTC/USDT", 0.0003)
        store.set_rate("bybit", "BTC/USDT", 0.0001)
        diff = store.get_rate_diff("BTC/USDT", "binance_futures", "bybit")
        assert diff == pytest.approx(0.0002)

    def test_get_rate_diff_missing(self):
        store = FundingRateStore()
        store.set_rate("binance_futures", "BTC/USDT", 0.0001)
        diff = store.get_rate_diff("BTC/USDT", "binance_futures", "bybit")
        assert diff is None

    def test_entry_updated_at_set(self):
        store = FundingRateStore()
        before = time.time()
        store.set_rate("okx", "ETH/USDT", 0.00015)
        after = time.time()
        entry = store.get_rate("okx", "ETH/USDT")
        assert before <= entry.updated_at <= after


# ---------------------------------------------------------------------------
# Symbol format helpers
# ---------------------------------------------------------------------------


class TestSymbolFormatting:
    def test_to_linear_symbol(self):
        assert FundingRateCollector._to_linear_symbol("BTC/USDT") == "BTCUSDT"
        assert FundingRateCollector._to_linear_symbol("ETH/USDT") == "ETHUSDT"

    def test_to_okx_symbol(self):
        assert FundingRateCollector._to_okx_symbol("BTC/USDT") == "BTC-USDT-SWAP"
        assert FundingRateCollector._to_okx_symbol("ETH/USDT") == "ETH-USDT-SWAP"


# ---------------------------------------------------------------------------
# Per-exchange fetch parser tests (mock HTTP)
# ---------------------------------------------------------------------------


def _make_mock_response(status_code: int, json_data: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    return resp


@pytest.fixture
def collector():
    client = MagicMock()
    client.get = AsyncMock()
    return FundingRateCollector(
        symbols=["BTC/USDT"],
        exchanges=["binance_futures", "bybit", "okx", "bitget"],
        http_client=client,
    )


class TestFetchBinance:
    @pytest.mark.asyncio
    async def test_fetch_binance_success(self, collector):
        collector._http_client.get.return_value = _make_mock_response(
            200,
            {"lastFundingRate": "0.00010000", "nextFundingTime": 1710979200000},
        )
        entry = await collector._fetch_binance("BTC/USDT")
        assert entry is not None
        assert entry.rate == pytest.approx(0.0001)
        assert entry.next_funding_time == pytest.approx(1710979200.0)

    @pytest.mark.asyncio
    async def test_fetch_binance_non_200_returns_none(self, collector):
        collector._http_client.get.return_value = _make_mock_response(400, {})
        entry = await collector._fetch_binance("BTC/USDT")
        assert entry is None

    @pytest.mark.asyncio
    async def test_fetch_binance_missing_next_funding_time(self, collector):
        collector._http_client.get.return_value = _make_mock_response(
            200, {"lastFundingRate": "0.00005000"}
        )
        entry = await collector._fetch_binance("BTC/USDT")
        assert entry is not None
        assert entry.next_funding_time is None


class TestFetchBybit:
    @pytest.mark.asyncio
    async def test_fetch_bybit_success(self, collector):
        collector._http_client.get.return_value = _make_mock_response(
            200,
            {
                "result": {
                    "list": [{"fundingRate": "0.00012345", "nextFundingTime": "1710979200000"}]
                }
            },
        )
        entry = await collector._fetch_bybit("BTC/USDT")
        assert entry is not None
        assert entry.rate == pytest.approx(0.00012345)
        assert entry.next_funding_time == pytest.approx(1710979200.0)

    @pytest.mark.asyncio
    async def test_fetch_bybit_empty_list_returns_none(self, collector):
        collector._http_client.get.return_value = _make_mock_response(
            200, {"result": {"list": []}}
        )
        entry = await collector._fetch_bybit("BTC/USDT")
        assert entry is None


class TestFetchOKX:
    @pytest.mark.asyncio
    async def test_fetch_okx_success(self, collector):
        collector._http_client.get.return_value = _make_mock_response(
            200,
            {"data": [{"fundingRate": "0.00015000", "nextFundingTime": "1710979200000"}]},
        )
        entry = await collector._fetch_okx("BTC/USDT")
        assert entry is not None
        assert entry.rate == pytest.approx(0.00015)
        assert entry.next_funding_time == pytest.approx(1710979200.0)

    @pytest.mark.asyncio
    async def test_fetch_okx_empty_data_returns_none(self, collector):
        collector._http_client.get.return_value = _make_mock_response(200, {"data": []})
        entry = await collector._fetch_okx("BTC/USDT")
        assert entry is None


class TestFetchBitget:
    @pytest.mark.asyncio
    async def test_fetch_bitget_success(self, collector):
        collector._http_client.get.return_value = _make_mock_response(
            200,
            {"data": [{"fundingRate": "0.00008000"}]},
        )
        entry = await collector._fetch_bitget("BTC/USDT")
        assert entry is not None
        assert entry.rate == pytest.approx(0.00008)
        assert entry.next_funding_time is None

    @pytest.mark.asyncio
    async def test_fetch_bitget_empty_data_returns_none(self, collector):
        collector._http_client.get.return_value = _make_mock_response(200, {"data": []})
        entry = await collector._fetch_bitget("BTC/USDT")
        assert entry is None


# ---------------------------------------------------------------------------
# poll_once integration test
# ---------------------------------------------------------------------------


class TestPollOnce:
    @pytest.mark.asyncio
    async def test_poll_once_stores_results(self):
        client = MagicMock()

        async def mock_get(url, params=None):
            if "binance" in url:
                return _make_mock_response(
                    200, {"lastFundingRate": "0.00010000", "nextFundingTime": 1710979200000}
                )
            elif "bybit" in url:
                return _make_mock_response(
                    200,
                    {"result": {"list": [{"fundingRate": "0.00012345", "nextFundingTime": "1710979200000"}]}},
                )
            elif "okx" in url:
                return _make_mock_response(
                    200, {"data": [{"fundingRate": "0.00015000", "nextFundingTime": "1710979200000"}]}
                )
            elif "bitget" in url:
                return _make_mock_response(200, {"data": [{"fundingRate": "0.00008000"}]})
            return _make_mock_response(404, {})

        client.get = mock_get
        col = FundingRateCollector(
            symbols=["BTC/USDT"],
            exchanges=["binance_futures", "bybit", "okx", "bitget"],
            http_client=client,
        )
        fetched = await col.poll_once()

        assert "binance_futures" in fetched
        assert "bybit" in fetched
        assert "okx" in fetched
        assert "bitget" in fetched

        assert col.store.get_rate("binance_futures", "BTC/USDT").rate == pytest.approx(0.0001)
        assert col.store.get_rate("bybit", "BTC/USDT").rate == pytest.approx(0.00012345)
        assert col.store.get_rate("okx", "BTC/USDT").rate == pytest.approx(0.00015)
        assert col.store.get_rate("bitget", "BTC/USDT").rate == pytest.approx(0.00008)

    @pytest.mark.asyncio
    async def test_poll_once_ignores_unknown_exchange(self):
        client = MagicMock()
        client.get = AsyncMock(return_value=_make_mock_response(200, {}))
        col = FundingRateCollector(
            symbols=["BTC/USDT"],
            exchanges=["unknown_exchange"],
            http_client=client,
        )
        fetched = await col.poll_once()
        assert fetched == {}

    @pytest.mark.asyncio
    async def test_poll_once_handles_http_error_gracefully(self):
        client = MagicMock()
        client.get = AsyncMock(side_effect=Exception("network error"))
        col = FundingRateCollector(
            symbols=["BTC/USDT"],
            exchanges=["binance_futures"],
            http_client=client,
        )
        # Should not raise
        fetched = await col.poll_once()
        assert fetched == {}

    @pytest.mark.asyncio
    async def test_defaults(self):
        col = FundingRateCollector()
        assert col.symbols == DEFAULT_SYMBOLS
        assert col.exchanges == DEFAULT_EXCHANGES
        assert col.poll_interval == 60.0
