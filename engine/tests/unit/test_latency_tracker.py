"""Tests for LatencyTracker."""
from __future__ import annotations

import pytest

from src.core.latency_tracker import ExchangeLatencyInfo, LatencyTracker


def test_record_single_latency():
    tracker = LatencyTracker(window_size=5)
    tracker.record_latency("binance", 10.0)
    info = tracker.get_latency_info("binance")
    assert info is not None
    assert isinstance(info, ExchangeLatencyInfo)
    assert info.exchange_id == "binance"
    assert info.ema_ms == pytest.approx(10.0, rel=1e-3)
    assert info.sample_count == 1


def test_ema_smoothing_two_samples():
    tracker = LatencyTracker(window_size=10, ema_alpha=0.5)
    tracker.record_latency("binance", 10.0)
    tracker.record_latency("binance", 20.0)
    info = tracker.get_latency_info("binance")
    # EMA: first sample initializes at 10.0, second = 0.5*20 + 0.5*10 = 15.0
    assert info.ema_ms == pytest.approx(15.0, rel=1e-3)


def test_ema_tracks_trend():
    tracker = LatencyTracker(window_size=10, ema_alpha=0.5)
    tracker.record_latency("binance", 10.0)
    tracker.record_latency("binance", 20.0)
    tracker.record_latency("binance", 20.0)
    info = tracker.get_latency_info("binance")
    # EMA after 3rd: 0.5*20 + 0.5*15 = 17.5
    assert info.ema_ms == pytest.approx(17.5, rel=1e-3)


def test_exchange_ranking_by_ema():
    tracker = LatencyTracker(window_size=5)
    tracker.record_latency("binance", 5.0)
    tracker.record_latency("okx", 15.0)
    tracker.record_latency("bybit", 10.0)
    ranking = tracker.ranked_exchanges()
    assert ranking[0] == "binance"
    assert ranking[1] == "bybit"
    assert ranking[2] == "okx"


def test_lead_lag_detects_fast_leader():
    tracker = LatencyTracker(window_size=5)
    tracker.record_latency("binance", 2.0)
    tracker.record_latency("okx", 20.0)
    pairs = tracker.lead_lag_pairs(threshold_ms=5.0)
    assert ("binance", "okx") in pairs


def test_lead_lag_no_pair_when_similar_latency():
    tracker = LatencyTracker(window_size=5)
    tracker.record_latency("binance", 10.0)
    tracker.record_latency("okx", 12.0)
    pairs = tracker.lead_lag_pairs(threshold_ms=5.0)
    assert len(pairs) == 0


def test_lead_lag_reverse_direction():
    tracker = LatencyTracker(window_size=5)
    tracker.record_latency("binance", 25.0)
    tracker.record_latency("okx", 5.0)
    pairs = tracker.lead_lag_pairs(threshold_ms=10.0)
    assert ("okx", "binance") in pairs


def test_sliding_window_evicts_old_samples():
    tracker = LatencyTracker(window_size=3)
    tracker.record_latency("binance", 100.0)
    tracker.record_latency("binance", 100.0)
    tracker.record_latency("binance", 100.0)
    tracker.record_latency("binance", 10.0)  # evicts first 100.0
    info = tracker.get_latency_info("binance")
    # Window is [100, 100, 10] → avg = 70.0
    assert info.window_avg_ms == pytest.approx(70.0, rel=1e-3)


def test_unknown_exchange_returns_none():
    tracker = LatencyTracker()
    assert tracker.get_latency_info("unknown_exchange") is None


def test_ranked_exchanges_empty():
    tracker = LatencyTracker()
    assert tracker.ranked_exchanges() == []


def test_lead_lag_empty_returns_empty():
    tracker = LatencyTracker()
    assert tracker.lead_lag_pairs() == []


def test_sample_count_increments():
    tracker = LatencyTracker(window_size=10)
    for i in range(7):
        tracker.record_latency("binance", float(i))
    info = tracker.get_latency_info("binance")
    assert info.sample_count == 7


def test_multiple_exchanges_independent():
    tracker = LatencyTracker(window_size=5)
    tracker.record_latency("binance", 5.0)
    tracker.record_latency("okx", 15.0)
    tracker.record_latency("okx", 25.0)
    binance_info = tracker.get_latency_info("binance")
    okx_info = tracker.get_latency_info("okx")
    assert binance_info.sample_count == 1
    assert okx_info.sample_count == 2
