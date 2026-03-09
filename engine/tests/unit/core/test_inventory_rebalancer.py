"""Tests for InventoryRebalancer (US-050)."""
from __future__ import annotations

import pytest

from src.core.balance_tracker import BalanceTracker
from src.core.inventory_rebalancer import InventoryRebalancer, TransferSuggestion


def _setup_tracker(*balances: tuple[str, float]) -> BalanceTracker:
    """Create tracker with given (exchange_id, available_usd) pairs."""
    bt = BalanceTracker()
    for eid, amount in balances:
        bt.record_balance(eid, amount, amount)
    return bt


class TestComputeDeviations:
    def test_equal_balances_zero_deviation(self):
        """Equal balances across exchanges → ~0 deviation."""
        bt = _setup_tracker(("binance", 500), ("upbit", 500))
        rb = InventoryRebalancer(bt)
        devs = rb.compute_deviations()
        assert abs(devs["binance"]) < 0.01
        assert abs(devs["upbit"]) < 0.01

    def test_unequal_balances_show_deviation(self):
        """Unequal balances → non-zero deviations."""
        bt = _setup_tracker(("binance", 900), ("upbit", 100))
        rb = InventoryRebalancer(bt)
        devs = rb.compute_deviations()
        assert devs["binance"] > 0.3  # over-allocated
        assert devs["upbit"] < -0.3  # under-allocated

    def test_custom_target_weights(self):
        """Custom weights change deviation calculation."""
        bt = _setup_tracker(("binance", 700), ("upbit", 300))
        rb = InventoryRebalancer(bt)
        rb.set_target_weights({"binance": 0.70, "upbit": 0.30})
        devs = rb.compute_deviations()
        assert abs(devs["binance"]) < 0.01  # matches target
        assert abs(devs["upbit"]) < 0.01

    def test_empty_tracker_returns_empty(self):
        bt = BalanceTracker()
        rb = InventoryRebalancer(bt)
        assert rb.compute_deviations() == {}

    def test_zero_total_returns_empty(self):
        bt = _setup_tracker(("binance", 0), ("upbit", 0))
        rb = InventoryRebalancer(bt)
        assert rb.compute_deviations() == {}


class TestCheckAndSuggest:
    def test_no_suggestions_when_balanced(self):
        bt = _setup_tracker(("binance", 500), ("upbit", 500))
        rb = InventoryRebalancer(bt)
        assert rb.check_and_suggest() == []

    def test_suggests_transfer_when_deviation_exceeds_threshold(self):
        bt = _setup_tracker(("binance", 900), ("upbit", 100))
        rb = InventoryRebalancer(bt, deviation_threshold=0.30)
        suggestions = rb.check_and_suggest()
        assert len(suggestions) >= 1
        s = suggestions[0]
        assert s.from_exchange == "binance"
        assert s.to_exchange == "upbit"
        assert s.amount_usd > 0

    def test_no_suggestion_below_min_transfer(self):
        bt = _setup_tracker(("binance", 60), ("upbit", 40))
        rb = InventoryRebalancer(bt, deviation_threshold=0.05, min_transfer_usd=100)
        suggestions = rb.check_and_suggest()
        assert len(suggestions) == 0

    def test_three_exchanges_rebalance(self):
        bt = _setup_tracker(("a", 800), ("b", 100), ("c", 100))
        rb = InventoryRebalancer(bt, deviation_threshold=0.20)
        suggestions = rb.check_and_suggest()
        assert len(suggestions) >= 1
        assert all(s.from_exchange == "a" for s in suggestions)


class TestCriticalImbalance:
    def test_critical_when_deviation_exceeds_2x_threshold(self):
        bt = _setup_tracker(("binance", 950), ("upbit", 50))
        rb = InventoryRebalancer(bt, deviation_threshold=0.20)
        assert rb.has_critical_imbalance() is True

    def test_not_critical_when_balanced(self):
        bt = _setup_tracker(("binance", 500), ("upbit", 500))
        rb = InventoryRebalancer(bt, deviation_threshold=0.30)
        assert rb.has_critical_imbalance() is False


class TestSetTargetWeights:
    def test_weights_normalized_to_sum_1(self):
        bt = _setup_tracker(("a", 500), ("b", 500))
        rb = InventoryRebalancer(bt)
        rb.set_target_weights({"a": 2, "b": 8})
        assert abs(rb._target_weights["a"] - 0.2) < 1e-10
        assert abs(rb._target_weights["b"] - 0.8) < 1e-10
