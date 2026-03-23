"""Tests for Leg Risk detection — US-331."""
import pytest
from decimal import Decimal
from src.execution.atomic import AtomicOrderExecutor


class TestLegRisk:
    def test_initial_count_zero(self):
        executor = AtomicOrderExecutor()
        assert executor.get_leg_risk_count() == 0

    def test_record_leg_risk_increments(self):
        executor = AtomicOrderExecutor()
        executor.record_leg_risk(symbol="BTC/USDT", buy_filled=True, sell_filled=False)
        assert executor.get_leg_risk_count() == 1
        executor.record_leg_risk(symbol="ETH/USDT", buy_filled=False, sell_filled=True)
        assert executor.get_leg_risk_count() == 2

    def test_both_filled_no_risk(self):
        executor = AtomicOrderExecutor()
        executor.record_leg_risk(symbol="BTC/USDT", buy_filled=True, sell_filled=True)
        assert executor.get_leg_risk_count() == 0

    def test_both_failed_no_risk(self):
        executor = AtomicOrderExecutor()
        executor.record_leg_risk(symbol="BTC/USDT", buy_filled=False, sell_filled=False)
        assert executor.get_leg_risk_count() == 0
