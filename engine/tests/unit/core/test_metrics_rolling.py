"""Tests for rolling performance metrics — US-281."""
from __future__ import annotations

import math

import pytest

from src.core.metrics_rolling import calmar, consistency, sharpe, sortino


# ---------------------------------------------------------------------------
# Sharpe
# ---------------------------------------------------------------------------

class TestSharpe:
    def test_sharpe_known_sequence(self) -> None:
        """Returns [0.01] * 252 → Sharpe = inf (std=0), but using ddof=1, need 2 different."""
        returns = [0.01 if i % 2 == 0 else 0.02 for i in range(252)]
        s = sharpe(returns)
        assert s > 0.0

    def test_sharpe_zero_std_returns_zero(self) -> None:
        """Identical returns → std=0 → Sharpe returns 0.0."""
        returns = [0.01] * 50
        assert sharpe(returns) == pytest.approx(0.0)

    def test_sharpe_single_return_returns_zero(self) -> None:
        assert sharpe([0.05]) == pytest.approx(0.0)

    def test_sharpe_empty_returns_zero(self) -> None:
        assert sharpe([]) == pytest.approx(0.0)

    def test_sharpe_positive_for_positive_mean(self) -> None:
        import numpy as np
        rng = np.random.default_rng(42)
        returns = (rng.normal(0.001, 0.01, 252)).tolist()
        # Mostly positive mean → Sharpe should be positive
        if sum(returns) > 0:
            assert sharpe(returns) > 0.0


# ---------------------------------------------------------------------------
# Sortino
# ---------------------------------------------------------------------------

class TestSortino:
    def test_sortino_downside_only(self) -> None:
        """All-positive returns → no downside → returns inf."""
        returns = [0.01] * 10 + [0.02] * 10
        result = sortino(returns)
        assert result == float("inf")

    def test_sortino_with_downside(self) -> None:
        returns = [0.01, -0.01, 0.02, -0.02, 0.015]
        result = sortino(returns)
        assert result != float("inf")
        # Positive mean → positive Sortino
        if sum(returns) > 0:
            assert result > 0.0

    def test_sortino_empty_returns_zero(self) -> None:
        assert sortino([]) == pytest.approx(0.0)

    def test_sortino_single_returns_zero(self) -> None:
        assert sortino([0.01]) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Calmar
# ---------------------------------------------------------------------------

class TestCalmar:
    def test_calmar_zero_dd_returns_zero(self) -> None:
        """max_drawdown == 0 → returns 0.0 (no division by zero)."""
        assert calmar([0.01, 0.02, 0.01], max_drawdown=0.0) == pytest.approx(0.0)

    def test_calmar_positive_return_positive_dd(self) -> None:
        returns = [0.01] * 252  # 1% daily mean
        result = calmar(returns, max_drawdown=0.10)
        assert result > 0.0

    def test_calmar_empty_returns_zero(self) -> None:
        assert calmar([], max_drawdown=0.05) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Consistency
# ---------------------------------------------------------------------------

class TestConsistency:
    def test_consistency_all_positive(self) -> None:
        assert consistency([0.01, 0.02, 0.005]) == pytest.approx(1.0)

    def test_consistency_all_negative(self) -> None:
        assert consistency([-0.01, -0.02]) == pytest.approx(0.0)

    def test_consistency_mixed(self) -> None:
        assert consistency([1.0, -1.0, 1.0, -1.0]) == pytest.approx(0.5)

    def test_consistency_empty_returns_zero(self) -> None:
        assert consistency([]) == pytest.approx(0.0)
