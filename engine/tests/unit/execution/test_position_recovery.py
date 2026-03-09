"""Tests for PositionRecovery (US-052)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from src.execution.position_recovery import (
    OpenPosition,
    PositionRecovery,
    RecoveryAction,
    RecoveryResult,
)


def _mock_redis(entries: dict[str, dict] | None = None) -> MagicMock:
    """Create mock Redis with optional WAL entries."""
    redis = MagicMock()
    store = {}
    if entries:
        for tid, state in entries.items():
            key = f"leviathan:wal:{tid}"
            store[key] = json.dumps(state)

    redis.keys.return_value = list(store.keys())
    redis.get.side_effect = lambda k: store.get(k)
    redis.set.side_effect = lambda k, v, **kw: store.update({k: v})
    redis.delete.side_effect = lambda k: store.pop(k, None)
    return redis


def _now_iso(offset_seconds: float = 0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(seconds=offset_seconds)
    return dt.isoformat()


class TestWalOperations:
    def test_write_wal_stores_state(self):
        pr = PositionRecovery()
        redis = _mock_redis()
        pr.write_wal(redis, "t1", {"trade_id": "t1", "symbol": "BTC/USDT"})
        redis.set.assert_called_once()

    def test_clear_wal_removes_entry(self):
        pr = PositionRecovery()
        redis = _mock_redis()
        pr.clear_wal(redis, "t1")
        redis.delete.assert_called_once_with("leviathan:wal:t1")


class TestScanWal:
    def test_scan_returns_positions(self):
        pr = PositionRecovery()
        redis = _mock_redis({
            "t1": {
                "trade_id": "t1",
                "strategy_id": "cross_exchange_v1",
                "exchange_id": "binance",
                "symbol": "BTC/USDT",
                "side": "buy",
                "size": 0.01,
                "entry_price": 85000,
                "opened_at": _now_iso(60),
            }
        })
        positions = pr.scan_wal(redis)
        assert len(positions) == 1
        assert positions[0].trade_id == "t1"
        assert positions[0].symbol == "BTC/USDT"

    def test_scan_empty_redis(self):
        pr = PositionRecovery()
        redis = _mock_redis()
        assert pr.scan_wal(redis) == []

    def test_scan_handles_corrupt_json(self):
        pr = PositionRecovery()
        redis = MagicMock()
        redis.keys.return_value = ["leviathan:wal:bad"]
        redis.get.return_value = "not-json{"
        positions = pr.scan_wal(redis)
        assert len(positions) == 0


class TestDecideAction:
    def test_stale_position_returns_close(self):
        pr = PositionRecovery(stale_threshold_s=300)
        pos = OpenPosition(
            trade_id="t1", strategy_id="s1", exchange_id="binance",
            symbol="BTC/USDT", side="buy", size=0.01, entry_price=85000,
            opened_at=datetime.now(timezone.utc), age_seconds=600,
        )
        assert pr.decide_action(pos) == RecoveryAction.CLOSE

    def test_recent_position_returns_resume(self):
        pr = PositionRecovery(stale_threshold_s=300)
        pos = OpenPosition(
            trade_id="t1", strategy_id="s1", exchange_id="binance",
            symbol="BTC/USDT", side="buy", size=0.01, entry_price=85000,
            opened_at=datetime.now(timezone.utc), age_seconds=60,
        )
        assert pr.decide_action(pos) == RecoveryAction.RESUME

    def test_zero_size_returns_skip(self):
        pr = PositionRecovery()
        pos = OpenPosition(
            trade_id="t1", strategy_id="s1", exchange_id="binance",
            symbol="BTC/USDT", side="buy", size=0.0, entry_price=85000,
            opened_at=datetime.now(timezone.utc), age_seconds=10,
        )
        assert pr.decide_action(pos) == RecoveryAction.SKIP


class TestRecover:
    def test_recover_closes_stale_positions(self):
        pr = PositionRecovery(stale_threshold_s=60)
        redis = _mock_redis({
            "stale1": {
                "trade_id": "stale1",
                "strategy_id": "cross_exchange_v1",
                "exchange_id": "binance",
                "symbol": "BTC/USDT",
                "side": "buy",
                "size": 0.01,
                "entry_price": 85000,
                "opened_at": _now_iso(120),  # 2 min old → stale
            }
        })
        result = pr.recover(redis)
        assert result.positions_found == 1
        assert result.closed == 1
        assert result.resumed == 0

    def test_recover_resumes_recent_positions(self):
        pr = PositionRecovery(stale_threshold_s=300)
        redis = _mock_redis({
            "recent1": {
                "trade_id": "recent1",
                "strategy_id": "latency_arb_v1",
                "exchange_id": "upbit",
                "symbol": "ETH/USDT",
                "side": "sell",
                "size": 0.1,
                "entry_price": 3200,
                "opened_at": _now_iso(10),  # 10s old → recent
            }
        })
        result = pr.recover(redis)
        assert result.positions_found == 1
        assert result.resumed == 1
        assert result.closed == 0

    def test_recover_empty_returns_zero(self):
        pr = PositionRecovery()
        redis = _mock_redis()
        result = pr.recover(redis)
        assert result.positions_found == 0
        assert result.closed == 0

    def test_recover_mixed(self):
        pr = PositionRecovery(stale_threshold_s=60)
        redis = _mock_redis({
            "old": {
                "trade_id": "old", "strategy_id": "s1", "exchange_id": "binance",
                "symbol": "BTC/USDT", "side": "buy", "size": 0.01,
                "entry_price": 85000, "opened_at": _now_iso(200),
            },
            "new": {
                "trade_id": "new", "strategy_id": "s2", "exchange_id": "upbit",
                "symbol": "ETH/USDT", "side": "sell", "size": 0.1,
                "entry_price": 3200, "opened_at": _now_iso(5),
            },
        })
        result = pr.recover(redis)
        assert result.positions_found == 2
        assert result.closed == 1
        assert result.resumed == 1
        assert len(result.actions) == 2
