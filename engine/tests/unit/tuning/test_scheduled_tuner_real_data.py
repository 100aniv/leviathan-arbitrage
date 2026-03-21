"""US-298: ScheduledTuner real-data path (InsufficientDataError + _check_sufficient_real_data).

Verifies that:
1. InsufficientDataError class exists in scheduled_tuner module
2. _check_sufficient_real_data raises InsufficientDataError when data is below MIN_ROWS
3. _check_sufficient_real_data raises InsufficientDataError when DATABASE_URL is unset
4. _optimize_strategy catches InsufficientDataError and returns status=INSUFFICIENT_DATA
5. _optimize_strategy result contains data_type="real_timescaledb" on insufficient data
6. TUNER_DATA_SOURCE=timescaledb triggers the real-data path in _optimize_strategy
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from src.tuning.scheduled_tuner import InsufficientDataError, ScheduledTuner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tuner(data_source: str = "synthetic") -> ScheduledTuner:
    """Create a ScheduledTuner with activation filter disabled."""
    with patch.object(ScheduledTuner, "_load_activation", return_value=None):
        return ScheduledTuner(strategies=["cross_exchange"], n_trials=1, data_source=data_source)


def _make_mock_ohlcv(length: int) -> MagicMock:
    ohlcv = MagicMock()
    ohlcv.length = length
    return ohlcv


# ---------------------------------------------------------------------------
# Tests: InsufficientDataError class
# ---------------------------------------------------------------------------


class TestInsufficientDataError:
    def test_class_exists_in_module(self):
        """US-298: InsufficientDataError must be importable from scheduled_tuner."""
        import src.tuning.scheduled_tuner as mod
        assert hasattr(mod, "InsufficientDataError")

    def test_is_subclass_of_runtime_error(self):
        """US-298: InsufficientDataError must be a RuntimeError subclass."""
        assert issubclass(InsufficientDataError, RuntimeError)

    def test_can_be_raised_and_caught(self):
        """InsufficientDataError can be raised with a message and caught normally."""
        with pytest.raises(InsufficientDataError, match="Only 10 hourly rows"):
            raise InsufficientDataError("Only 10 hourly rows available; need at least 72 (3 days)")


# ---------------------------------------------------------------------------
# Tests: _check_sufficient_real_data
# ---------------------------------------------------------------------------


class TestCheckSufficientRealData:
    def test_raises_when_database_url_not_set(self, monkeypatch):
        """US-298: raises InsufficientDataError immediately when DATABASE_URL is unset."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        tuner = _make_tuner(data_source="timescaledb")
        mock_optimizer = MagicMock()

        with pytest.raises(InsufficientDataError, match="DATABASE_URL not set"):
            tuner._check_sufficient_real_data(mock_optimizer, "cross_exchange")

    def test_raises_when_row_count_below_min(self, monkeypatch):
        """US-298: raises InsufficientDataError when ohlcv.length < MIN_ROWS (default 72)."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
        monkeypatch.setenv("TUNER_MIN_REAL_DATA_ROWS", "72")
        tuner = _make_tuner(data_source="timescaledb")
        mock_optimizer = MagicMock()

        insufficient_ohlcv = _make_mock_ohlcv(length=10)

        # ThreadPoolExecutor is imported locally inside _check_sufficient_real_data,
        # so patch via concurrent.futures module.
        with patch("concurrent.futures.ThreadPoolExecutor") as mock_pool_cls:
            mock_pool = MagicMock()
            mock_pool_cls.return_value.__enter__.return_value = mock_pool
            mock_future = MagicMock()
            mock_future.result.return_value = insufficient_ohlcv
            mock_pool.submit.return_value = mock_future

            with pytest.raises(InsufficientDataError, match="Only 10 hourly rows"):
                tuner._check_sufficient_real_data(mock_optimizer, "cross_exchange")

    def test_does_not_raise_when_row_count_meets_minimum(self, monkeypatch):
        """US-298: does NOT raise when ohlcv.length >= MIN_ROWS."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
        monkeypatch.setenv("TUNER_MIN_REAL_DATA_ROWS", "72")
        tuner = _make_tuner(data_source="timescaledb")
        mock_optimizer = MagicMock()

        sufficient_ohlcv = _make_mock_ohlcv(length=72)

        with patch("concurrent.futures.ThreadPoolExecutor") as mock_pool_cls:
            mock_pool = MagicMock()
            mock_pool_cls.return_value.__enter__.return_value = mock_pool
            mock_future = MagicMock()
            mock_future.result.return_value = sufficient_ohlcv
            mock_pool.submit.return_value = mock_future

            # Should complete without raising
            tuner._check_sufficient_real_data(mock_optimizer, "cross_exchange")

    def test_custom_min_rows_env_var_respected(self, monkeypatch):
        """US-298: TUNER_MIN_REAL_DATA_ROWS env var overrides the default 72."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
        monkeypatch.setenv("TUNER_MIN_REAL_DATA_ROWS", "10")
        tuner = _make_tuner(data_source="timescaledb")
        mock_optimizer = MagicMock()

        # 9 rows < 10 minimum → should raise
        low_ohlcv = _make_mock_ohlcv(length=9)

        with patch("concurrent.futures.ThreadPoolExecutor") as mock_pool_cls:
            mock_pool = MagicMock()
            mock_pool_cls.return_value.__enter__.return_value = mock_pool
            mock_future = MagicMock()
            mock_future.result.return_value = low_ohlcv
            mock_pool.submit.return_value = mock_future

            with pytest.raises(InsufficientDataError):
                tuner._check_sufficient_real_data(mock_optimizer, "cross_exchange")


# ---------------------------------------------------------------------------
# Tests: _optimize_strategy catches InsufficientDataError
# ---------------------------------------------------------------------------


class TestOptimizeStrategyInsufficientData:
    def test_returns_insufficient_data_status_on_error(self, monkeypatch):
        """US-298: _optimize_strategy returns status=INSUFFICIENT_DATA when check raises."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
        tuner = _make_tuner(data_source="timescaledb")

        with patch.object(
            tuner,
            "_check_sufficient_real_data",
            side_effect=InsufficientDataError("Only 5 rows; need 72"),
        ):
            result = tuner._optimize_strategy("cross_exchange")

        assert result["status"] == "INSUFFICIENT_DATA"

    def test_returns_error_message_on_insufficient_data(self, monkeypatch):
        """US-298: INSUFFICIENT_DATA result includes the error message."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
        tuner = _make_tuner(data_source="timescaledb")

        with patch.object(
            tuner,
            "_check_sufficient_real_data",
            side_effect=InsufficientDataError("Only 5 rows; need 72"),
        ):
            result = tuner._optimize_strategy("cross_exchange")

        assert "error" in result
        assert "5 rows" in result["error"]

    def test_returns_data_type_real_timescaledb_on_insufficient_data(self, monkeypatch):
        """US-298: INSUFFICIENT_DATA result has data_type=real_timescaledb."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
        tuner = _make_tuner(data_source="timescaledb")

        with patch.object(
            tuner,
            "_check_sufficient_real_data",
            side_effect=InsufficientDataError("Only 5 rows; need 72"),
        ):
            result = tuner._optimize_strategy("cross_exchange")

        assert result.get("data_type") == "real_timescaledb"

    def test_check_not_called_when_data_source_is_synthetic(self):
        """US-298: _check_sufficient_real_data is NOT called for synthetic data source."""
        tuner = _make_tuner(data_source="synthetic")

        mock_study = MagicMock()
        mock_study.best_params = {"min_spread_bps": 5.0}
        mock_study.best_value = 1.0

        mock_engine = MagicMock()
        mock_engine.run_with_synthetic_data.return_value = MagicMock(sharpe_ratio=1.0)
        mock_wfo = MagicMock()
        mock_wfo._engine = mock_engine

        with patch("src.tuning.scheduled_tuner.optuna") as mock_optuna, \
             patch("src.tuning.scheduled_tuner.WalkForwardOptimizer", return_value=mock_wfo), \
             patch.object(tuner, "_check_sufficient_real_data") as mock_check:

            mock_optuna.create_study.return_value = mock_study
            tuner._optimize_strategy("cross_exchange")

        mock_check.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: TUNER_DATA_SOURCE=timescaledb triggers real-data path
