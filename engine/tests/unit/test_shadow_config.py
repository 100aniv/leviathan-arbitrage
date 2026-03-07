"""Unit tests for shadow mode config files and run_shadow script.

Covers:
- shadow_mode.json can be loaded and has required keys
- testnet.env.example has all required env var keys
- scripts.run_shadow imports correctly
- scripts.run_shadow._build_strategy_params maps params correctly
- scripts.run_shadow._check_alert_thresholds fires warnings correctly
- scripts.run_shadow.run_shadow executes end-to-end with mocked ShadowRunner
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------
_ENGINE_ROOT = Path(__file__).parent.parent.parent
_SHADOW_CONFIG = _ENGINE_ROOT / "config" / "shadow_mode.json"
_TESTNET_ENV = _ENGINE_ROOT / "config" / "testnet.env.example"


# ---------------------------------------------------------------------------
# shadow_mode.json validation
# ---------------------------------------------------------------------------


class TestShadowModeJson:
    def test_file_exists(self) -> None:
        assert _SHADOW_CONFIG.exists(), f"shadow_mode.json not found at {_SHADOW_CONFIG}"

    def test_loads_as_valid_json(self) -> None:
        with _SHADOW_CONFIG.open() as fh:
            data = json.load(fh)
        assert isinstance(data, dict)

    def test_has_shadow_section(self) -> None:
        data = json.loads(_SHADOW_CONFIG.read_text())
        assert "shadow" in data, "Missing top-level 'shadow' key"

    def test_shadow_duration_is_72_hours(self) -> None:
        data = json.loads(_SHADOW_CONFIG.read_text())
        assert data["shadow"]["duration_hours"] == 72

    def test_shadow_strategies_includes_monitor_strategies(self) -> None:
        data = json.loads(_SHADOW_CONFIG.read_text())
        strategies = data["shadow"]["strategies"]
        expected = {"triangular", "cex_dex", "cross_exchange", "futures_futures"}
        assert set(strategies) == expected, f"Unexpected strategies: {strategies}"

    def test_metrics_track_required_fields(self) -> None:
        data = json.loads(_SHADOW_CONFIG.read_text())
        required = {"sharpe", "pnl", "max_drawdown", "trade_count", "win_rate"}
        tracked = set(data["metrics"]["track"])
        assert required <= tracked, f"Missing metrics: {required - tracked}"

    def test_alert_thresholds_present(self) -> None:
        data = json.loads(_SHADOW_CONFIG.read_text())
        thresholds = data["alert_thresholds"]
        assert "max_drawdown_pct" in thresholds
        assert "min_sharpe" in thresholds

    def test_alert_threshold_mdd_is_5_pct(self) -> None:
        data = json.loads(_SHADOW_CONFIG.read_text())
        assert data["alert_thresholds"]["max_drawdown_pct"] == 5.0

    def test_alert_threshold_sharpe_is_0_5(self) -> None:
        data = json.loads(_SHADOW_CONFIG.read_text())
        assert data["alert_thresholds"]["min_sharpe"] == 0.5


# ---------------------------------------------------------------------------
# testnet.env.example validation
# ---------------------------------------------------------------------------


class TestTestnetEnvExample:
    def test_file_exists(self) -> None:
        assert _TESTNET_ENV.exists(), f"testnet.env.example not found at {_TESTNET_ENV}"

    def _parse_keys(self) -> set[str]:
        """Return all KEY= names from the env file (skips comments and blank lines)."""
        keys: set[str] = set()
        for line in _TESTNET_ENV.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key = line.split("=", 1)[0].strip()
                keys.add(key)
        return keys

    def test_has_binance_api_key(self) -> None:
        assert "BINANCE_API_KEY" in self._parse_keys()

    def test_has_binance_api_secret(self) -> None:
        assert "BINANCE_API_SECRET" in self._parse_keys()

    def test_has_binance_testnet_flag(self) -> None:
        assert "BINANCE_TESTNET" in self._parse_keys()

    def test_has_bybit_api_key(self) -> None:
        assert "BYBIT_API_KEY" in self._parse_keys()

    def test_has_bybit_api_secret(self) -> None:
        assert "BYBIT_API_SECRET" in self._parse_keys()

    def test_has_bybit_testnet_flag(self) -> None:
        assert "BYBIT_TESTNET" in self._parse_keys()

    def test_has_engine_env_staging(self) -> None:
        content = _TESTNET_ENV.read_text()
        assert "ENGINE_ENV=staging" in content

    def test_has_execution_mode_sandbox(self) -> None:
        content = _TESTNET_ENV.read_text()
        assert "EXECUTION_MODE=sandbox" in content

    def test_has_use_native_adapters(self) -> None:
        assert "USE_NATIVE_ADAPTERS" in self._parse_keys()

    def test_has_redis_url(self) -> None:
        assert "REDIS_URL" in self._parse_keys()

    def test_has_database_url(self) -> None:
        assert "DATABASE_URL" in self._parse_keys()

    def test_has_jwt_secret(self) -> None:
        assert "JWT_SECRET" in self._parse_keys()


# ---------------------------------------------------------------------------
# scripts.run_shadow import check
# ---------------------------------------------------------------------------


class TestRunShadowImport:
    def test_module_imports_without_error(self) -> None:
        """scripts.run_shadow must be importable (no top-level side effects)."""
        import importlib
        mod = importlib.import_module("scripts.run_shadow")
        assert mod is not None

    def test_run_shadow_function_exists(self) -> None:
        from scripts.run_shadow import run_shadow
        assert callable(run_shadow)

    def test_build_strategy_params_function_exists(self) -> None:
        from scripts.run_shadow import _build_strategy_params
        assert callable(_build_strategy_params)

    def test_check_alert_thresholds_function_exists(self) -> None:
        from scripts.run_shadow import _check_alert_thresholds
        assert callable(_check_alert_thresholds)


# ---------------------------------------------------------------------------
# _build_strategy_params
# ---------------------------------------------------------------------------


class TestBuildStrategyParams:
    def test_entry_threshold_mapped(self) -> None:
        from scripts.run_shadow import _build_strategy_params
        raw = {"entry_threshold": 0.005, "exit_threshold": 0.001, "stop_loss_pct": 0.01}
        params = _build_strategy_params(raw)
        assert abs(params.entry_threshold - 0.005) < 1e-9

    def test_exit_threshold_mapped(self) -> None:
        from scripts.run_shadow import _build_strategy_params
        raw = {"entry_threshold": 0.005, "exit_threshold": 0.002, "stop_loss_pct": 0.01}
        params = _build_strategy_params(raw)
        assert abs(params.exit_threshold - 0.002) < 1e-9

    def test_unknown_keys_ignored(self) -> None:
        from scripts.run_shadow import _build_strategy_params
        raw = {
            "entry_threshold": 0.005,
            "exit_threshold": 0.001,
            "stop_loss_pct": 0.01,
            "status": "MONITOR",
            "wfe": 0.94,
            "max_position_usdt": 100,
        }
        # Should not raise
        params = _build_strategy_params(raw)
        assert params is not None

    def test_defaults_used_when_keys_missing(self) -> None:
        from scripts.run_shadow import _build_strategy_params
        params = _build_strategy_params({})
        assert params.entry_threshold == 0.005
        assert params.exit_threshold == 0.001
        assert params.stop_loss_pct == 0.01


# ---------------------------------------------------------------------------
# _check_alert_thresholds
# ---------------------------------------------------------------------------


class TestCheckAlertThresholds:
    def _make_result(self, mdd: float = 0.0, sharpe: float = 1.0) -> MagicMock:
        result = MagicMock()
        result.shadow_result.max_drawdown = mdd
        result.shadow_result.sharpe_ratio = sharpe
        return result

    def test_no_warning_when_within_thresholds(self, caplog: pytest.LogCaptureFixture) -> None:
        from scripts.run_shadow import _check_alert_thresholds
        result = self._make_result(mdd=0.02, sharpe=1.5)
        thresholds = {"max_drawdown_pct": 5.0, "min_sharpe": 0.5}
        with caplog.at_level(logging.WARNING):
            _check_alert_thresholds("test_strat", result, thresholds)
        assert "ALERT" not in caplog.text

    def test_mdd_alert_when_exceeded(self, caplog: pytest.LogCaptureFixture) -> None:
        from scripts.run_shadow import _check_alert_thresholds
        result = self._make_result(mdd=0.06, sharpe=1.5)  # 6% > 5%
        thresholds = {"max_drawdown_pct": 5.0, "min_sharpe": 0.5}
        with caplog.at_level(logging.WARNING):
            _check_alert_thresholds("test_strat", result, thresholds)
        assert "MDD" in caplog.text

    def test_sharpe_alert_when_below_threshold(self, caplog: pytest.LogCaptureFixture) -> None:
        from scripts.run_shadow import _check_alert_thresholds
        result = self._make_result(mdd=0.01, sharpe=0.3)  # 0.3 < 0.5
        thresholds = {"max_drawdown_pct": 5.0, "min_sharpe": 0.5}
        with caplog.at_level(logging.WARNING):
            _check_alert_thresholds("test_strat", result, thresholds)
        assert "Sharpe" in caplog.text


# ---------------------------------------------------------------------------
# run_shadow end-to-end (mocked ShadowRunner)
# ---------------------------------------------------------------------------


class TestRunShadow:
    def _make_mock_result(self) -> MagicMock:
        result = MagicMock()
        result.shadow_result.total_pnl = 10.0
        result.shadow_result.sharpe_ratio = 1.5
        result.shadow_result.max_drawdown = 0.01
        result.shadow_result.num_trades = 20
        result.shadow_result.win_rate = 0.6
        result.baseline_result.total_pnl = 8.0
        result.evaluation.recommendation = "APPLY: metrics improved"
        return result

    def test_run_shadow_returns_dict(self) -> None:
        from scripts.run_shadow import run_shadow
        mock_result = self._make_mock_result()
        with patch("scripts.run_shadow.ShadowRunner") as mock_runner_cls:
            instance = mock_runner_cls.return_value
            instance.evaluate_and_decide.return_value = ("APPLY", mock_result)
            instance.print_report.return_value = None
            results = run_shadow(
                config_path=_SHADOW_CONFIG,
                params_path=_ENGINE_ROOT / "config" / "strategy_params.json",
                duration_override=0.001,  # near-zero to skip hourly loop
            )
        assert isinstance(results, dict)

    def test_run_shadow_processes_monitor_strategies(self) -> None:
        from scripts.run_shadow import run_shadow
        mock_result = self._make_mock_result()
        with patch("scripts.run_shadow.ShadowRunner") as mock_runner_cls:
            instance = mock_runner_cls.return_value
            instance.evaluate_and_decide.return_value = ("MONITOR", mock_result)
            instance.print_report.return_value = None
            results = run_shadow(
                config_path=_SHADOW_CONFIG,
                params_path=_ENGINE_ROOT / "config" / "strategy_params.json",
                duration_override=0.001,
            )
        # All 4 MONITOR strategies should appear in results
        assert "triangular" in results
        assert "cex_dex" in results
        assert "cross_exchange" in results
        assert "futures_futures" in results

    def test_run_shadow_decision_propagated(self) -> None:
        from scripts.run_shadow import run_shadow
        mock_result = self._make_mock_result()
        with patch("scripts.run_shadow.ShadowRunner") as mock_runner_cls:
            instance = mock_runner_cls.return_value
            instance.evaluate_and_decide.return_value = ("REJECT", mock_result)
            instance.print_report.return_value = None
            results = run_shadow(
                config_path=_SHADOW_CONFIG,
                params_path=_ENGINE_ROOT / "config" / "strategy_params.json",
                duration_override=0.001,
            )
        assert all(v == "REJECT" for v in results.values())

    def test_run_shadow_missing_config_raises(self) -> None:
        from scripts.run_shadow import run_shadow
        with pytest.raises(FileNotFoundError):
            run_shadow(config_path=Path("/nonexistent/shadow_mode.json"))

    def test_run_shadow_missing_params_raises(self) -> None:
        from scripts.run_shadow import run_shadow
        with pytest.raises(FileNotFoundError):
            run_shadow(
                config_path=_SHADOW_CONFIG,
                params_path=Path("/nonexistent/strategy_params.json"),
            )
