"""Unit tests for InventoryRebalancer wiring — US-120."""
import pytest
from src.core.inventory_rebalancer import InventoryRebalancer, TransferSuggestion
from src.core.balance_tracker import BalanceTracker


class TestRebalancerWiring:
    """Tests for rebalancer construction defaults and parameter wiring."""

    def test_rebalancer_creation_with_env_defaults(self):
        tracker = BalanceTracker()
        rebalancer = InventoryRebalancer(tracker=tracker)
        assert rebalancer.deviation_threshold == 0.30
        assert rebalancer.check_interval_s == 14400.0
        assert rebalancer.min_transfer_usd == 50.0

    def test_rebalancer_creation_with_custom_params(self):
        tracker = BalanceTracker()
        rebalancer = InventoryRebalancer(
            tracker=tracker,
            deviation_threshold=0.10,
            check_interval_s=3600,
            min_transfer_usd=100,
        )
        assert rebalancer.deviation_threshold == 0.10
        assert rebalancer.check_interval_s == 3600
        assert rebalancer.min_transfer_usd == 100

    def test_tracker_reference_is_stored(self):
        tracker = BalanceTracker()
        rebalancer = InventoryRebalancer(tracker=tracker)
        assert rebalancer.tracker is tracker

    def test_has_critical_imbalance_empty(self):
        tracker = BalanceTracker()
        rebalancer = InventoryRebalancer(tracker=tracker)
        assert rebalancer.has_critical_imbalance() is False

    def test_check_and_suggest_empty(self):
        tracker = BalanceTracker()
        rebalancer = InventoryRebalancer(tracker=tracker)
        assert rebalancer.check_and_suggest() == []

    def test_transfer_suggestion_format(self):
        s = TransferSuggestion(
            from_exchange="binance",
            to_exchange="upbit",
            amount_usd=500.0,
            reason="binance over by 40.0%, upbit under by 35.0%",
        )
        assert s.from_exchange == "binance"
        assert s.to_exchange == "upbit"
        assert s.amount_usd == 500.0
        assert "binance" in s.reason

    def test_target_weights_initially_empty(self):
        tracker = BalanceTracker()
        rebalancer = InventoryRebalancer(tracker=tracker)
        assert rebalancer._target_weights == {}
