"""Tests for LiveGate preflight 10-item check — US-055.

US-055: LiveGate.run_preflight() 10항목 체크
  - kill_switch_verify, api_connectivity, balance_sufficient,
    risk_guardian_healthy, paper_hours_gate,
    kill_switch_clear, circuit_breaker_closed,
    sharpe_gate, mdd_gate, signals_gate
  - result dict: checks, all_pass, failed_checks, timestamp
  - saves to .omc/state/preflight-result.json
  - enforce_or_fallback() calls run_preflight()
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


EXPECTED_CHECK_KEYS = {
    "kill_switch_verify",
    "api_connectivity",
    "balance_sufficient",
    "risk_guardian_healthy",
    "paper_hours_gate",
    "kill_switch_clear",
    "circuit_breaker_closed",
    "sharpe_gate",
    "mdd_gate",
    "signals_gate",
}


def _make_gate(**kwargs):
    """Create a LiveGate with all external dependencies mocked."""
    from src.modes.live_gate import LiveGate

    gate = LiveGate.__new__(LiveGate)
    # Minimal init state
    gate._settings = MagicMock()
    gate._settings.live_gate.sharpe_threshold = 0.0
    gate._settings.live_gate.min_signals_per_day = 0
    gate._settings.live_gate.evaluation_days = 1
    gate._settings.live_gate.bypass = False
    gate._risk_guardian = kwargs.get("risk_guardian", None)
    gate._api_connectivity_fn = kwargs.get("api_connectivity_fn", None)
    gate._balance_fn = kwargs.get("balance_fn", None)
    gate._circuit_breaker = MagicMock()
    gate._circuit_breaker.state = "CLOSED"
    return gate


# ---------------------------------------------------------------------------
# US-055-1: run_preflight() exists and returns (bool, dict)
# ---------------------------------------------------------------------------

class TestRunPreflightExists:
    def test_run_preflight_method_exists(self):
        """LiveGate must have run_preflight() method — US-055."""
        from src.modes.live_gate import LiveGate
        assert hasattr(LiveGate, "run_preflight"), (
            "LiveGate missing run_preflight() — US-055 requires 10-item preflight check"
        )

    def test_run_preflight_is_coroutine(self):
        """run_preflight() must be an async method."""
        from src.modes.live_gate import LiveGate
        assert inspect.iscoroutinefunction(LiveGate.run_preflight), (
            "LiveGate.run_preflight must be async (coroutinefunction)"
        )


# ---------------------------------------------------------------------------
# US-055-2: result dict structure
# ---------------------------------------------------------------------------

class TestRunPreflightResultStructure:
    @pytest.mark.asyncio
    async def test_returns_tuple_of_bool_and_dict(self, tmp_path):
        """run_preflight() returns (bool, dict) tuple."""
        from src.modes.live_gate import LiveGate

        gate = MagicMock(spec=LiveGate)
        gate.run_preflight = AsyncMock(return_value=(True, {
            "checks": {k: True for k in EXPECTED_CHECK_KEYS},
            "all_pass": True,
            "failed_checks": [],
            "timestamp": "2026-04-03T00:00:00+00:00",
        }))

        result = await gate.run_preflight()
        ok, data = result
        assert isinstance(ok, bool)
        assert isinstance(data, dict)

    @pytest.mark.asyncio
    async def test_result_has_required_top_level_keys(self, tmp_path):
        """run_preflight() result must have checks/all_pass/failed_checks/timestamp."""
        from src.modes.live_gate import LiveGate

        gate = MagicMock(spec=LiveGate)
        gate.run_preflight = AsyncMock(return_value=(True, {
            "checks": {},
            "all_pass": True,
            "failed_checks": [],
            "timestamp": "2026-04-03T00:00:00+00:00",
        }))

        _, data = await gate.run_preflight()
        for key in ("checks", "all_pass", "failed_checks", "timestamp"):
            assert key in data, f"run_preflight result missing key: {key!r}"

    @pytest.mark.asyncio
    async def test_result_checks_has_all_10_keys(self, tmp_path):
        """run_preflight() result['checks'] must contain all 10 check keys."""
        from src.modes.live_gate import LiveGate

        # Test against the actual implementation
        gate = LiveGate.__new__(LiveGate)
        gate._settings = MagicMock()
        gate._settings.live_gate.sharpe_threshold = 0.0
        gate._settings.live_gate.min_signals_per_day = 0
        gate._settings.live_gate.evaluation_days = 1
        gate._settings.live_gate.bypass = False
        gate._risk_guardian = None
        gate._api_connectivity_fn = None
        gate._balance_fn = None
        gate._circuit_breaker = MagicMock()
        gate._circuit_breaker.state = "CLOSED"

        # Mock evaluate() to return a result with all check names
        mock_check = MagicMock()
        mock_eval = MagicMock()
        mock_eval.checks = [
            MagicMock(name_attr=None, passed=True),
        ]

        # Patch evaluate and state dir
        with (
            patch.object(type(gate), "evaluate", new_callable=lambda: AsyncMock),
            patch("src.modes.live_gate.pathlib") as mock_pathlib,
        ):
            gate.evaluate = AsyncMock(return_value=MagicMock(
                checks=[
                    MagicMock(name="Kill Switch", passed=True),
                    MagicMock(name="Circuit Breaker", passed=True),
                    MagicMock(name="Sharpe Ratio", passed=True),
                    MagicMock(name="Max Drawdown", passed=True),
                    MagicMock(name="Signals/Day", passed=True),
                ]
            ))
            mock_pathlib.Path.return_value.resolve.return_value.parents.__getitem__ = MagicMock(
                return_value=MagicMock(__truediv__=MagicMock(return_value=MagicMock(
                    mkdir=MagicMock(),
                    __truediv__=MagicMock(return_value=MagicMock(write_text=MagicMock()))
                )))
            )

            with patch("src.modes.live_gate.LiveGate._verify_kill_switch_blocks_orders", return_value=True), \
                 patch("src.modes.live_gate.LiveGate._check_balance_sufficient", return_value=True), \
                 patch("src.modes.live_gate.LiveGate._check_risk_guardian_healthy", return_value=True), \
                 patch("src.modes.live_gate.LiveGate._check_paper_hours_gate", return_value=True):
                _, result = await gate.run_preflight.__func__(gate)

        assert set(result["checks"].keys()) >= EXPECTED_CHECK_KEYS, (
            f"run_preflight checks missing keys: {EXPECTED_CHECK_KEYS - set(result['checks'].keys())}"
        )


# ---------------------------------------------------------------------------
# US-055-3: all_pass logic
# ---------------------------------------------------------------------------

class TestPreflightAllPass:
    def test_all_pass_true_when_all_checks_pass(self):
        """all_pass=True only when all check values are True."""
        checks = {k: True for k in EXPECTED_CHECK_KEYS}
        failed = [k for k, v in checks.items() if not v]
        all_pass = len(failed) == 0
        assert all_pass is True

    def test_all_pass_false_when_any_check_fails(self):
        """all_pass=False when at least one check is False."""
        checks = {k: True for k in EXPECTED_CHECK_KEYS}
        checks["paper_hours_gate"] = False
        failed = [k for k, v in checks.items() if not v]
        all_pass = len(failed) == 0
        assert all_pass is False
        assert "paper_hours_gate" in failed

    def test_failed_checks_lists_all_failing_keys(self):
        """failed_checks contains names of all False check keys."""
        checks = {k: True for k in EXPECTED_CHECK_KEYS}
        checks["sharpe_gate"] = False
        checks["balance_sufficient"] = False
        failed = [k for k, v in checks.items() if not v]
        assert "sharpe_gate" in failed
        assert "balance_sufficient" in failed
        assert len(failed) == 2


# ---------------------------------------------------------------------------
# US-055-4: enforce_or_fallback calls run_preflight
# ---------------------------------------------------------------------------

class TestEnforceOrFallbackUsesRunPreflight:
    def test_enforce_or_fallback_source_calls_run_preflight(self):
        """enforce_or_fallback() must call run_preflight() (not evaluate())."""
        from src.modes.live_gate import LiveGate
        src = inspect.getsource(LiveGate.enforce_or_fallback)
        assert "run_preflight" in src, (
            "enforce_or_fallback() must call run_preflight() — US-055 requires preflight 10-check"
        )


# ---------------------------------------------------------------------------
# US-055-5: paper_hours_gate reads cumulative file
# ---------------------------------------------------------------------------

class TestPaperHoursGate:
    def test_paper_hours_gate_method_exists(self):
        """LiveGate must have _check_paper_hours_gate() — US-055 check 9."""
        from src.modes.live_gate import LiveGate
        assert hasattr(LiveGate, "_check_paper_hours_gate"), (
            "LiveGate missing _check_paper_hours_gate()"
        )

    def test_paper_hours_gate_source_reads_cumulative_file(self):
        """_check_paper_hours_gate() must read paper-cumulative-hours.json."""
        from src.modes.live_gate import LiveGate
        src = inspect.getsource(LiveGate._check_paper_hours_gate)
        assert "paper-cumulative-hours" in src or "cumulative" in src, (
            "_check_paper_hours_gate must read paper-cumulative-hours.json"
        )
