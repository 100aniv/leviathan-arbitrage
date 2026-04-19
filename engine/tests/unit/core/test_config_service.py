"""Tests for ConfigService (Path-B Day 4).

Covers:
  - Real engine.json parses cleanly
  - Numeric bound violations rejected
  - Cross-field validator (max_position_pct vs base_position_pct)
  - Dotted-path lookup mirrors JSON tree
  - reload() triggers on_change asyncio event
  - Comment keys (_bug_*_note, _comment) tolerated
  - Unknown exchanges rejected
  - Strategy allocation sum >100% rejected
  - execution.min_trade_notional_usd >=1 enforced
  - Singleton accessor returns same instance
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.core import config_service as cs_mod
from src.core.config_service import (
    ConfigService,
    EngineConfig,
    get_config_service,
    reset_config_service,
)


REAL_CONFIG_PATH = (
    Path(__file__).resolve().parents[3] / "config" / "engine.json"
)


@pytest.fixture
def real_raw() -> dict:
    return json.loads(REAL_CONFIG_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def tmp_cfg(tmp_path: Path, real_raw: dict):
    """Factory: write a mutated copy of real config to tmp and return its path."""

    def _write(overrides: dict | None = None) -> Path:
        raw = json.loads(json.dumps(real_raw))  # deep copy
        if overrides:
            for dotpath, value in overrides.items():
                node = raw
                parts = dotpath.split(".")
                for p in parts[:-1]:
                    node = node.setdefault(p, {})
                node[parts[-1]] = value
        p = tmp_path / "engine.json"
        p.write_text(json.dumps(raw), encoding="utf-8")
        return p

    return _write


# ---------------------------------------------------------------------------
# Real config parsing
# ---------------------------------------------------------------------------


class TestRealConfig:
    def test_parses_current_engine_json(self):
        svc = ConfigService(REAL_CONFIG_PATH)
        cfg = svc.load()
        assert isinstance(cfg, EngineConfig)
        assert cfg.mode in {"backtest", "paper", "live"}
        assert cfg.env in {"dev", "staging", "prod", "test"}

    def test_real_config_risk_ceiling_reasonable(self):
        cfg = ConfigService(REAL_CONFIG_PATH).load()
        assert 0 < cfg.risk.max_position_pct <= 100

    def test_real_config_active_exchanges_known(self):
        cfg = ConfigService(REAL_CONFIG_PATH).load()
        from src.core.config_service import KNOWN_EXCHANGES

        assert all(e in KNOWN_EXCHANGES for e in cfg.exchanges.active)


# ---------------------------------------------------------------------------
# Bound & constraint violations
# ---------------------------------------------------------------------------


class TestValidation:
    def test_reject_max_position_pct_over_100(self, tmp_cfg):
        path = tmp_cfg({"risk.max_position_pct": 200})
        with pytest.raises(ValidationError):
            ConfigService(path).load()

    def test_reject_base_position_above_max(self, tmp_cfg):
        path = tmp_cfg(
            {
                "risk.max_position_pct": 3.0,
                "dynamic_risk.base_position_pct": 10.0,
            }
        )
        with pytest.raises(ValidationError) as exc:
            ConfigService(path).load()
        assert "base_position_pct" in str(exc.value)

    def test_reject_fake_exchange(self, tmp_cfg):
        path = tmp_cfg({"exchanges.active": ["binance", "fake_exchange"]})
        with pytest.raises(ValidationError) as exc:
            ConfigService(path).load()
        assert "fake_exchange" in str(exc.value)

    def test_reject_strategy_allocation_over_100(self, tmp_cfg):
        path = tmp_cfg(
            {
                "capital.strategies": {
                    "funding_rate": {"allocation_pct": 60},
                    "cross_exchange": {"allocation_pct": 60},
                }
            }
        )
        with pytest.raises(ValidationError) as exc:
            ConfigService(path).load()
        assert "100" in str(exc.value)

    def test_reject_min_notional_below_1(self, tmp_cfg):
        path = tmp_cfg({"execution.min_trade_notional_usd": 0.5})
        with pytest.raises(ValidationError):
            ConfigService(path).load()

    def test_reject_invalid_mode(self, tmp_cfg):
        path = tmp_cfg({"mode": "shadow"})
        with pytest.raises(ValidationError):
            ConfigService(path).load()


# ---------------------------------------------------------------------------
# Accessors & tolerance
# ---------------------------------------------------------------------------


class TestAccessors:
    def test_dotted_path_returns_value(self):
        svc = ConfigService(REAL_CONFIG_PATH)
        svc.load()
        assert svc.get("risk.max_position_pct") == 6.0
        assert svc.get("execution.min_trade_notional_usd") == 5

    def test_dotted_path_default_on_missing(self):
        svc = ConfigService(REAL_CONFIG_PATH)
        svc.load()
        assert svc.get("nonexistent.path", "fallback") == "fallback"

    def test_comment_keys_tolerated(self, tmp_cfg):
        path = tmp_cfg(
            {
                "risk._comment": "sample",
                "risk._bug_999_note": "another note",
            }
        )
        cfg = ConfigService(path).load()
        # Comment keys flow through extras without breaking
        assert cfg.risk.max_position_pct > 0


# ---------------------------------------------------------------------------
# Reload + asyncio event
# ---------------------------------------------------------------------------


class TestReload:
    def test_reload_sets_on_change_event(self, tmp_cfg):
        path = tmp_cfg()
        svc = ConfigService(path)
        svc.load()

        async def _run() -> bool:
            _ = svc.on_change  # create event inside loop
            assert not svc.on_change.is_set()
            svc.reload()
            # Wait briefly to ensure set() landed
            await asyncio.wait_for(svc.on_change.wait(), timeout=1.0)
            return svc.on_change.is_set()

        assert asyncio.run(_run()) is True

    def test_reload_picks_up_disk_changes(self, tmp_cfg):
        path = tmp_cfg()
        svc = ConfigService(path)
        before = svc.load()
        assert before.risk.max_position_pct == 6.0

        # mutate file on disk — keep max >= dynamic_risk.base_position_pct (5.0)
        raw = json.loads(path.read_text())
        raw["risk"]["max_position_pct"] = 5.5
        path.write_text(json.dumps(raw))

        after = svc.reload()
        assert after.risk.max_position_pct == 5.5


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


class TestSingleton:
    def teardown_method(self):
        reset_config_service()

    def test_singleton_returns_same_instance(self):
        reset_config_service()
        a = get_config_service()
        b = get_config_service()
        assert a is b

    def test_reset_clears_singleton(self):
        reset_config_service()
        a = get_config_service()
        reset_config_service()
        b = get_config_service()
        assert a is not b
