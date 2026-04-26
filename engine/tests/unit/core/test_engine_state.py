"""Phase 5.2.1: EngineState dataclass 검증."""
from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from src.core.engine_state import EngineState


class TestEngineStateDefaults:
    def test_default_init(self) -> None:
        s = EngineState()
        assert s.running is False
        assert s.kill_switch_active is False
        assert isinstance(s.shutdown_event, asyncio.Event)
        assert s.background_tasks == []
        assert s.total_pnl == Decimal("0")
        assert s.peak_equity is None
        assert s.position_sizes == {}
        assert s.cross_exchange_positions == set()
        assert s.cross_gross_exposure == Decimal("0")
        assert s.exchange_health == {}
        assert s.position_tracking_errors == 0
        assert s.pm_drain_errors == 0
        assert s.regime_last_pnl == 0.0

    def test_mutation_isolation(self) -> None:
        """별도 인스턴스 mutable field 공유 방지 (default_factory)."""
        a = EngineState()
        b = EngineState()
        a.position_sizes["BTC"] = Decimal("1")
        a.cross_exchange_positions.add("ETH")
        assert b.position_sizes == {}
        assert b.cross_exchange_positions == set()


class TestEngineStateReset:
    def test_reset_clears_all_mutable(self) -> None:
        s = EngineState()
        s.running = True
        s.total_pnl = Decimal("100")
        s.position_sizes["BTC"] = Decimal("0.5")
        s.cross_exchange_positions.add("ETH")
        s.exchange_health["binance"] = Decimal("0.95")
        s.position_tracking_errors = 3
        s.regime_pnl_history.extend([1.0, 2.0])
        s.regime_last_pnl = 5.5
        s.reset()
        assert s.running is False
        assert s.total_pnl == Decimal("0")
        assert s.position_sizes == {}
        assert s.cross_exchange_positions == set()
        assert s.exchange_health == {}
        assert s.position_tracking_errors == 0
        assert s.regime_pnl_history == []
        assert s.regime_last_pnl == 0.0


class TestEngineStateSnapshot:
    def test_snapshot_immutable_dict(self) -> None:
        s = EngineState()
        s.total_pnl = Decimal("42.5")
        s.peak_equity = Decimal("100")
        s.position_sizes["BTC"] = Decimal("0.5")
        s.cross_exchange_positions.add("ETH")
        snap = s.snapshot()
        assert snap["total_pnl"] == "42.5"
        assert snap["peak_equity"] == "100"
        assert snap["position_sizes"] == {"BTC": "0.5"}
        assert snap["cross_exchange_positions"] == ["ETH"]

    def test_snapshot_does_not_mutate(self) -> None:
        s = EngineState()
        s.position_sizes["BTC"] = Decimal("1")
        snap = s.snapshot()
        snap["position_sizes"]["ETH"] = "999"  # noqa: not Decimal — string after snapshot
        assert s.position_sizes == {"BTC": Decimal("1")}  # original unchanged
