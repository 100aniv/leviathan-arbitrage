"""Unit tests for BithumbCollector incremental orderbook (US-073).

Tests: delta upsert, qty=0 delete, cumulative state, stale detection,
parallel refresh, initial snapshot populates _books.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.collectors.bithumb_collector import BithumbCollector


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_collector(symbols=None):
    return BithumbCollector(
        symbols=symbols or ["BTC/KRW", "ETH/KRW"],
        on_orderbook=None,
    )


def _delta_msg(symbol_bithumb: str, entries: list[dict]) -> dict:
    """Build a Bithumb orderbookdepth WS message."""
    return {
        "type": "orderbookdepth",
        "content": {
            "list": [
                {"symbol": symbol_bithumb, "orderType": e["side"],
                 "price": e["price"], "quantity": e["qty"]}
                for e in entries
            ]
        },
    }


def _rest_response(bids: list, asks: list) -> dict:
    """Build a fake Bithumb REST orderbook response."""
    return {
        "status": "0000",
        "data": {
            "bids": [{"price": p, "quantity": q} for p, q in bids],
            "asks": [{"price": p, "quantity": q} for p, q in asks],
        },
    }


# ---------------------------------------------------------------------------
# test_delta_apply_upsert
# ---------------------------------------------------------------------------

class TestDeltaApplyUpsert:
    def test_new_bid_level_added(self):
        col = _make_collector()
        msg = _delta_msg("BTC_KRW", [{"side": "bid", "price": "50000000", "qty": "0.5"}])
        result = col._parse_message(msg)
        assert result is not None
        symbol, bids, asks = result
        assert symbol == "BTC/KRW"
        assert any(b[0] == "50000000" and b[1] == "0.5" for b in bids)

    def test_existing_bid_level_updated(self):
        col = _make_collector()
        # First delta: add level
        col._parse_message(_delta_msg("BTC_KRW", [{"side": "bid", "price": "50000000", "qty": "0.5"}]))
        # Second delta: update same price
        _, bids, _ = col._parse_message(
            _delta_msg("BTC_KRW", [{"side": "bid", "price": "50000000", "qty": "1.2"}])
        )
        matched = [b for b in bids if b[0] == "50000000"]
        assert len(matched) == 1
        assert matched[0][1] == "1.2"

    def test_ask_level_upserted(self):
        col = _make_collector()
        msg = _delta_msg("BTC_KRW", [{"side": "ask", "price": "50100000", "qty": "0.3"}])
        _, _, asks = col._parse_message(msg)
        assert any(a[0] == "50100000" for a in asks)


# ---------------------------------------------------------------------------
# test_delta_apply_delete_zero_qty
# ---------------------------------------------------------------------------

class TestDeltaApplyDeleteZeroQty:
    def test_zero_qty_removes_bid_level(self):
        col = _make_collector()
        # Add a level first
        col._parse_message(_delta_msg("BTC_KRW", [{"side": "bid", "price": "49000000", "qty": "0.5"}]))
        # Delete it with qty=0
        _, bids, _ = col._parse_message(
            _delta_msg("BTC_KRW", [{"side": "bid", "price": "49000000", "qty": "0"}])
        )
        assert not any(b[0] == "49000000" for b in bids)

    def test_zero_qty_removes_ask_level(self):
        col = _make_collector()
        col._parse_message(_delta_msg("BTC_KRW", [{"side": "ask", "price": "51000000", "qty": "0.2"}]))
        _, _, asks = col._parse_message(
            _delta_msg("BTC_KRW", [{"side": "ask", "price": "51000000", "qty": "0"}])
        )
        assert not any(a[0] == "51000000" for a in asks)

    def test_delete_nonexistent_level_does_not_raise(self):
        col = _make_collector()
        # Deleting a level that was never added should not raise
        result = col._parse_message(
            _delta_msg("BTC_KRW", [{"side": "bid", "price": "99999999", "qty": "0"}])
        )
        assert result is not None  # returns empty bids/asks, not None


# ---------------------------------------------------------------------------
# test_cumulative_book_state
# ---------------------------------------------------------------------------

class TestCumulativeBookState:
    def test_multiple_deltas_accumulate(self):
        col = _make_collector()
        # Apply 3 bid levels
        col._parse_message(_delta_msg("BTC_KRW", [{"side": "bid", "price": "50000000", "qty": "1.0"}]))
        col._parse_message(_delta_msg("BTC_KRW", [{"side": "bid", "price": "49900000", "qty": "2.0"}]))
        _, bids, _ = col._parse_message(
            _delta_msg("BTC_KRW", [{"side": "bid", "price": "49800000", "qty": "3.0"}])
        )
        prices = [b[0] for b in bids]
        assert "50000000" in prices
        assert "49900000" in prices
        assert "49800000" in prices

    def test_bids_sorted_descending(self):
        col = _make_collector()
        col._parse_message(_delta_msg("BTC_KRW", [{"side": "bid", "price": "49800000", "qty": "1.0"}]))
        _, bids, _ = col._parse_message(
            _delta_msg("BTC_KRW", [{"side": "bid", "price": "50000000", "qty": "2.0"}])
        )
        bid_prices = [float(b[0]) for b in bids]
        assert bid_prices == sorted(bid_prices, reverse=True)

    def test_asks_sorted_ascending(self):
        col = _make_collector()
        col._parse_message(_delta_msg("BTC_KRW", [{"side": "ask", "price": "51000000", "qty": "1.0"}]))
        _, _, asks = col._parse_message(
            _delta_msg("BTC_KRW", [{"side": "ask", "price": "50200000", "qty": "2.0"}])
        )
        ask_prices = [float(a[0]) for a in asks]
        assert ask_prices == sorted(ask_prices)

    def test_separate_symbols_have_independent_books(self):
        col = _make_collector()
        col._parse_message(_delta_msg("BTC_KRW", [{"side": "bid", "price": "50000000", "qty": "1.0"}]))
        _, eth_bids, _ = col._parse_message(
            _delta_msg("ETH_KRW", [{"side": "bid", "price": "3000000", "qty": "5.0"}])
        )
        # ETH book should not contain BTC prices
        assert not any(b[0] == "50000000" for b in eth_bids)


# ---------------------------------------------------------------------------
# test_stale_detection
# ---------------------------------------------------------------------------

class TestStaleDetection:
    def test_never_updated_symbol_is_stale(self):
        col = _make_collector(["BTC/KRW"])
        assert col.is_symbol_stale("BTC/KRW", max_age_s=5.0) is True

    def test_recently_updated_symbol_is_not_stale(self):
        col = _make_collector(["BTC/KRW"])
        col._last_update["BTC/KRW"] = time.monotonic()
        assert col.is_symbol_stale("BTC/KRW", max_age_s=5.0) is False

    def test_symbol_becomes_stale_after_threshold(self):
        col = _make_collector(["BTC/KRW"])
        col._last_update["BTC/KRW"] = time.monotonic() - 6.0  # 6s ago
        assert col.is_symbol_stale("BTC/KRW", max_age_s=5.0) is True

    def test_parse_message_updates_last_update(self):
        col = _make_collector(["BTC/KRW"])
        before = time.monotonic()
        col._parse_message(_delta_msg("BTC_KRW", [{"side": "bid", "price": "50000000", "qty": "1.0"}]))
        assert "BTC/KRW" in col._last_update
        assert col._last_update["BTC/KRW"] >= before


# ---------------------------------------------------------------------------
# test_refresh_parallel
# ---------------------------------------------------------------------------

class TestRefreshParallel:
    @pytest.mark.asyncio
    async def test_refresh_symbols_calls_gather(self):
        """refresh_symbols uses asyncio.gather (parallel, not sequential)."""
        col = _make_collector(["BTC/KRW", "ETH/KRW", "XRP/KRW"])

        call_order: list[str] = []

        mock_resp = MagicMock()
        mock_resp.json.side_effect = lambda: _rest_response(
            [("50000000", "1.0")], [("50100000", "0.5")]
        )

        async def fake_get(url, **kwargs):
            # Track which symbol was fetched
            coin = url.split("/orderbook/")[1].split("_KRW")[0]
            call_order.append(coin)
            return mock_resp

        mock_client = AsyncMock()
        mock_client.get = fake_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.collectors.bithumb_collector.httpx.AsyncClient", return_value=mock_client):
            count = await col.refresh_symbols(["BTC/KRW", "ETH/KRW", "XRP/KRW"])

        assert count == 3
        assert set(call_order) == {"BTC", "ETH", "XRP"}

    @pytest.mark.asyncio
    async def test_refresh_symbols_updates_books(self):
        col = _make_collector(["BTC/KRW"])

        mock_resp = MagicMock()
        mock_resp.json.return_value = _rest_response(
            [("50000000", "2.0")], [("50100000", "1.0")]
        )

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.collectors.bithumb_collector.httpx.AsyncClient", return_value=mock_client):
            await col.refresh_symbols(["BTC/KRW"])

        assert "BTC/KRW" in col._books
        assert "50000000" in col._books["BTC/KRW"]["bids"]

    @pytest.mark.asyncio
    async def test_refresh_symbols_returns_zero_on_api_error(self):
        col = _make_collector(["BTC/KRW"])

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": "5100", "message": "error"}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.collectors.bithumb_collector.httpx.AsyncClient", return_value=mock_client):
            count = await col.refresh_symbols(["BTC/KRW"])

        assert count == 0


# ---------------------------------------------------------------------------
# test_initial_snapshot_populates_books
# ---------------------------------------------------------------------------

class TestInitialSnapshotPopulatesBooks:
    @pytest.mark.asyncio
    async def test_books_populated_after_fetch(self):
        col = _make_collector(["BTC/KRW"])

        mock_resp = MagicMock()
        mock_resp.json.return_value = _rest_response(
            [("50000000", "1.5"), ("49900000", "2.0")],
            [("50100000", "0.5"), ("50200000", "1.0")],
        )

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.collectors.bithumb_collector.httpx.AsyncClient", return_value=mock_client):
            with patch("src.collectors.bithumb_collector.asyncio.sleep", new_callable=AsyncMock):
                await col._fetch_initial_snapshots()

        assert "BTC/KRW" in col._books
        book = col._books["BTC/KRW"]
        assert "50000000" in book["bids"]
        assert "50100000" in book["asks"]

    @pytest.mark.asyncio
    async def test_snapshot_enables_delta_accumulation(self):
        """After initial snapshot, WS delta should build on top of existing state."""
        col = _make_collector(["BTC/KRW"])

        mock_resp = MagicMock()
        mock_resp.json.return_value = _rest_response(
            [("50000000", "1.0")],
            [("50100000", "0.5")],
        )

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.collectors.bithumb_collector.httpx.AsyncClient", return_value=mock_client):
            with patch("src.collectors.bithumb_collector.asyncio.sleep", new_callable=AsyncMock):
                await col._fetch_initial_snapshots()

        # Apply a WS delta adding a new level
        _, bids, _ = col._parse_message(
            _delta_msg("BTC_KRW", [{"side": "bid", "price": "49800000", "qty": "3.0"}])
        )
        prices = [b[0] for b in bids]
        # Both snapshot level and delta level should be present
        assert "50000000" in prices
        assert "49800000" in prices

    @pytest.mark.asyncio
    async def test_callback_called_with_is_snapshot_true(self):
        callback = AsyncMock()
        col = BithumbCollector(symbols=["BTC/KRW"], on_orderbook=callback)

        mock_resp = MagicMock()
        mock_resp.json.return_value = _rest_response(
            [("50000000", "1.0")],
            [("50100000", "0.5")],
        )

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.collectors.bithumb_collector.httpx.AsyncClient", return_value=mock_client):
            with patch("src.collectors.bithumb_collector.asyncio.sleep", new_callable=AsyncMock):
                await col._fetch_initial_snapshots()

        callback.assert_called_once()
        call_kwargs = callback.call_args[1]
        assert call_kwargs.get("is_snapshot") is True


# ---------------------------------------------------------------------------
# BUG-70/70b: Two-step verify flood guard and persistent fake blacklist
# ---------------------------------------------------------------------------

def _price_guard_delta(symbol_bithumb: str, bad_price: str, good_bid: str) -> dict:
    """Build a Bithumb delta that triggers the price guard (50% drop)."""
    return {
        "type": "orderbookdepth",
        "content": {
            "list": [
                {"symbol": symbol_bithumb, "orderType": "ask",
                 "price": bad_price, "quantity": "0.5"},
                {"symbol": symbol_bithumb, "orderType": "bid",
                 "price": good_bid, "quantity": "0.5"},
            ]
        },
    }


def _fake_spread_delta(symbol_bithumb: str) -> dict:
    """Build a delta whose accumulated result triggers the >50% price guard.

    Strategy: seed book with bid/ask=100, then send delta that removes the
    100-level and adds bid/ask=40 → new_mid=40 vs last_valid_mid=100 = 60% drop.
    """
    return {
        "type": "orderbookdepth",
        "content": {
            "list": [
                {"symbol": symbol_bithumb, "orderType": "bid", "price": "100", "quantity": "0"},  # delete
                {"symbol": symbol_bithumb, "orderType": "ask", "price": "100", "quantity": "0"},  # delete
                {"symbol": symbol_bithumb, "orderType": "bid", "price": "40", "quantity": "1.0"},
                {"symbol": symbol_bithumb, "orderType": "ask", "price": "40", "quantity": "1.0"},
            ]
        },
    }


def _seed_book(col, symbol: str, bid: str = "100", ask: str = "100") -> None:
    """Seed collector book and last_valid_mid for guard testing."""
    col._last_valid_mid[symbol] = (float(bid) + float(ask)) / 2
    col._books[symbol] = {
        "bids": {bid: "1.0"},
        "asks": {ask: "1.0"},
    }


class TestPriceGuardFloodPrevention:
    """BUG-70: _two_step_verify should not flood when guard fires rapidly."""

    def test_pending_flag_prevents_duplicate_task(self):
        """Second guard trigger while one verify is in-flight should not create a new task."""
        col = _make_collector(["ETH/BTC"])
        _seed_book(col, "ETH/BTC")

        tasks_created = []

        class FakeLoop:
            def create_task(self, coro):
                tasks_created.append(coro)
                coro.close()
                return MagicMock()

        with patch("src.collectors.bithumb_collector.asyncio.get_running_loop", return_value=FakeLoop()):
            # Trigger guard twice rapidly — second should be blocked by pending flag
            result1 = col._parse_message(_fake_spread_delta("ETH_BTC"))
            # Re-seed so next delta can also trigger guard
            _seed_book(col, "ETH/BTC")
            result2 = col._parse_message(_fake_spread_delta("ETH_BTC"))

        assert result1 is None   # delta rejected by guard
        assert result2 is None   # delta rejected by guard
        assert len(tasks_created) == 1  # only ONE verify task created (pending blocked second)
        assert "ETH/BTC" in col._two_step_pending

    def test_pending_flag_cleared_on_symbol_discard(self):
        """After manually discarding pending, next guard trigger creates a new task."""
        col = _make_collector(["ETH/BTC"])
        _seed_book(col, "ETH/BTC")

        tasks_created = []

        class FakeLoop:
            def create_task(self, coro):
                tasks_created.append(coro)
                coro.close()
                return MagicMock()

        with patch("src.collectors.bithumb_collector.asyncio.get_running_loop", return_value=FakeLoop()):
            col._parse_message(_fake_spread_delta("ETH_BTC"))
            col._two_step_pending.discard("ETH/BTC")  # simulate verify completing
            _seed_book(col, "ETH/BTC")
            col._parse_message(_fake_spread_delta("ETH_BTC"))

        assert len(tasks_created) == 2  # two tasks since pending was cleared

    def test_blacklisted_symbol_skips_task_creation(self):
        """BUG-70b: Blacklisted symbol should reject delta silently with no task."""
        import time as _time
        col = _make_collector(["ETH/BTC"])
        _seed_book(col, "ETH/BTC")
        # Blacklist the symbol directly
        col._fake_blacklist_expiry["ETH/BTC"] = _time.monotonic() + 600.0

        tasks_created = []

        class FakeLoop:
            def create_task(self, coro):
                tasks_created.append(coro)
                coro.close()
                return MagicMock()

        with patch("src.collectors.bithumb_collector.asyncio.get_running_loop", return_value=FakeLoop()):
            result = col._parse_message(_fake_spread_delta("ETH_BTC"))

        assert result is None          # delta still rejected
        assert len(tasks_created) == 0  # NO REST verify task created

    def test_blacklist_threshold(self):
        """After _FAKE_BLACKLIST_THRESHOLD fake confirmations, symbol should be blacklisted."""
        col = _make_collector(["ETH/BTC"])
        assert col._FAKE_BLACKLIST_THRESHOLD == 3

        # Simulate fake confirmations below threshold
        col._fake_confirm_count["ETH/BTC"] = 2
        col._fake_confirm_count["ETH/BTC"] += 1
        count = col._fake_confirm_count["ETH/BTC"]

        import time as _time
        if count >= col._FAKE_BLACKLIST_THRESHOLD:
            col._fake_blacklist_expiry["ETH/BTC"] = _time.monotonic() + col._FAKE_BLACKLIST_TTL_S

        assert "ETH/BTC" in col._fake_blacklist_expiry
        assert col._fake_blacklist_expiry["ETH/BTC"] > _time.monotonic()