# ---------------------------------------------------------------------------


class TestDataSourceRouting:
    def test_timescaledb_data_source_stored_on_tuner(self, monkeypatch):
        """US-298: ScheduledTuner stores data_source=timescaledb from constructor arg."""
        tuner = _make_tuner(data_source="timescaledb")
        assert tuner.data_source == "timescaledb"

    def test_timescaledb_data_source_read_from_env(self, monkeypatch):
        """US-298: TUNER_DATA_SOURCE env var sets data_source on ScheduledTuner."""
        monkeypatch.setenv("TUNER_DATA_SOURCE", "timescaledb")
        with patch.object(ScheduledTuner, "_load_activation", return_value=None):
            tuner = ScheduledTuner(strategies=["cross_exchange"], n_trials=1)
        assert tuner.data_source == "timescaledb"

    def test_synthetic_is_default_data_source(self, monkeypatch):
        """US-298: data_source defaults to synthetic when env var is not set."""
        monkeypatch.delenv("TUNER_DATA_SOURCE", raising=False)
        with patch.object(ScheduledTuner, "_load_activation", return_value=None):
            tuner = ScheduledTuner(strategies=["cross_exchange"], n_trials=1)
        assert tuner.data_source == "synthetic"

    def test_timescaledb_path_calls_check_before_optuna(self, monkeypatch):
        """US-298: with timescaledb source, preflight check runs before Optuna study."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
        tuner = _make_tuner(data_source="timescaledb")

        call_order: list[str] = []

        def mock_check(optimizer, strategy):
            call_order.append("check")

        mock_study = MagicMock()
        mock_study.best_params = {"min_spread_bps": 5.0}
        mock_study.best_value = 1.0

        mock_engine = MagicMock()
        mock_engine.run_with_synthetic_data.return_value = MagicMock(sharpe_ratio=1.0)
        mock_wfo = MagicMock()
        mock_wfo._engine = mock_engine

        def mock_create_study(*args, **kwargs):
            call_order.append("optuna_study")
            return mock_study

        with patch.object(tuner, "_check_sufficient_real_data", side_effect=mock_check), \
             patch("src.tuning.scheduled_tuner.optuna") as mock_optuna, \
             patch("src.tuning.scheduled_tuner.WalkForwardOptimizer", return_value=mock_wfo):

            mock_optuna.create_study.side_effect = mock_create_study
            tuner._optimize_strategy("cross_exchange")

        assert call_order.index("check") < call_order.index("optuna_study"), (
            "Preflight check must run before Optuna study is created"
        )
