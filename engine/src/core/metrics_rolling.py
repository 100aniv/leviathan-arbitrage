"""Rolling performance metrics — US-281.

Pure-function module; no state.  All inputs are plain lists of float returns.
"""
from __future__ import annotations

import math

import numpy as np


def sharpe(
    returns: list[float],
    risk_free: float = 0.0,
    periods_per_year: int = 252,
) -> float:
    """Annualised Sharpe ratio.  Returns 0.0 if std == 0 or < 2 samples."""
    if len(returns) < 2:
        return 0.0
    arr = np.array(returns, dtype=float)
    excess = arr - risk_free / periods_per_year
    std = float(np.std(excess, ddof=1))
    if std == 0.0:
        return 0.0
    return float(np.mean(excess) / std * math.sqrt(periods_per_year))


def sortino(
    returns: list[float],
    risk_free: float = 0.0,
    periods_per_year: int = 252,
) -> float:
    """Annualised Sortino ratio (downside deviation denominator).

    Returns 0.0 if downside std == 0.
    """
    if len(returns) < 2:
        return 0.0
    arr = np.array(returns, dtype=float)
    excess = arr - risk_free / periods_per_year
    downside = excess[excess < 0]
    if len(downside) == 0:
        return float("inf")
    ds_std = float(np.std(downside, ddof=1)) if len(downside) > 1 else float(abs(downside[0]))
    if ds_std == 0.0:
        return 0.0
    return float(np.mean(excess) / ds_std * math.sqrt(periods_per_year))


def calmar(returns: list[float], max_drawdown: float) -> float:
    """Calmar ratio = annualised_return / |max_drawdown|.

    Returns 0.0 when max_drawdown == 0 to avoid division by zero.
    """
    if max_drawdown == 0.0 or not returns:
        return 0.0
    annual_return = float(np.mean(returns)) * periods_per_year_default
    return annual_return / abs(max_drawdown)


periods_per_year_default = 252


def consistency(returns: list[float]) -> float:
    """Fraction of positive return periods (0.0–1.0)."""
    if not returns:
        return 0.0
    return sum(1 for r in returns if r > 0) / len(returns)
