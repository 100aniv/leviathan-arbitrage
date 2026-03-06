"""Auto-tuning CLI — walk-forward optimization with shadow mode validation.

Usage:
    python -m src.cli.tune_cli --data synthetic --trials 50
    python -m src.cli.tune_cli --data synthetic --trials 100 --shadow
    python -m src.cli.tune_cli --data ./data.csv --strategy cross_exchange
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from src.tuning.backtest import BacktestEngine, StrategyParams
from src.tuning.file_data_loader import FileDataLoader, generate_synthetic_ohlcv
from src.tuning.optimizer import TunerConfig, WalkForwardOptimizer
from src.tuning.param_bridge import params_to_strategy_config
from src.tuning.shadow_runner import ShadowRunner


def _run_tune(args: argparse.Namespace) -> dict:
    """Run walk-forward optimization with optional shadow evaluation."""
    loader = FileDataLoader()

    if args.data == "synthetic":
        ohlcv = generate_synthetic_ohlcv(
            num_candles=args.candles,
            spread_injection_rate=0.15,
            spread_injection_bps=30.0,
        )
    else:
        ohlcv = loader.load(args.data)

    config = TunerConfig(
        n_trials=args.trials,
        train_periods=args.train_periods,
        val_periods=args.val_periods,
    )

    engine = BacktestEngine(
        initial_capital=args.capital,
        fee_rate=args.fee_rate,
    )

    optimizer = WalkForwardOptimizer(config=config, engine=engine)

    print(f"\n{'=' * 60}")
    print(f"LEVIATHAN Auto-Tuner")
    print(f"{'=' * 60}")
    print(f"Data:       {args.data} ({ohlcv.length} candles)")
    print(f"Trials:     {args.trials}")
    print(f"Folds:      train={args.train_periods} val={args.val_periods}")
    print(f"Capital:    ${args.capital:.2f}")
    print(f"Strategy:   {args.strategy}")

    start = time.time()
    results = optimizer.optimize(ohlcv, loader)
    optimize_time = time.time() - start

    if not results:
        print("\nInsufficient data for walk-forward optimization.")
        return {"error": "insufficient_data"}

    # Print fold results
    print(f"\n{'─' * 60}")
    print(f"Optimization Results ({len(results)} folds, {optimize_time:.1f}s)")
    print(f"{'─' * 60}")

    fold_data = []
    for i, res in enumerate(results):
        tp = res.best_params
        tr = res.train_result
        vr = res.val_result
        val_ok = "+" if vr.sharpe_ratio > 0 else "-"
        print(
            f"  Fold {i + 1:2d}: "
            f"train_pnl=${tr.total_pnl:8.4f} train_sharpe={tr.sharpe_ratio:7.4f} | "
            f"val_pnl=${vr.total_pnl:8.4f} val_sharpe={vr.sharpe_ratio:7.4f} [{val_ok}]"
        )
        fold_data.append({
            "fold": i + 1,
            "train_pnl": tr.total_pnl,
            "train_sharpe": tr.sharpe_ratio,
            "val_pnl": vr.total_pnl,
            "val_sharpe": vr.sharpe_ratio,
            "params": {
                "min_spread_bps": tp.min_spread_bps,
                "max_position_size": tp.max_position_size,
                "entry_threshold": tp.entry_threshold,
                "exit_threshold": tp.exit_threshold,
                "stop_loss_pct": tp.stop_loss_pct,
            },
        })

    # Select best fold
    best = optimizer.select_best_fold(results)
    if best is None:
        print("\nNo valid fold found.")
        return {"error": "no_valid_fold"}

    bp = best.best_params
    print(f"\n{'─' * 60}")
    print(f"Best Fold (by validation Sharpe)")
    print(f"{'─' * 60}")
    print(f"  Val Sharpe:     {best.val_result.sharpe_ratio:.4f}")
    print(f"  Val PnL:        ${best.val_result.total_pnl:.4f}")
    print(f"  Val Win Rate:   {best.val_result.win_rate * 100:.1f}%")
    print(f"  Val MDD:        {best.val_result.max_drawdown * 100:.2f}%")
    print(f"  Parameters:")
    print(f"    min_spread_bps:    {bp.min_spread_bps:.2f}")
    print(f"    max_position_size: {bp.max_position_size:.2f}")
    print(f"    entry_threshold:   {bp.entry_threshold:.6f}")
    print(f"    exit_threshold:    {bp.exit_threshold:.6f}")
    print(f"    stop_loss_pct:     {bp.stop_loss_pct:.4f}")

    # Strategy config mapping
    config_dict = params_to_strategy_config(bp, args.strategy)
    print(f"\n  Mapped Strategy Config:")
    for k, v in config_dict.items():
        print(f"    {k}: {v}")

    result_data = {
        "data_source": args.data,
        "candles": ohlcv.length,
        "trials": args.trials,
        "folds": fold_data,
        "best_params": {
            "min_spread_bps": bp.min_spread_bps,
            "max_position_size": bp.max_position_size,
            "entry_threshold": bp.entry_threshold,
            "exit_threshold": bp.exit_threshold,
            "stop_loss_pct": bp.stop_loss_pct,
        },
        "best_val_sharpe": best.val_result.sharpe_ratio,
        "best_val_pnl": best.val_result.total_pnl,
        "strategy_config": config_dict,
        "optimize_time_seconds": optimize_time,
    }

    # Shadow evaluation
    if args.shadow:
        print(f"\n{'─' * 60}")
        print(f"Shadow Mode Evaluation")
        print(f"{'─' * 60}")

        baseline = StrategyParams()  # default params
        shadow = ShadowRunner(engine=engine)
        decision, shadow_result = shadow.evaluate_and_decide(
            strategy_id=f"{args.strategy}_v1",
            strategy_type=args.strategy,
            baseline_params=baseline,
            shadow_params=bp,
            data_source=args.data,
            num_candles=args.candles,
        )

        shadow.print_report(shadow_result)

        result_data["shadow"] = {
            "decision": decision,
            "baseline_pnl": shadow_result.baseline_result.total_pnl,
            "shadow_pnl": shadow_result.shadow_result.total_pnl,
            "sim_real_variance_pct": shadow_result.evaluation.sim_real_variance_pct,
            "recommendation": shadow_result.evaluation.recommendation,
        }

        print(f"\n  Decision: {decision}")
        if decision == "APPLY":
            print(f"  Action: Parameters ready for live application")
        elif decision == "MONITOR":
            print(f"  Action: Continue monitoring in shadow mode")
        else:
            print(f"  Action: Parameters rejected — keep current settings")

    # Positive folds summary
    positive_folds = sum(1 for r in results if r.val_result.sharpe_ratio > 0)
    print(f"\n{'=' * 60}")
    print(f"Summary: {positive_folds}/{len(results)} folds with positive validation Sharpe")
    print(f"Total time: {optimize_time:.1f}s")
    print(f"{'=' * 60}")

    return result_data


def main() -> None:
    parser = argparse.ArgumentParser(
        description="LEVIATHAN Auto-Tuner CLI",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--data", default="synthetic", help="Data source: 'synthetic' or CSV path")
    parser.add_argument("--candles", type=int, default=2000, help="Number of candles (synthetic)")
    parser.add_argument("--capital", type=float, default=70.0, help="Initial capital (USDT)")
    parser.add_argument("--fee-rate", type=float, default=0.001, help="Fee rate per leg")
    parser.add_argument("--strategy", default="cross_exchange", help="Strategy type to tune")
    parser.add_argument("--trials", type=int, default=50, help="Optuna trials per fold")
    parser.add_argument("--train-periods", type=int, default=60, help="Train window (candles)")
    parser.add_argument("--val-periods", type=int, default=20, help="Validation window (candles)")
    parser.add_argument("--shadow", action="store_true", help="Run shadow mode evaluation")
    parser.add_argument("--output", type=str, default=None, help="Save results to JSON")

    args = parser.parse_args()

    result = _run_tune(args)

    if args.output and "error" not in result:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(result, f, indent=2)
        print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
