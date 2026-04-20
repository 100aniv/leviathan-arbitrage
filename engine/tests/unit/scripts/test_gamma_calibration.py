"""Unit tests for engine/scripts/calibrate_gamma.py — Day 13.

Tests are ordered: synthetic fit, sparse guard, R² reject, range reject, write roundtrip.
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path
from typing import Any

import pytest

# Make engine/scripts importable without installing the package
_SCRIPTS_DIR = Path(__file__).parent.parent.parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import calibrate_gamma as cg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_records(
    n: int,
    gamma: float = 0.5,
    t0: float = 60.0,
    impact_0: float = 50.0,
    noise: float = 0.5,
    t_spread_s: float = 3600.0,
) -> list[dict[str, Any]]:
    """Generate synthetic (predicted, actual) pairs following power-law decay.

    Each record is one trade fill.  ``age = now - timestamp`` is the time elapsed
    since the fill occurred.  The fitter uses ``t = age`` in:

        actual_bps = Impact_0 * (1 + age/t0)^(-gamma)

    So newer records (small age) have high bps, older records (large age) have low bps.
    """
    import random
    rng = random.Random(42)
    now = time.time()
    records = []
    for i in range(n):
        # age increases with i: record 0 is newest (age≈0), record n-1 is oldest
        age = (i / max(n - 1, 1)) * t_spread_s
        ts = now - age
        actual_bps = impact_0 * (1 + age / t0) ** (-gamma)
        predicted_bps = impact_0
        actual_bps += rng.gauss(0, noise)
        actual_bps = max(0.1, actual_bps)
        records.append(
            {
                "timestamp": ts,
                "exchange": "binance",
                "pair": "BTC/USDT",
                "predicted_bps": float(predicted_bps),
                "actual_bps": float(actual_bps),
            }
        )
    return records


# ---------------------------------------------------------------------------
# Test 1: synthetic data → recovers known gamma ≈ 0.5 ± 0.05
# ---------------------------------------------------------------------------

class TestSyntheticFit:
    def test_recovers_known_gamma(self) -> None:
        records = _make_records(n=300, gamma=0.5)
        result = cg.fit_gamma(records, min_samples=100)
        assert result is not None, "Expected fit to succeed on clean synthetic data"
        fitted_gamma, r2 = result
        assert abs(fitted_gamma - 0.5) <= 0.05, (
            f"Expected gamma ≈ 0.50 ± 0.05, got {fitted_gamma:.4f}"
        )
        assert r2 > 0.6, f"Expected R² > 0.6, got {r2:.4f}"


# ---------------------------------------------------------------------------
# Test 2: sparse data (<100 samples) → returns None
# ---------------------------------------------------------------------------

class TestSparseDataGuard:
    def test_sparse_returns_none(self) -> None:
        records = _make_records(n=50, gamma=0.5)
        result = cg.fit_gamma(records, min_samples=100)
        assert result is None, "Expected None for < 100 samples"

    def test_exactly_at_threshold_succeeds(self) -> None:
        records = _make_records(n=100, gamma=0.5)
        result = cg.fit_gamma(records, min_samples=100)
        # 100 clean samples should fit
        assert result is not None


# ---------------------------------------------------------------------------
# Test 3: R² < 0.4 → rejects fit, function returns None
# ---------------------------------------------------------------------------

class TestR2Reject:
    def test_noisy_data_rejected(self) -> None:
        """Uniform random actual_bps has no decay structure → R² ≈ 0 → rejected."""
        import random
        rng = random.Random(99)
        now = time.time()
        records = [
            {
                "timestamp": now - i,
                "exchange": "binance",
                "pair": "BTC/USDT",
                "predicted_bps": 50.0,
                "actual_bps": rng.uniform(1.0, 200.0),  # pure noise
            }
            for i in range(200)
        ]
        result = cg.fit_gamma(records, min_samples=100, r2_threshold=0.4)
        # With pure noise, R² will be well below 0.4
        assert result is None, "Expected None for pure-noise data (R² < 0.4)"


# ---------------------------------------------------------------------------
# Test 4: gamma outside [0.2, 1.0] → rejects
# ---------------------------------------------------------------------------

class TestGammaRangeReject:
    def test_extreme_gamma_rejected(self) -> None:
        """Craft records that would fit to gamma < 0.2 or > 1.0 — should reject."""
        # gamma=3.0 decays extremely fast — outside valid range
        records = _make_records(n=300, gamma=3.0, noise=0.01)
        result = cg.fit_gamma(
            records,
            min_samples=100,
            gamma_min=0.2,
            gamma_max=1.0,
            r2_threshold=0.4,
        )
        # gamma=3.0 is outside [0.2, 1.0] → should be rejected
        assert result is None, (
            "Expected None when fitted gamma falls outside [0.2, 1.0]"
        )


# ---------------------------------------------------------------------------
# Test 5: successful fit writes slippage.gamma + gamma_calibrated=true to engine.json
# ---------------------------------------------------------------------------

class TestWriteEngineJson:
    def test_write_roundtrip(self, tmp_path: Path) -> None:
        engine_json = tmp_path / "engine.json"
        # Seed with minimal valid config
        engine_json.write_text(
            json.dumps(
                {
                    "mode": "paper",
                    "slippage": {
                        "gamma": 0.5,
                        "gamma_calibrated": False,
                        "k_default": 1.0,
                        "t0": 60.0,
                    },
                }
            )
        )

        records = _make_records(n=300, gamma=0.5)
        cg.write_calibrated_gamma(
            records=records,
            engine_json_path=engine_json,
            dry_run=False,
            min_samples=100,
            r2_threshold=0.6,
            gamma_min=0.2,
            gamma_max=1.0,
        )

        # Backup must exist
        bak = tmp_path / "engine.json.bak"
        assert bak.exists(), "engine.json.bak must be created before write"

        # Re-read and validate
        result = json.loads(engine_json.read_text())
        assert result["slippage"]["gamma_calibrated"] is True
        fitted = result["slippage"]["gamma"]
        assert abs(fitted - 0.5) <= 0.05, (
            f"engine.json gamma should be ≈ 0.50 ± 0.05, got {fitted}"
        )
        # Schema preserved
        assert "mode" in result
        assert "k_default" in result["slippage"]

    def test_dry_run_does_not_write(self, tmp_path: Path) -> None:
        engine_json = tmp_path / "engine.json"
        original = {
            "mode": "paper",
            "slippage": {"gamma": 0.5, "gamma_calibrated": False},
        }
        engine_json.write_text(json.dumps(original))

        records = _make_records(n=300, gamma=0.45)
        cg.write_calibrated_gamma(
            records=records,
            engine_json_path=engine_json,
            dry_run=True,
            min_samples=100,
        )

        # File must be unchanged
        result = json.loads(engine_json.read_text())
        assert result["slippage"]["gamma"] == 0.5
        assert result["slippage"]["gamma_calibrated"] is False
        # No backup created in dry-run
        assert not (tmp_path / "engine.json.bak").exists()
