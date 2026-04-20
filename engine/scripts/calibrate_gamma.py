"""Gamma calibration cron — Path-B v2 Day 13.

Fits power-law decay exponent gamma against SlippageFeedbackCollector history:

    Impact_decay(t) = Impact_0 * (1 + t/t_0)^(-gamma)

Usage::

    cd engine
    # Dry run against real JSONL data (no write):
    python scripts/calibrate_gamma.py --dry-run

    # Smoke test with synthetic known-gamma=0.5 data:
    python scripts/calibrate_gamma.py --dry-run --synthetic

    # Production: fit and write to engine.json:
    python scripts/calibrate_gamma.py

Gate criteria:
    R² > 0.6  AND  gamma ∈ [0.2, 1.0]  AND  ≥ 100 samples

On gate failure: log WARN, keep previous gamma unchanged.
On gate pass: atomic write to engine.json (backup created at engine.json.bak).
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import shutil
import time
from pathlib import Path
from typing import Optional

import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("calibrate_gamma")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_ENGINE_ROOT = Path(__file__).parent.parent
_ENGINE_JSON = _ENGINE_ROOT / "config" / "engine.json"
_FEEDBACK_LOG_DIR = _ENGINE_ROOT / "logs" / "slippage_feedback"

# Calibration constants
_T_0_DEFAULT = 60.0        # seconds (matches slippage_model.py)
_WINDOW_HOURS = 48         # use last 48h of records
_GRID_POINTS = 200         # gamma grid resolution
_GAMMA_GRID_MIN = 0.1      # wider grid to detect out-of-range fits
_GAMMA_GRID_MAX = 1.5


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_jsonl_records(log_dir: Path, window_hours: int = _WINDOW_HOURS) -> list[dict]:
    """Load SlippageFeedbackCollector JSONL records from the last `window_hours`."""
    cutoff = time.time() - window_hours * 3600
    records: list[dict] = []

    if not log_dir.exists():
        logger.info("slippage_feedback log dir not found: %s", log_dir)
        return records

    for jsonl_file in sorted(log_dir.glob("*.jsonl")):
        try:
            with jsonl_file.open() as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = rec.get("timestamp", 0.0)
                    if ts >= cutoff:
                        records.append(rec)
        except OSError as exc:
            logger.warning("Cannot read %s: %s", jsonl_file, exc)

    logger.info("Loaded %d records from JSONL (last %dh)", len(records), window_hours)
    return records


def _load_synthetic_records(n: int = 300, gamma: float = 0.5) -> list[dict]:
    """Generate synthetic records with known gamma for smoke-testing.

    Each record represents one trade fill.  The timestamp is when the fill
    occurred.  ``actual_bps`` reflects the market impact at that moment:

        actual_bps = Impact_0 * (1 + t/t_0)^(-gamma)

    where ``t = now - timestamp`` (age of the record at calibration time).

    Oldest records (large age) have already decayed → low bps.
    Newest records (small age) are fresh → high bps.  This is the correct
    cross-sectional structure that the fitter observes.
    """
    import random

    rng = random.Random(42)
    now = time.time()
    t_spread = _WINDOW_HOURS * 3600
    records = []
    for i in range(n):
        # age = time elapsed since this fill (large age = old record)
        age = (i / max(n - 1, 1)) * t_spread          # 0 … t_spread
        ts = now - age                                  # newest first … oldest last
        actual_bps = 50.0 * (1 + age / _T_0_DEFAULT) ** (-gamma)
        actual_bps += rng.gauss(0, 0.5)
        actual_bps = max(0.1, actual_bps)
        records.append(
            {
                "timestamp": ts,
                "exchange": "binance",
                "pair": "BTC/USDT",
                "predicted_bps": 50.0,
                "actual_bps": float(actual_bps),
            }
        )
    logger.info("Generated %d synthetic records (true gamma=%.3f)", n, gamma)
    return records


# ---------------------------------------------------------------------------
# Core fitter — numpy grid search (no scipy dependency)
# ---------------------------------------------------------------------------

def fit_gamma(
    records: list[dict],
    *,
    min_samples: int = 100,
    r2_threshold: float = 0.6,
    gamma_min: float = 0.2,
    gamma_max: float = 1.0,
    t_0: float = _T_0_DEFAULT,
    window_hours: int = _WINDOW_HOURS,
) -> Optional[tuple[float, float]]:
    """Fit power-law gamma from (timestamp, actual_bps) pairs.

    Filters records to last `window_hours` and excludes zero-valued entries.

    Returns:
        (gamma, r2) if gate passes.
        None if gate fails (insufficient data, low R², or gamma out of range).
    """
    cutoff = time.time() - window_hours * 3600

    # Filter: last window, both fields non-zero
    valid = [
        r for r in records
        if r.get("timestamp", 0.0) >= cutoff
        and float(r.get("predicted_bps", 0.0)) > 0.0
        and float(r.get("actual_bps", 0.0)) > 0.0
    ]

    if len(valid) < min_samples:
        logger.warning(
            "Insufficient samples: %d < %d (min_samples). Keeping previous gamma.",
            len(valid), min_samples,
        )
        return None

    # t = time elapsed since each trade was executed.
    # Records are stamped at fill time: a record with timestamp=T represents the
    # slippage measured right after the trade at T.  Older records (small T) have
    # had more time for the impact to decay, so they should show lower actual_bps.
    # We model: actual_bps(t) ≈ Impact_0 * (1 + t/t_0)^(-gamma)
    # where t = now - record_timestamp  (age of the measurement).
    now = time.time()
    ts_arr = np.array([float(r["timestamp"]) for r in valid])
    # Clip to non-negative (guard against future timestamps in synthetic data)
    t_arr = np.clip(now - ts_arr, 0.0, None)

    actual_arr = np.array([float(r["actual_bps"]) for r in valid])

    # Normalise: fit ratio actual / actual_mean so Impact_0 cancels
    # Model: y(t) = Impact_0 * (1 + t/t_0)^(-gamma)
    # Linearise: ln(y) = ln(Impact_0) - gamma * ln(1 + t/t_0)
    # Use log-linear OLS to get initial Impact_0 and gamma
    log_y = np.log(np.clip(actual_arr, 1e-9, None))
    x_feature = np.log(1.0 + t_arr / t_0)  # ln(1 + t/t_0)

    # OLS: [ln(Impact_0), -gamma] via least squares
    # design matrix: [1, x_feature]
    A = np.column_stack([np.ones(len(x_feature)), x_feature])
    try:
        coeffs, _, _, _ = np.linalg.lstsq(A, log_y, rcond=None)
    except np.linalg.LinAlgError as exc:
        logger.warning("OLS failed: %s. Keeping previous gamma.", exc)
        return None

    log_impact_0_ols = float(coeffs[0])
    gamma_ols = float(-coeffs[1])  # negative because log_y = ln(I0) - gamma*x

    # Refine with grid search around the OLS estimate (no scipy needed)
    gamma_lo = max(_GAMMA_GRID_MIN, gamma_ols - 0.3)
    gamma_hi = min(_GAMMA_GRID_MAX, gamma_ols + 0.3)
    gamma_grid = np.linspace(gamma_lo, gamma_hi, _GRID_POINTS)

    best_gamma = gamma_ols
    best_r2 = -np.inf

    impact_0_ols = math.exp(log_impact_0_ols)

    for g in gamma_grid:
        y_pred = impact_0_ols * (1.0 + t_arr / t_0) ** (-g)
        ss_res = float(np.sum((actual_arr - y_pred) ** 2))
        ss_tot = float(np.sum((actual_arr - actual_arr.mean()) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else -np.inf
        if r2 > best_r2:
            best_r2 = r2
            best_gamma = float(g)

    logger.info(
        "Fit result: gamma=%.4f  R²=%.4f  n=%d",
        best_gamma, best_r2, len(valid),
    )

    # Gate 1: R² quality
    if best_r2 < r2_threshold:
        logger.warning(
            "R²=%.4f < threshold=%.2f. Rejecting fit, keeping previous gamma.",
            best_r2, r2_threshold,
        )
        return None

    # Gate 2: gamma range
    if not (gamma_min <= best_gamma <= gamma_max):
        logger.warning(
            "Fitted gamma=%.4f outside [%.2f, %.2f]. Rejecting fit.",
            best_gamma, gamma_min, gamma_max,
        )
        return None

    return (best_gamma, best_r2)


# ---------------------------------------------------------------------------
# engine.json atomic write
# ---------------------------------------------------------------------------

def _read_engine_json(path: Path) -> dict:
    with path.open() as fh:
        return json.load(fh)


def _write_engine_json_atomic(path: Path, data: dict) -> None:
    """Write JSON atomically via temp file + rename (crash-safe)."""
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(path)


def write_calibrated_gamma(
    records: list[dict],
    engine_json_path: Path = _ENGINE_JSON,
    *,
    dry_run: bool = False,
    min_samples: int = 100,
    r2_threshold: float = 0.6,
    gamma_min: float = 0.2,
    gamma_max: float = 1.0,
) -> Optional[float]:
    """Fit gamma and optionally write to engine.json.

    Returns fitted gamma if gate passed, else None.
    """
    result = fit_gamma(
        records,
        min_samples=min_samples,
        r2_threshold=r2_threshold,
        gamma_min=gamma_min,
        gamma_max=gamma_max,
    )
    if result is None:
        return None

    fitted_gamma, r2 = result

    if dry_run:
        logger.info(
            "[DRY-RUN] Would write slippage.gamma=%.4f (R²=%.4f) to %s",
            fitted_gamma, r2, engine_json_path,
        )
        return fitted_gamma

    if not engine_json_path.exists():
        logger.error("engine.json not found at %s", engine_json_path)
        return None

    # Backup before any mutation
    bak = engine_json_path.with_suffix(".json.bak")
    shutil.copy2(engine_json_path, bak)
    logger.info("Backup created: %s", bak)

    config = _read_engine_json(engine_json_path)
    slippage = config.setdefault("slippage", {})

    prev_gamma = slippage.get("gamma", 0.5)
    slippage["gamma"] = round(fitted_gamma, 6)
    slippage["gamma_calibrated"] = True

    _write_engine_json_atomic(engine_json_path, config)
    logger.info(
        "Written slippage.gamma=%.4f (was %.4f), gamma_calibrated=true → %s",
        fitted_gamma, prev_gamma, engine_json_path,
    )
    return fitted_gamma


# ---------------------------------------------------------------------------
# Prediction-error monitoring (non-blocking)
# ---------------------------------------------------------------------------

def _log_prediction_error(records: list[dict], window_hours: int = _WINDOW_HOURS) -> None:
    """Log mean |actual - predicted| bps for post-48h monitoring gate."""
    cutoff = time.time() - window_hours * 3600
    deltas = [
        abs(float(r["actual_bps"]) - float(r["predicted_bps"]))
        for r in records
        if r.get("timestamp", 0.0) >= cutoff
        and float(r.get("predicted_bps", 0.0)) > 0.0
    ]
    if not deltas:
        return
    deltas_arr = np.array(deltas)
    mean_err = float(deltas_arr.mean())
    p95 = float(np.percentile(deltas_arr, 95))
    gate_ok = mean_err < 5.0 and p95 <= 20.0
    level = logging.INFO if gate_ok else logging.WARNING
    logger.log(
        level,
        "Prediction error: mean=%.2f bps  p95=%.2f bps  gate=%s",
        mean_err, p95, "PASS" if gate_ok else "WARN",
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Calibrate power-law gamma for slippage decay.")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute fit but do not write engine.json.",
    )
    p.add_argument(
        "--synthetic",
        action="store_true",
        help="Use synthetic data (true gamma=0.5) instead of JSONL logs.",
    )
    p.add_argument(
        "--engine-json",
        type=Path,
        default=_ENGINE_JSON,
        help="Path to engine.json (default: config/engine.json).",
    )
    p.add_argument(
        "--log-dir",
        type=Path,
        default=_FEEDBACK_LOG_DIR,
        help="Path to slippage_feedback JSONL log dir.",
    )
    p.add_argument(
        "--min-samples",
        type=int,
        default=100,
        help="Minimum samples required (default: 100).",
    )
    p.add_argument(
        "--r2-threshold",
        type=float,
        default=0.6,
        help="Minimum R² to accept fit (default: 0.6).",
    )
    p.add_argument(
        "--gamma-min",
        type=float,
        default=0.2,
    )
    p.add_argument(
        "--gamma-max",
        type=float,
        default=1.0,
    )
    return p


def main() -> None:
    args = _build_parser().parse_args()

    if args.synthetic:
        records = _load_synthetic_records()
    else:
        records = _load_jsonl_records(args.log_dir)

    _log_prediction_error(records)

    write_calibrated_gamma(
        records=records,
        engine_json_path=args.engine_json,
        dry_run=args.dry_run,
        min_samples=args.min_samples,
        r2_threshold=args.r2_threshold,
        gamma_min=args.gamma_min,
        gamma_max=args.gamma_max,
    )


if __name__ == "__main__":
    main()
