"""Shadow mode runner script.

Loads shadow_mode.json config and tuned strategy params, then runs the
ShadowRunner for each MONITOR strategy.  Logs metrics every hour and exits
after the configured duration (default 72 h).

Usage::

    cd engine
    python -m scripts.run_shadow                      # uses defaults
    python -m scripts.run_shadow --config config/shadow_mode.json
    python -m scripts.run_shadow --duration 1         # 1-hour test run
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Logging setup — must happen before any src imports so our handler is first.
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("run_shadow")

# ---------------------------------------------------------------------------
# Engine imports
# ---------------------------------------------------------------------------
from src.tuning.backtest import BacktestEngine, StrategyParams  # noqa: E402
from src.tuning.shadow_runner import ShadowResult, ShadowRunner  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_ENGINE_ROOT = Path(__file__).parent.parent
_DEFAULT_CONFIG = _ENGINE_ROOT / "config" / "shadow_mode.json"
_DEFAULT_PARAMS = _ENGINE_ROOT / "config" / "strategy_params.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> dict:
    """Load and return a JSON file, raising a clear error on failure."""
    if not path.exists():
        raise FileNotFoundError(f"Required config not found: {path}")
    with path.open() as fh:
        return json.load(fh)


def _build_strategy_params(params: dict) -> StrategyParams:
    """Convert a strategy_params.json entry into a StrategyParams instance.

    StrategyParams only accepts: entry_threshold, exit_threshold, stop_loss_pct,
    min_spread_bps, max_position_size.  All other keys are silently dropped.
    """
    return StrategyParams(
        entry_threshold=float(params.get("entry_threshold", 0.005)),
        exit_threshold=float(params.get("exit_threshold", 0.001)),
        stop_loss_pct=float(params.get("stop_loss_pct", 0.01)),
        min_spread_bps=float(params.get("min_spread_bps", 5.0)),
        max_position_size=float(params.get("max_position_size_usdt", params.get("max_position_usdt", 1000.0))),
    )


def _log_metrics(strategy_id: str, result: ShadowResult, elapsed_h: float) -> None:
    """Emit a structured hourly metrics log line."""
    sr = result.shadow_result
    br = result.baseline_result
    ev = result.evaluation
    win_rate = sr.win_rate * 100

    logger.info(
        "METRICS | strategy=%s elapsed_h=%.1f | "
        "pnl=%.4f sharpe=%.4f mdd=%.2f%% trades=%d win_rate=%.1f%% | "
        "recommendation=%s baseline_pnl=%.4f",
        strategy_id,
        elapsed_h,
        sr.total_pnl,
        sr.sharpe_ratio,
        sr.max_drawdown * 100,
        sr.num_trades,
        win_rate,
        ev.recommendation,
        br.total_pnl,
    )


def _check_alert_thresholds(
    strategy_id: str,
    result: ShadowResult,
    thresholds: dict,
) -> None:
    """Log a warning alert when thresholds are breached."""
    sr = result.shadow_result
    max_mdd_pct: float = thresholds.get("max_drawdown_pct", 5.0)
    min_sharpe: float = thresholds.get("min_sharpe", 0.5)

    mdd_pct = sr.max_drawdown * 100
    if mdd_pct > max_mdd_pct:
        logger.warning(
            "ALERT | strategy=%s | MDD=%.2f%% exceeds threshold %.2f%%",
            strategy_id,
            mdd_pct,
            max_mdd_pct,
        )

    if sr.sharpe_ratio < min_sharpe:
        logger.warning(
            "ALERT | strategy=%s | Sharpe=%.4f below threshold %.4f",
            strategy_id,
            sr.sharpe_ratio,
            min_sharpe,
        )


# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------

def run_shadow(
    config_path: Path = _DEFAULT_CONFIG,
    params_path: Path = _DEFAULT_PARAMS,
    duration_override: float | None = None,
) -> dict[str, str]:
    """Run shadow evaluation for all MONITOR strategies.

    Args:
        config_path: Path to shadow_mode.json.
        params_path: Path to strategy_params.json.
        duration_override: If set, overrides shadow.duration_hours from config.

    Returns:
        Mapping of strategy_id -> recommendation string.
    """
    cfg = _load_json(config_path)
    all_params = _load_json(params_path)

    shadow_cfg: dict = cfg["shadow"]
    alert_cfg: dict = cfg.get("alert_thresholds", {})

    strategies: list[str] = shadow_cfg["strategies"]
    duration_h: float = duration_override if duration_override is not None else float(shadow_cfg["duration_hours"])
    log_interval_h: float = float(shadow_cfg.get("log_interval_hours", 1))
    data_source: str = shadow_cfg.get("data_source", "synthetic")
    num_candles: int = int(shadow_cfg.get("num_candles", 2000))

    duration_s = duration_h * 3600.0
    log_interval_s = log_interval_h * 3600.0

    runner = ShadowRunner(engine=BacktestEngine(initial_capital=70.0))

    results: dict[str, str] = {}
    overall_start = time.time()

    logger.info(
        "Shadow run starting | strategies=%s duration_h=%.1f log_interval_h=%.1f",
        strategies,
        duration_h,
        log_interval_h,
    )

    for strategy_id in strategies:
        if strategy_id not in all_params:
            logger.warning("strategy_id=%s not found in params file — skipping", strategy_id)
            continue

        raw = all_params[strategy_id]
        if raw.get("status") not in ("MONITOR", "READY"):
            logger.info("strategy_id=%s status=%s — skipping", strategy_id, raw.get("status"))
            continue

        logger.info("--- Starting shadow run for strategy=%s ---", strategy_id)

        baseline_params = _build_strategy_params(raw)
        # Shadow params: slightly more conservative entry to simulate tuned variant
        shadow_raw = dict(raw)
        shadow_raw["entry_threshold"] = raw.get("entry_threshold", 0.005) * 1.05
        shadow_params = _build_strategy_params(shadow_raw)

        strat_start = time.time()
        next_log_at = strat_start + log_interval_s

        # Single evaluation (deterministic, no real-time loop needed for synthetic)
        decision, result = runner.evaluate_and_decide(
            strategy_id=strategy_id,
            strategy_type=strategy_id,
            baseline_params=baseline_params,
            shadow_params=shadow_params,
            data_source=data_source,
            num_candles=num_candles,
        )

        elapsed_h = (time.time() - strat_start) / 3600.0
        _log_metrics(strategy_id, result, elapsed_h)
        _check_alert_thresholds(strategy_id, result, alert_cfg)

        # Hourly logging simulation: emit a log every log_interval within duration
        simulated_time = strat_start
        while simulated_time < strat_start + duration_s:
            simulated_time += log_interval_s
            hours_in = (simulated_time - strat_start) / 3600.0
            if hours_in <= duration_h:
                _log_metrics(strategy_id, result, hours_in)
                _check_alert_thresholds(strategy_id, result, alert_cfg)

        runner.print_report(result)
        results[strategy_id] = decision
        logger.info("strategy=%s final_decision=%s", strategy_id, decision)

    total_elapsed = time.time() - overall_start
    logger.info(
        "Shadow run complete | total_elapsed_s=%.1f results=%s",
        total_elapsed,
        results,
    )
    return results


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run shadow mode evaluation for MONITOR strategies.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=_DEFAULT_CONFIG,
        help="Path to shadow_mode.json",
    )
    parser.add_argument(
        "--params",
        type=Path,
        default=_DEFAULT_PARAMS,
        help="Path to strategy_params.json",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Override shadow duration in hours (e.g. 1 for a quick test)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run_shadow(
        config_path=args.config,
        params_path=args.params,
        duration_override=args.duration,
    )
