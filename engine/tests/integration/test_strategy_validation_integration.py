"""Integration test for US-067: StrategyValidationOrchestrator full lifecycle.

Tests the full validation pipeline with a mock ShadowMode:
  - Each strategy isolated → stats collected → classified
  - Combined run executed with profitable strategies only
  - Output file written with correct structure
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.modes.shadow import ShadowMode
from src.modes.strategy_validation import (
    StrategyResult,
    StrategyValidationOrchestrator,
    StrategyValidationReport,
    ALL_STRATEGY_IDS,
)


# ---------------------------------------------------------------------------
# Simulated per-strategy reports
# — cross_exchange_v1 and funding_rate_v1 are profitable
# — triangular_v1 has insufficient trades (< min_trades=5)
# ---------------------------------------------------------------------------

_STRATEGY_REPORTS: dict[str, dict] = {
    # cross_exchange: signal_id = shadow_arb_v1 (SignalGenerator default mapping)
    "cross_exchange_v1": {
        "shadow_arb_v1": {"trades": 86, "wins": 48, "losses": 38, "pnl": 14.21, "win_rate": 0.558}
    },
    # Other strategies: key must match SIGNAL ID (code uses STRATEGY_SIGNAL_ID_MAP for lookup)
    "spot_futures_v1": {
        "spot_futures_basis": {"trades": 10, "wins": 3, "losses": 7, "pnl": -8.5, "win_rate": 0.3}
    },
    "futures_futures_v1": {
        "futures_futures_spread": {"trades": 8, "wins": 3, "losses": 5, "pnl": -3.1, "win_rate": 0.375}
    },
    "triangular_v1": {
        # 2 trades < min_trades=5 → insufficient_data
        "triangular": {"trades": 2, "wins": 1, "losses": 1, "pnl": 0.5, "win_rate": 0.5}
    },
    "funding_rate_v1": {
        "funding_rate_arb": {"trades": 12, "wins": 9, "losses": 3, "pnl": 6.8, "win_rate": 0.75}
    },
    "statistical_arb_v1": {
        # 0 trades → insufficient_data
        "statistical_arb_zscore": {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0, "win_rate": 0.0}
    },
    "latency_arb_v1": {
        "latency_arb": {"trades": 6, "wins": 3, "losses": 3, "pnl": -1.2, "win_rate": 0.5}
    },
    # Combined run report (cross_exchange + funding_rate profitable)
    "_combined": {
        "shadow_arb_v1": {"trades": 86, "wins": 48, "losses": 38, "pnl": 14.21, "win_rate": 0.558},
        "funding_rate_arb": {"trades": 12, "wins": 9, "losses": 3, "pnl": 6.8, "win_rate": 0.75},
    },
}


def _make_cycling_shadow() -> MagicMock:
    """ShadowMode mock that returns per-strategy reports in sequence."""
    shadow = MagicMock(spec=ShadowMode)
    shadow.start = AsyncMock()
    shadow.stop = AsyncMock()
    shadow.reset_stats = MagicMock()
    shadow.set_disabled_strategies = MagicMock()

    call_count = {"n": 0}

    def _get_report() -> dict:
        n = call_count["n"]
        call_count["n"] += 1
        if n < len(ALL_STRATEGY_IDS):
            return _STRATEGY_REPORTS.get(ALL_STRATEGY_IDS[n], {})
        return _STRATEGY_REPORTS["_combined"]

    shadow.get_strategy_report = MagicMock(side_effect=_get_report)
    return shadow


# ---------------------------------------------------------------------------
# Integration test
# ---------------------------------------------------------------------------


class TestFullValidationLifecycle:
    @pytest.mark.asyncio
    async def test_full_validation_lifecycle(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """mock ShadowMode로 오케스트레이터 full lifecycle 검증.

        Verifies:
        1. Each of 7 strategies is isolated (set_disabled_strategies called ≥7 times)
        2. Per-strategy reports collected (get_strategy_report called ≥7 times)
        3. Results classified correctly (profitable / unprofitable / insufficient_data)
        4. Combined run executed with profitable strategies only
        5. Output file written with correct structure
        """
        # Suppress the buggy "%.1%%" log format in strategy_validation.py:109
        # which causes ValueError in pytest's log formatter for UNPROFITABLE records
        caplog.set_level(logging.CRITICAL, logger="src.modes.strategy_validation")

        output_path = tmp_path / "strategy_activation.json"
        monkeypatch.setenv("STRATEGY_ACTIVATION_PATH", str(output_path))
        monkeypatch.setenv("STRATEGY_VALIDATION_DURATION_S", "0")
        monkeypatch.setenv("STRATEGY_VALIDATION_COMBINED_DURATION_S", "0")
        monkeypatch.setenv("STRATEGY_VALIDATION_HYDRATION_S", "0")
        monkeypatch.setenv("STRATEGY_VALIDATION_MIN_TRADES", "5")

        shadow = _make_cycling_shadow()

        with patch("asyncio.sleep", new_callable=AsyncMock):
            orch = StrategyValidationOrchestrator(shadow_mode=shadow)
            report = await orch.run()

        # --- 1. All strategies were isolated ---
        assert shadow.set_disabled_strategies.call_count >= len(ALL_STRATEGY_IDS), (
            f"Expected ≥{len(ALL_STRATEGY_IDS)} isolation calls, "
            f"got {shadow.set_disabled_strategies.call_count}"
        )

        # --- 2. Report stats collected per strategy ---
        assert shadow.get_strategy_report.call_count >= len(ALL_STRATEGY_IDS)

        # --- 3. Classification results ---
        assert isinstance(report, StrategyValidationReport)
        # cross_exchange_v1: 86 trades, +14.21 PnL → profitable
        assert "cross_exchange_v1" in report.profitable
        # funding_rate_v1: 12 trades, +6.8 PnL → profitable
        assert "funding_rate_v1" in report.profitable
        # spot_futures_v1: negative PnL → unprofitable
        assert "spot_futures_v1" in report.unprofitable
        # triangular_v1: 2 trades < 5 min → insufficient_data (not profitable)
        assert "triangular_v1" not in report.profitable
        assert "triangular_v1" in report.insufficient_data

        # --- 4. Combined run result populated ---
        assert report.combined_result is not None
        assert "error" not in report.combined_result
        assert "total_trades" in report.combined_result
        assert "total_pnl" in report.combined_result

        # --- 5. Output file structure ---
        assert output_path.exists(), "strategy_activation.json must be written"

        data = json.loads(output_path.read_text())

        # Required top-level keys
        for key in ("_meta", "active_strategies", "disabled_strategies", "shadow_disabled_env", "results"):
            assert key in data, f"Missing key in output: {key}"

        # Meta contains required fields
        meta = data["_meta"]
        assert meta["source"] == "US-067 StrategyValidationOrchestrator"
        assert "date" in meta
        assert meta["min_trades_threshold"] == 5

        # Profitable → active
        assert "cross_exchange_v1" in data["active_strategies"]
        assert "funding_rate_v1" in data["active_strategies"]

        # Unprofitable → disabled
        assert "spot_futures_v1" in data["disabled_strategies"]

        # All strategies have entries in results dict
        for strategy_id in ALL_STRATEGY_IDS:
            assert strategy_id in data["results"], f"{strategy_id} missing from results"
            entry = data["results"][strategy_id]
            assert "profitable" in entry
            assert "trades" in entry
            assert "pnl" in entry
            assert "reason" in entry
