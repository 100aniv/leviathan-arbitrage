"""Tests for HealthChecker."""
from __future__ import annotations

import pytest

from src.infra.exchange.health_checker import HealthChecker


class TestHealthChecker:
    def test_initial_score_in_range(self):
        checker = HealthChecker("test_exchange")
        score = checker.health_score
        assert 0.0 <= score <= 1.0

    def test_connected_raises_score(self):
        checker = HealthChecker("test_exchange")
        checker.record_ws_connect()
        score = checker.health_score
        assert score > 0.0

    def test_disconnected_lowers_score(self):
        checker = HealthChecker("test_exchange")
        checker.record_ws_connect()
        connected_score = checker.health_score
        checker.record_ws_disconnect()
        disconnected_score = checker.health_score
        assert disconnected_score < connected_score

    def test_low_latency_yields_higher_score_than_high_latency(self):
        low_lat = HealthChecker("test_exchange")
        low_lat.record_ws_connect()
        for _ in range(10):
            low_lat.record_api_latency(10.0)  # 10ms

        high_lat = HealthChecker("test_exchange")
        high_lat.record_ws_connect()
        for _ in range(10):
            high_lat.record_api_latency(1000.0)  # 1000ms

        assert low_lat.health_score > high_lat.health_score

    def test_score_always_in_0_1_range(self):
        checker = HealthChecker("test_exchange")
        checker.record_ws_connect()
        checker.record_api_latency(50.0)
        checker.record_order_fill(True)
        checker.record_order_fill(False)
        score = checker.health_score
        assert 0.0 <= score <= 1.0

    def test_perfect_conditions_yield_high_score(self):
        checker = HealthChecker("test_exchange")
        checker.record_ws_connect()
        for _ in range(20):
            checker.record_api_latency(5.0)  # very fast
            checker.record_order_fill(True)
        score = checker.health_score
        assert score > 0.9

    def test_disconnected_score_is_low(self):
        # PHOENIX Phase 2: connection_score uses staleness, not is_connected flag.
        # Stale data (last_heartbeat > stale_threshold ago) drives score low.
        import time
        checker = HealthChecker("test_exchange")
        checker._metrics.last_heartbeat = time.monotonic() - 200  # 200s stale
        checker.record_ws_disconnect()
        score = checker.health_score
        # Stale data + disconnect: connection_score=0, ws_score degraded → score < 0.5
        assert score < 0.5

    def test_many_disconnects_reduce_ws_stability(self):
        checker = HealthChecker("test_exchange")
        checker.record_ws_connect()
        baseline = checker.health_score
        for _ in range(10):
            checker.record_ws_disconnect()
        degraded = checker.health_score
        assert degraded <= baseline

    def test_reset_restores_initial_state(self):
        checker = HealthChecker("test_exchange")
        checker.record_ws_connect()
        for _ in range(10):
            checker.record_api_latency(10.0)
        checker.reset()

        fresh = HealthChecker("test_exchange")
        assert checker.health_score == fresh.health_score

    def test_order_fill_tracking(self):
        checker = HealthChecker("test_exchange")
        checker.record_ws_connect()
        checker.record_order_fill(True)
        checker.record_order_fill(True)
        checker.record_order_fill(False)
        score = checker.health_score
        assert 0.0 <= score <= 1.0

    def test_record_heartbeat_maintains_connection_score(self):
        checker = HealthChecker("test_exchange", stale_threshold_seconds=0.001)
        checker.record_ws_connect()
        # Record heartbeat to refresh timestamp
        checker.record_heartbeat()
        score = checker.health_score
        # After heartbeat, staleness should be minimal
        assert score > 0.3

    def test_record_error_does_not_raise(self):
        checker = HealthChecker("test_exchange")
        checker.record_error()
        checker.record_error()
        # Should not raise, just internally track
        assert checker.health_score >= 0.0
