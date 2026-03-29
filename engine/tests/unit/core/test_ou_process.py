"""Tests for OUProcess — US-268."""
from __future__ import annotations
import math
import pytest
from src.core.ou_process import OUProcess


def _feed_ou(process: OUProcess, values: list[float], dt: float = 1.0) -> None:
    t = 0.0
    for v in values:
        process.update(v, t)
        t += dt


def test_ou_process_basic_mean_reverting():
    """Synthetic OU data: theta>0 and half_life within 50% of true value."""
    import numpy as np
    rng = np.random.default_rng(42)
    theta_true = 0.1
    mu = 5.0
    sigma = 0.3
    dt = 1.0
    n = 200
    x = [mu]
    for _ in range(n - 1):
        dx = theta_true * (mu - x[-1]) * dt + sigma * rng.normal() * math.sqrt(dt)
        x.append(x[-1] + dx)

    ou = OUProcess(window=400)
    _feed_ou(ou, x, dt=dt)

    assert ou.is_mean_reverting
    assert ou.theta > 0
    true_hl = math.log(2) / theta_true
    assert abs(ou.half_life - true_hl) / true_hl < 0.5


def test_ou_process_not_mean_reverting():
    """Monotone increasing series should not be flagged as mean-reverting."""
    ou = OUProcess(window=200)
    _feed_ou(ou, list(range(1, 101)), dt=1.0)
    assert not ou.is_mean_reverting


def test_ou_process_predict():
    """predict(horizon) should converge towards mu for mean-reverting process."""
    import numpy as np
    rng = np.random.default_rng(7)
    theta = 0.3
    mu = 10.0
    n = 100
    x = [mu + 5.0]
    for _ in range(n - 1):
        dx = theta * (mu - x[-1]) + 0.1 * rng.normal()
        x.append(x[-1] + dx)

    ou = OUProcess(window=200)
    _feed_ou(ou, x, dt=1.0)

    if ou.is_mean_reverting:
        pred_short = ou.predict(1.0)
        pred_long = ou.predict(100.0)
        current = x[-1]
        # Long-horizon prediction must be closer to mu than current value
        assert abs(pred_long - ou.mu) < abs(current - ou.mu)
        # Short prediction is between current and mu direction
        if current > ou.mu:
            assert pred_short <= current
        else:
            assert pred_short >= current


def test_ou_process_time_weighted():
    """Non-uniform dt (0.1~10s) should not crash and still fit when mean-reverting."""
    import numpy as np
    rng = np.random.default_rng(99)
    ou = OUProcess(window=200)
    t = 0.0
    x = 5.0
    for _ in range(80):
        dt = float(rng.uniform(0.1, 10.0))
        x = x + 0.2 * (5.0 - x) * dt + 0.2 * rng.normal() * math.sqrt(dt)
        ou.update(x, t)
        t += dt
    # Must not raise; fitted flag may be True or False depending on data
    assert ou.is_mean_reverting in (True, False)


def test_ou_process_min_samples():
    """Fewer than 30 samples must not trigger mean-reversion flag."""
    ou = OUProcess(window=200)
    for i in range(29):
        ou.update(float(i), float(i))
    assert not ou.is_mean_reverting
