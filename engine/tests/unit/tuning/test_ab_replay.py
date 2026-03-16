"""Tests for A/B Replay Infrastructure (US-200)."""
import pytest

from src.tuning.ab_replay import ABResult, ReplayEvent, replay_session


def _make_events() -> list[ReplayEvent]:
    """Create a deterministic set of test events."""
    return [
        ReplayEvent(timestamp_ms=1000, strategy_id="cross_exchange", symbol="BTC/USDT",
                     signal_edge_bps=12.0, threshold_bps=10.0, filled=True, fill_pnl_usd=5.0),
        ReplayEvent(timestamp_ms=2000, strategy_id="cross_exchange", symbol="ETH/USDT",
                     signal_edge_bps=8.0, threshold_bps=10.0, filled=True, fill_pnl_usd=-2.0),
        ReplayEvent(timestamp_ms=3000, strategy_id="cross_exchange", symbol="BTC/USDT",
                     signal_edge_bps=15.0, threshold_bps=10.0, filled=True, fill_pnl_usd=3.0),
        ReplayEvent(timestamp_ms=4000, strategy_id="cross_exchange", symbol="SOL/USDT",
                     signal_edge_bps=6.0, threshold_bps=10.0, filled=True, fill_pnl_usd=-1.0),
        ReplayEvent(timestamp_ms=5000, strategy_id="cross_exchange", symbol="BTC/USDT",
                     signal_edge_bps=20.0, threshold_bps=10.0, filled=True, fill_pnl_usd=4.0),
    ]


def test_deterministic_replay_same_params():
    """Same params -> identical results."""
    events = _make_events()
    params = {"threshold_bps": 10.0}
    result = replay_session(events, params, params)
    assert result.pnl_diff == 0.0
    assert result.wr_diff == 0.0
    assert result.a_trades == result.b_trades


def test_higher_threshold_filters_more():
    """Higher threshold -> fewer trades."""
    events = _make_events()
    params_a = {"threshold_bps": 5.0}   # Low: catches all 5 events
    params_b = {"threshold_bps": 15.0}  # High: catches only 2 events (15, 20)
    result = replay_session(events, params_a, params_b)
    assert result.a_trades == 5
    assert result.b_trades == 2
    assert result.b_pnl == 3.0 + 4.0  # only 15bps and 20bps events


def test_b_is_better_property():
    """b_is_better when pnl_diff > 0."""
    events = _make_events()
    # B filters out losing trades
    params_a = {"threshold_bps": 5.0}   # All trades: 5+(-2)+3+(-1)+4 = 9
    params_b = {"threshold_bps": 10.0}  # Only 12,15,20: 5+3+4 = 12
    result = replay_session(events, params_a, params_b)
    assert result.a_pnl == pytest.approx(9.0)
    assert result.b_pnl == pytest.approx(12.0)
    assert result.b_is_better is True


def test_empty_events():
    """Empty events -> zero trades."""
    result = replay_session([], {"threshold_bps": 10.0}, {"threshold_bps": 5.0})
    assert result.total_events == 0
    assert result.a_trades == 0
    assert result.b_trades == 0
    assert result.pnl_diff == 0.0
