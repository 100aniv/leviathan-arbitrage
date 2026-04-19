"""Unit tests for StrategyRegistry (Phoenix Path-B Day-4).

These tests exercise the standalone registry module without touching
main.py. The registry under test is intentionally decoupled: strategies
are represented by lightweight stubs, the universe matrix by a minimal
dataclass shim, and the budget ledger / CB by duck-typed stubs.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from src.core.strategy_registry import (
    REASON_BUDGET_EXHAUSTED,
    REASON_CB_TRIP,
    REASON_NO_VALID_UNIVERSE,
    StrategyEntry,
    StrategyRegistry,
)


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _StubStrategy:
    def __init__(self, strategy_id: str, strategy_type: str = "cross_exchange_spot"):
        self.strategy_id = strategy_id
        self.STRATEGY_TYPE = strategy_type


class _StubUniverseMatrix:
    """Minimal shim exposing ``_entries`` (the real matrix's internal dict)."""

    def __init__(self, entries: dict):
        # Keys are (strategy_id, symbol, leg_a, leg_b); values irrelevant here.
        self._entries = entries


class _StubBudgetLedger:
    def __init__(self, halted: set[str] | None = None):
        self._halted = set(halted or set())

    def is_strategy_halted(self, sid: str) -> bool:
        return sid in self._halted

    def halt(self, sid: str) -> None:
        self._halted.add(sid)

    def get_status(self) -> dict:  # mimics StrategyBudgetLedger.get_status
        return {}


class _StubCircuitBreaker:
    def __init__(self, blocked: set[str] | None = None):
        self._blocked = set(blocked or set())

    def is_allowed(self, sid: str) -> bool:
        return sid not in self._blocked

    def block(self, sid: str) -> None:
        self._blocked.add(sid)


def _make_registry(
    *,
    config: dict | None = None,
    universe: _StubUniverseMatrix | None = None,
    ledger: _StubBudgetLedger | None = None,
) -> StrategyRegistry:
    return StrategyRegistry(
        config=config or {},
        universe_matrix=universe,
        budget_ledger=ledger,
        cost_calculator=None,
    )


# ---------------------------------------------------------------------------
# Core lifecycle
# ---------------------------------------------------------------------------


def test_register_adds_entry_and_emits_log(caplog):
    reg = _make_registry()
    entry = StrategyEntry(
        strategy_id="cross_exchange_v1",
        instance=_StubStrategy("cross_exchange_v1"),
        is_active=True,
        allocation_pct=Decimal("25"),
    )
    with caplog.at_level("INFO"):
        reg.register(entry)
    assert reg.get("cross_exchange_v1") is entry
    assert any("strategy_registered" in m for m in caplog.messages)


def test_register_replaces_existing_id():
    reg = _make_registry()
    first = StrategyEntry(
        strategy_id="ce_v1",
        instance=_StubStrategy("ce_v1"),
        is_active=True,
    )
    second = StrategyEntry(
        strategy_id="ce_v1",
        instance=_StubStrategy("ce_v1"),
        is_active=False,
    )
    reg.register(first)
    reg.register(second)
    assert reg.get("ce_v1") is second


def test_get_active_filters_inactive_entries():
    reg = _make_registry()
    reg.register(
        StrategyEntry("a", _StubStrategy("a"), is_active=True)
    )
    reg.register(
        StrategyEntry("b", _StubStrategy("b"), is_active=False)
    )
    reg.register(
        StrategyEntry("c", _StubStrategy("c"), is_active=True)
    )
    active_ids = {e.strategy_id for e in reg.get_active()}
    assert active_ids == {"a", "c"}


def test_deactivate_sets_flag_and_reason(caplog):
    reg = _make_registry()
    reg.register(StrategyEntry("x", _StubStrategy("x"), is_active=True))
    with caplog.at_level("WARNING"):
        reg.deactivate("x", "BUDGET_EXHAUSTED")
    entry = reg.get("x")
    assert entry is not None
    assert entry.is_active is False
    assert entry.deactivation_reason == "BUDGET_EXHAUSTED"
    assert any("strategy_deactivated" in m for m in caplog.messages)


def test_deactivate_unknown_strategy_is_noop():
    reg = _make_registry()
    # Should not raise even if strategy isn't registered.
    reg.deactivate("does-not-exist", "BUDGET_EXHAUSTED")
    assert reg.get("does-not-exist") is None


def test_deactivate_is_idempotent_for_same_reason():
    reg = _make_registry()
    reg.register(StrategyEntry("x", _StubStrategy("x"), is_active=True))
    reg.deactivate("x", "CB_TRIP")
    reg.deactivate("x", "CB_TRIP")  # second call must be a no-op
    assert reg.get("x").deactivation_reason == "CB_TRIP"


def test_activate_sets_flag_only_when_previously_inactive():
    reg = _make_registry()
    reg.register(StrategyEntry("x", _StubStrategy("x"), is_active=True))
    assert reg.activate("x") is False  # already active
    reg.deactivate("x", "OPERATOR")
    assert reg.activate("x") is True
    assert reg.get("x").is_active is True
    assert reg.get("x").deactivation_reason is None


def test_health_report_includes_all_entries():
    reg = _make_registry()
    reg.register(StrategyEntry("a", _StubStrategy("a"), is_active=True))
    reg.register(StrategyEntry("b", _StubStrategy("b"), is_active=False))
    report = reg.health_report()
    assert set(report.keys()) == {"a", "b"}
    assert report["a"].is_active is True
    assert report["b"].is_active is False


def test_record_error_increments_counter():
    reg = _make_registry()
    reg.register(StrategyEntry("x", _StubStrategy("x"), is_active=True))
    reg.record_error("x")
    reg.record_error("x")
    assert reg.get("x").error_count == 2


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def _write_activation_json(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "strategy_activation.json"
    path.write_text(json.dumps(payload))
    return path


def test_load_active_from_config_skips_disabled_strategies(tmp_path):
    """active_strategies ∩ disabled_strategies are excluded."""
    activation_path = _write_activation_json(
        tmp_path,
        {
            "active_strategies": [
                "cross_exchange_v1",
                "spot_futures_v1",
                "latency_arb_v1",
            ],
            "disabled_strategies": ["latency_arb_v1"],
            "unverified_strategies": [],
        },
    )

    created: list[str] = []

    def factory(sid, _registry):
        created.append(sid)
        return _StubStrategy(sid)

    reg = _make_registry()
    loaded = reg.load_active_from_config(activation_path, factory=factory)

    assert "latency_arb_v1" not in loaded
    assert "latency_arb_v1" not in created
    assert set(loaded) == {"cross_exchange_v1", "spot_futures_v1"}
    # Registered entries mirror loaded list
    assert set(reg.list_strategies()) == {"cross_exchange_v1", "spot_futures_v1"}
    # All loaded strategies are active by default.
    assert all(e.is_active for e in reg.all_entries())


def test_load_active_from_config_dry_run_does_not_instantiate(tmp_path):
    activation_path = _write_activation_json(
        tmp_path,
        {
            "active_strategies": ["ce_v1"],
            "disabled_strategies": [],
            "unverified_strategies": [],
        },
    )
    reg = _make_registry()
    loaded = reg.load_active_from_config(activation_path, factory=None)
    assert loaded == ["ce_v1"]
    # Dry-run means nothing got registered.
    assert reg.list_strategies() == []


def test_load_active_handles_missing_file(tmp_path):
    """Missing activation file → empty load, no crash."""
    missing = tmp_path / "does_not_exist.json"
    reg = _make_registry()
    loaded = reg.load_active_from_config(missing, factory=lambda sid, r: _StubStrategy(sid))
    assert loaded == []


def test_load_active_reads_allocation_pct_from_engine_config(tmp_path):
    activation_path = _write_activation_json(
        tmp_path,
        {
            "active_strategies": ["cross_exchange_v1"],
            "disabled_strategies": [],
            "unverified_strategies": [],
        },
    )
    config = {
        "capital": {
            "strategies": {
                "cross_exchange": {"allocation_pct": 40},
            }
        }
    }
    reg = _make_registry(config=config)
    reg.load_active_from_config(
        activation_path,
        factory=lambda sid, r: _StubStrategy(sid),
    )
    entry = reg.get("cross_exchange_v1")
    assert entry is not None
    assert entry.allocation_pct == Decimal("40")


def test_load_active_unverified_registers_inactive(tmp_path):
    activation_path = _write_activation_json(
        tmp_path,
        {
            "active_strategies": ["active_v1"],
            "disabled_strategies": [],
            "unverified_strategies": ["preview_v1"],
        },
    )
    reg = _make_registry()
    reg.load_active_from_config(
        activation_path,
        factory=lambda sid, r: _StubStrategy(sid),
    )
    active = reg.get("active_v1")
    preview = reg.get("preview_v1")
    assert active is not None and active.is_active is True
    assert preview is not None and preview.is_active is False
    assert preview.deactivation_reason == "UNVERIFIED"


# ---------------------------------------------------------------------------
# Universe filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_universe_filter_deactivates_empty_universe():
    """Strategies with zero UniverseMatrix entries → NO_VALID_UNIVERSE."""
    matrix = _StubUniverseMatrix(
        entries={
            # Only "ce_v1" has validated pairs.
            ("ce_v1", "BTC/USDT", "binance", "bitget"): object(),
            ("ce_v1", "ETH/USDT", "binance", "bitget"): object(),
        }
    )
    reg = _make_registry(universe=matrix)
    reg.register(StrategyEntry("ce_v1", _StubStrategy("ce_v1"), is_active=True))
    reg.register(StrategyEntry("empty_v1", _StubStrategy("empty_v1"), is_active=True))

    deactivated = await reg.apply_universe_filter()
    assert deactivated == ["empty_v1"]
    assert reg.get("empty_v1").is_active is False
    assert reg.get("empty_v1").deactivation_reason == REASON_NO_VALID_UNIVERSE
    assert reg.get("ce_v1").is_active is True
    # Universe entry counts are cached on the entry.
    assert reg.get("ce_v1").universe_entry_count == 2
    assert reg.get("empty_v1").universe_entry_count == 0


@pytest.mark.asyncio
async def test_apply_universe_filter_no_matrix_is_noop():
    reg = _make_registry(universe=None)
    reg.register(StrategyEntry("a", _StubStrategy("a"), is_active=True))
    assert await reg.apply_universe_filter() == []
    assert reg.get("a").is_active is True


# ---------------------------------------------------------------------------
# Budget / CB subscriptions
# ---------------------------------------------------------------------------


def test_subscribe_budget_ledger_deactivates_halted_strategies():
    ledger = _StubBudgetLedger()
    reg = _make_registry(ledger=ledger)
    reg.register(StrategyEntry("ok", _StubStrategy("ok"), is_active=True))
    reg.register(StrategyEntry("halted", _StubStrategy("halted"), is_active=True))

    reg.subscribe_budget_ledger()
    # No halts yet — sweep produces zero deactivations.
    assert reg.poll_budget_halts() == []

    # Halt "halted" in the ledger, then run another sweep.
    ledger.halt("halted")
    deactivated = reg.poll_budget_halts()
    assert deactivated == ["halted"]
    assert reg.get("halted").deactivation_reason == REASON_BUDGET_EXHAUSTED
    assert reg.get("ok").is_active is True


def test_subscribe_circuit_breaker_deactivates_blocked_strategies():
    cb = _StubCircuitBreaker()
    reg = _make_registry()
    reg.register(StrategyEntry("a", _StubStrategy("a"), is_active=True))
    reg.register(StrategyEntry("b", _StubStrategy("b"), is_active=True))

    reg.subscribe_circuit_breaker(cb)
    assert reg.poll_cb_halts() == []

    cb.block("b")
    deactivated = reg.poll_cb_halts()
    assert deactivated == ["b"]
    assert reg.get("b").deactivation_reason == REASON_CB_TRIP
    assert reg.get("a").is_active is True


def test_subscribe_budget_ledger_no_ledger_is_noop():
    reg = _make_registry(ledger=None)
    reg.register(StrategyEntry("a", _StubStrategy("a"), is_active=True))
    # Returns a no-op unsubscribe without failing.
    unsub = reg.subscribe_budget_ledger()
    assert callable(unsub)
    assert reg.poll_budget_halts() == []
