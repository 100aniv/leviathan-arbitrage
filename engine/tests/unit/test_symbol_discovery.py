"""Tests for symbol auto-discovery and TRADING_SYMBOLS=auto config integration."""
from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import AsyncMock, patch

import pytest

from src.collectors.symbol_discovery import discover_common_symbols


def _make_fetcher_mocks(data: dict[str, set[str]]):
    """Create mock _EXCHANGE_FETCHERS that return fixed data without HTTP calls."""
    async def _mock_fetcher(bases: set[str]):
        async def fetcher(client):
            return bases
        return fetcher

    mocks = {}
    for ex, bases in data.items():
        async def make(b=bases):
            return b
        mocks[ex] = make
    return mocks


class TestDiscoverCommonSymbols:
    """Tests for discover_common_symbols()."""

    @pytest.fixture
    def mock_data(self):
        return {
            "binance": {"BTC", "ETH", "XRP", "SOL", "DOGE"},
            "upbit": {"BTC", "ETH", "XRP", "ADA"},
            "bithumb": {"BTC", "ETH", "XRP", "SOL", "ADA"},
        }

    def _patch_fetchers(self, data: dict[str, set[str]]):
        """Patch _EXCHANGE_FETCHERS dict with coroutine functions returning fixed sets."""
        async def _make(bases):
            async def fetcher(client):
                return bases
            return fetcher

        fetchers = {}
        for ex, bases in data.items():
            async def f(client, b=bases):
                return b
            fetchers[ex] = f
        return patch.dict("src.collectors.symbol_discovery._EXCHANGE_FETCHERS", fetchers)

    @pytest.mark.asyncio
    async def test_discovers_common_symbols_min3(self, mock_data):
        """Symbols on >= 3 exchanges are discovered."""
        with self._patch_fetchers(mock_data):
            result = await discover_common_symbols(min_exchanges=3)

        assert "BTC/USDT" in result
        assert "ETH/USDT" in result
        assert "XRP/USDT" in result
        assert "SOL/USDT" not in result  # only 2 exchanges
        assert "DOGE/USDT" not in result  # only 1

    @pytest.mark.asyncio
    async def test_discovers_common_symbols_min2(self, mock_data):
        """Lower min_exchanges includes more symbols."""
        with self._patch_fetchers(mock_data):
            result = await discover_common_symbols(min_exchanges=2)

        assert "SOL/USDT" in result  # binance + bithumb
        assert "ADA/USDT" in result  # upbit + bithumb
        assert "DOGE/USDT" not in result  # only binance

    @pytest.mark.asyncio
    async def test_excludes_stablecoins(self):
        """Stablecoins are excluded from discovery."""
        data = {
            "binance": {"BTC", "USDC", "USDT", "BUSD"},
            "upbit": {"BTC", "USDC", "USDT"},
            "bithumb": {"BTC", "USDC", "USDT", "BUSD"},
        }
        with self._patch_fetchers(data):
            result = await discover_common_symbols(min_exchanges=2)

        assert "BTC/USDT" in result
        assert "USDC/USDT" not in result
        assert "USDT/USDT" not in result
        assert "BUSD/USDT" not in result

    @pytest.mark.asyncio
    async def test_returns_sorted(self, mock_data):
        """Results are sorted alphabetically."""
        with self._patch_fetchers(mock_data):
            result = await discover_common_symbols(min_exchanges=2)

        assert result == sorted(result)

    @pytest.mark.asyncio
    async def test_all_failures_returns_empty(self):
        """Returns empty list when all exchanges fail (Engine handles fallback)."""
        async def fail(client):
            raise Exception("API down")

        fetchers = {"binance": fail, "upbit": fail, "bithumb": fail}
        with patch.dict("src.collectors.symbol_discovery._EXCHANGE_FETCHERS", fetchers):
            result = await discover_common_symbols(min_exchanges=2)

        assert result == []

    @pytest.mark.asyncio
    async def test_partial_failure_still_discovers(self):
        """Works with partial exchange failures."""
        async def fail(client):
            raise Exception("timeout")

        async def binance(client):
            return {"BTC", "ETH", "SOL"}

        async def bithumb(client):
            return {"BTC", "ETH", "XRP"}

        fetchers = {"binance": binance, "upbit": fail, "bithumb": bithumb}
        with patch.dict("src.collectors.symbol_discovery._EXCHANGE_FETCHERS", fetchers):
            result = await discover_common_symbols(min_exchanges=2)

        assert "BTC/USDT" in result
        assert "ETH/USDT" in result
        assert "SOL/USDT" not in result  # only on binance


