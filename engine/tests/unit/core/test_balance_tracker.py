"""Tests for BalanceTracker (US-050)."""
from __future__ import annotations

import pytest

from src.core.balance_tracker import BalanceTracker


class TestBalanceTrackerRecord:
    def test_record_stores_snapshot(self):
        bt = BalanceTracker()
        snap = bt.record_balance("binance", total_usd=1000, available_usd=800, locked_usd=200)
        assert snap.exchange_id == "binance"
        assert snap.total_usd == 1000
        assert snap.available_usd == 800

    def test_get_latest_returns_most_recent(self):
        bt = BalanceTracker()
        bt.record_balance("binance", 1000, 800)
        bt.record_balance("binance", 1100, 900)
        snap = bt.get_latest("binance")
        assert snap is not None
        assert snap.available_usd == 900

    def test_history_respects_max_size(self):
        bt = BalanceTracker(history_max=5)
        for i in range(10):
            bt.record_balance("binance", float(i * 100), float(i * 100))
        assert len(bt.get_history("binance")) == 5

    def test_get_latest_unknown_returns_none(self):
        bt = BalanceTracker()
        assert bt.get_latest("unknown") is None

    def test_assets_dict_stored(self):
        bt = BalanceTracker()
        snap = bt.record_balance("upbit", 500, 500, assets={"BTC": 0.01, "ETH": 0.5})
        assert snap.assets["BTC"] == 0.01


class TestBalanceTrackerThreshold:
    def test_below_threshold_detected(self):
        bt = BalanceTracker(min_balance_usd=200)
        bt.record_balance("binance", 150, 150)
        assert bt.is_below_threshold("binance") is True

    def test_above_threshold_ok(self):
        bt = BalanceTracker(min_balance_usd=200)
        bt.record_balance("binance", 500, 500)
        assert bt.is_below_threshold("binance") is False

    def test_get_low_balance_exchanges(self):
        bt = BalanceTracker(min_balance_usd=200)
        bt.record_balance("binance", 500, 500)
        bt.record_balance("upbit", 50, 50)
        bt.record_balance("bithumb", 100, 100)
        low = bt.get_low_balance_exchanges()
        assert "upbit" in low
        assert "bithumb" in low
        assert "binance" not in low


class TestBalanceTrackerSizeScale:
    def test_full_balance_returns_1(self):
        bt = BalanceTracker()
        bt.record_balance("binance", 1000, 1000)
        assert bt.compute_size_scale("binance", 500) == 1.0

    def test_partial_balance_returns_fraction(self):
        bt = BalanceTracker()
        bt.record_balance("binance", 200, 200)
        scale = bt.compute_size_scale("binance", 500)
        assert abs(scale - 0.4) < 1e-10

    def test_zero_balance_returns_zero(self):
        bt = BalanceTracker()
        bt.record_balance("binance", 0, 0)
        assert bt.compute_size_scale("binance", 500) == 0.0

    def test_unknown_exchange_returns_zero(self):
        bt = BalanceTracker()
        assert bt.compute_size_scale("unknown", 500) == 0.0

    def test_zero_target_returns_zero(self):
        bt = BalanceTracker()
        bt.record_balance("binance", 1000, 1000)
        assert bt.compute_size_scale("binance", 0) == 0.0


class TestBalanceTrackerTotals:
    def test_total_balance_sums_all(self):
        bt = BalanceTracker()
        bt.record_balance("binance", 1000, 800)
        bt.record_balance("upbit", 500, 400)
        assert bt.get_total_balance() == 1200.0

    def test_get_all_exchanges(self):
        bt = BalanceTracker()
        bt.record_balance("binance", 1000, 1000)
        bt.record_balance("upbit", 500, 500)
        assert set(bt.get_all_exchanges()) == {"binance", "upbit"}
