"""US-297: StatisticalArbStrategy DISABLED status guard.

Verifies that:
1. strategy_params.json has stat_arb.status == "DISABLED"
2. stat_arb.wfe == -1.03 (analysis result)
3. main.py _register_default_strategies skips StatisticalArbStrategy when
   strategy_params.json status is "DISABLED" (not in READY/MONITOR set)
"""
from __future__ import annotations

import json
import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PARAMS_PATH = (
    pathlib.Path(__file__).parent.parent.parent.parent
    / "config"
    / "strategy_params.json"
)


def _load_params() -> dict:
    return json.loads(PARAMS_PATH.read_text())


# ---------------------------------------------------------------------------
# Tests: strategy_params.json content
# ---------------------------------------------------------------------------


class TestStrategyParamsJson:
    def test_statistical_arb_status_is_disabled(self):
        """US-297: strategy_params.json must have stat_arb status DISABLED."""
        params = _load_params()
        assert "statistical_arb" in params, "statistical_arb key must exist in strategy_params.json"
        assert params["statistical_arb"]["status"] == "DISABLED"

    def test_statistical_arb_wfe_is_negative(self):
        """US-297: stat_arb WFE=-1.03 reflects walk-forward evaluation result."""
        params = _load_params()
        wfe = params["statistical_arb"]["wfe"]
        assert wfe == pytest.approx(-1.03), f"Expected wfe=-1.03, got {wfe}"

    def test_other_strategies_retain_their_status(self):
        """Sanity: disabling stat_arb must not affect other strategies' status."""
        params = _load_params()
        for name in ("spot_futures", "funding_rate", "cross_exchange"):
            assert params.get(name, {}).get("status") in (
                "READY", "MONITOR"
            ), f"{name} should still have READY/MONITOR status"


# ---------------------------------------------------------------------------
# Tests: main.py _register_default_strategies skips DISABLED stat_arb
# ---------------------------------------------------------------------------


class TestMainStatArbRegistration:
    """Test that _register_default_strategies skips stat_arb when status=DISABLED."""

    def _make_engine(self):
        """Import Engine and create a minimal instance without running __init__ side effects."""
        from src.main import Engine
        engine = Engine.__new__(Engine)
        # Minimal state so _register_default_strategies can run
        engine._cost_calculator = MagicMock()
        engine._regime_detector = MagicMock()
        engine._strategy_manager = MagicMock()
        engine._latency_tracker = None
        return engine

    @pytest.mark.asyncio
    async def test_stat_arb_not_registered_when_disabled(self):
        """US-297: StatisticalArbStrategy is NOT registered when status=DISABLED."""
        from src.main import Engine

        engine = self._make_engine()

        # Provide params where stat_arb is DISABLED
        disabled_params = {
            "statistical_arb": {"status": "DISABLED", "wfe": -1.03},
        }

        registered_names = []

        def capture_register(strategy):
            registered_names.append(type(strategy).__name__)

        engine._strategy_manager.register.side_effect = capture_register

        with patch.object(engine, "_load_strategy_params", return_value=disabled_params), \
             patch.object(engine, "_build_dex_adapter", return_value=None):
            await engine._register_default_strategies()

        assert "StatisticalArbStrategy" not in registered_names, (
            "StatisticalArbStrategy must NOT be registered when status=DISABLED"
        )

    @pytest.mark.asyncio
    async def test_stat_arb_registered_when_ready(self):
        """Guard: StatisticalArbStrategy IS registered when status=READY."""
        engine = self._make_engine()

        ready_params = {
            "statistical_arb": {"status": "READY", "wfe": 0.5},
        }

        registered_names = []

        def capture_register(strategy):
            registered_names.append(type(strategy).__name__)

        engine._strategy_manager.register.side_effect = capture_register

        with patch.object(engine, "_load_strategy_params", return_value=ready_params), \
             patch.object(engine, "_build_dex_adapter", return_value=None):
            await engine._register_default_strategies()

        assert "StatisticalArbStrategy" in registered_names, (
            "StatisticalArbStrategy must be registered when status=READY"
        )

    @pytest.mark.asyncio
    async def test_stat_arb_registered_when_monitor(self):
        """Guard: StatisticalArbStrategy IS registered when status=MONITOR."""
        engine = self._make_engine()

        monitor_params = {
            "statistical_arb": {"status": "MONITOR", "wfe": 0.1},
        }

        registered_names = []

        def capture_register(strategy):
            registered_names.append(type(strategy).__name__)

        engine._strategy_manager.register.side_effect = capture_register

        with patch.object(engine, "_load_strategy_params", return_value=monitor_params), \
             patch.object(engine, "_build_dex_adapter", return_value=None):
            await engine._register_default_strategies()

        assert "StatisticalArbStrategy" in registered_names, (
            "StatisticalArbStrategy must be registered when status=MONITOR"
        )

    @pytest.mark.asyncio
    async def test_other_strategies_always_registered_regardless_of_stat_arb(self):
        """US-297: disabling stat_arb must not prevent other strategies from registering."""
        engine = self._make_engine()

        disabled_params = {
            "statistical_arb": {"status": "DISABLED", "wfe": -1.03},
            "cross_exchange": {"status": "READY", "wfe": 1.2, "min_spread_bps": 5.0},
        }

        registered_names = []

        def capture_register(strategy):
            registered_names.append(type(strategy).__name__)

        engine._strategy_manager.register.side_effect = capture_register

        with patch.object(engine, "_load_strategy_params", return_value=disabled_params), \
             patch.object(engine, "_build_dex_adapter", return_value=None):
            await engine._register_default_strategies()

        # At minimum the always-registered strategies must appear
        assert "CrossExchangeStrategy" in registered_names
        assert "StatisticalArbStrategy" not in registered_names