class TestTradingSymbolsConfig:
    """Tests for TRADING_SYMBOLS env var parsing in config."""

    def test_auto_sentinel_parsed(self):
        """TRADING_SYMBOLS=["auto"] is parsed as sentinel list."""
        with patch.dict(os.environ, {"TRADING_SYMBOLS": '["auto"]'}):
            from src.core.config import TradingSettings
            t = TradingSettings()
            assert t.symbols == ["auto"]

    def test_json_array_parsed(self):
        """TRADING_SYMBOLS with JSON array is parsed correctly."""
        with patch.dict(os.environ, {"TRADING_SYMBOLS": '["BTC/USDT","ETH/USDT"]'}):
            from src.core.config import TradingSettings
            t = TradingSettings()
            assert t.symbols == ["BTC/USDT", "ETH/USDT"]

    def test_min_exchanges_default(self):
        """TRADING_SYMBOL_MIN_EXCHANGES defaults to 3."""
        from src.core.config import TradingSettings
        t = TradingSettings()
        assert t.symbol_min_exchanges == 3

    def test_min_exchanges_override(self):
        """TRADING_SYMBOL_MIN_EXCHANGES can be overridden."""
        with patch.dict(os.environ, {"TRADING_SYMBOL_MIN_EXCHANGES": "2"}):
            from src.core.config import TradingSettings
            t = TradingSettings()
            assert t.symbol_min_exchanges == 2


class TestEngineResolveSymbols:
    """Tests for Engine._resolve_symbols() auto-discovery integration."""

    @pytest.mark.asyncio
    async def test_resolve_auto_calls_discovery(self):
        """When symbols is ['auto'], _resolve_symbols calls discover_common_symbols."""
        from src.main import Engine

        engine = Engine()
        engine._settings = type("S", (), {
            "trading": type("T", (), {
                "symbols": ["auto"],
                "symbol_min_exchanges": 3,
            })(),
        })()

        mock_symbols = ["BTC/USDT", "ETH/USDT", "XRP/USDT", "SOL/USDT"]
        with patch("src.collectors.symbol_discovery.discover_common_symbols", new_callable=AsyncMock, return_value=mock_symbols) as mock_disc:
            await engine._resolve_symbols()

            mock_disc.assert_called_once_with(min_exchanges=3)
            assert engine._settings.trading.symbols == mock_symbols

    @pytest.mark.asyncio
    async def test_resolve_manual_skips_discovery(self):
        """When symbols is explicit list, _resolve_symbols does nothing."""
        from src.main import Engine

        engine = Engine()
        engine._settings = type("S", (), {
            "trading": type("T", (), {
                "symbols": ["BTC/USDT", "ETH/USDT"],
                "symbol_min_exchanges": 3,
            })(),
        })()

        with patch("src.collectors.symbol_discovery.discover_common_symbols", new_callable=AsyncMock) as mock_disc:
            await engine._resolve_symbols()

            mock_disc.assert_not_called()
            assert engine._settings.trading.symbols == ["BTC/USDT", "ETH/USDT"]

    @pytest.mark.asyncio
    async def test_resolve_fallback_on_failure(self):
        """On discovery failure, falls back to 3 default symbols."""
        from src.main import Engine

        engine = Engine()
        engine._settings = type("S", (), {
            "trading": type("T", (), {
                "symbols": ["auto"],
                "symbol_min_exchanges": 3,
            })(),
        })()

        with patch("src.collectors.symbol_discovery.discover_common_symbols", new_callable=AsyncMock, side_effect=Exception("network")):
            await engine._resolve_symbols()

            assert engine._settings.trading.symbols == ["BTC/USDT", "ETH/USDT", "XRP/USDT"]

    @pytest.mark.asyncio
    async def test_resolve_fallback_on_empty(self):
        """On empty discovery result, falls back to 3 default symbols."""
        from src.main import Engine

        engine = Engine()
        engine._settings = type("S", (), {
            "trading": type("T", (), {
                "symbols": ["auto"],
                "symbol_min_exchanges": 3,
            })(),
        })()

        with patch("src.collectors.symbol_discovery.discover_common_symbols", new_callable=AsyncMock, return_value=[]):
            await engine._resolve_symbols()

            assert engine._settings.trading.symbols == ["BTC/USDT", "ETH/USDT", "XRP/USDT"]
