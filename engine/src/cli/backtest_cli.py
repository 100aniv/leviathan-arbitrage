"""Backtest CLI — run strategy backtests on synthetic or CSV data.

Usage:
    python -m src.cli.backtest_cli --data synthetic --candles 2000
    python -m src.cli.backtest_cli --data ./data.csv --strategy cross_exchange
    python -m src.cli.backtest_cli --data synthetic --optimize --trials 50
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from src.tuning.backtest import BacktestEngine, BacktestResult, StrategyParams
from src.tuning.file_data_loader import (
    FileDataLoader,
    generate_synthetic_ohlcv,
    generate_synthetic_spreads,
)


def _print_result(label: str, result: BacktestResult) -> None:
    """Print a single backtest result."""
    print(f"\n{'=' * 50}")
    print(f"  {label}")
    print(f"{'=' * 50}")
    print(f"  Total PnL:      ${result.total_pnl:.4f}")
    print(f"  Sharpe Ratio:   {result.sharpe_ratio:.4f}")
    print(f"  Max Drawdown:   {result.max_drawdown * 100:.2f}%")
    print(f"  Win Rate:       {result.win_rate * 100:.1f}%")
    print(f"  Num Trades:     {result.num_trades}")

    # Beta gate check
    passes = (
        result.total_pnl > 0
        and (result.num_trades == 0 or result.win_rate > 0)
        and abs(result.max_drawdown) < 0.02
    )
    gate = "PASS" if passes else "FAIL"
    print(f"  Beta Gate:      [{gate}]")


def _run_backtest(args: argparse.Namespace) -> dict:
    """Run a standard backtest."""
    loader = FileDataLoader()

    if args.data == "synthetic":
        ohlcv = generate_synthetic_ohlcv(
            num_candles=args.candles,
            spread_injection_rate=args.injection_rate,
            spread_injection_bps=args.injection_bps,
        )
    else:
        ohlcv = loader.load(args.data)

    engine = BacktestEngine(
        initial_capital=args.capital,
        fee_rate=args.fee_rate,
    )

    params = StrategyParams(
        min_spread_bps=args.min_spread_bps,
        max_position_size=args.max_position,
        entry_threshold=args.entry_threshold,
        exit_threshold=args.exit_threshold,
        stop_loss_pct=args.stop_loss,
    )

    print(f"\nRunning backtest on {args.data} ({ohlcv.length} candles)...")
    print(f"  Capital: ${args.capital:.2f}")
    print(f"  Fee rate: {args.fee_rate * 100:.2f}%")
    print(f"  Params: spread_bps={params.min_spread_bps} entry={params.entry_threshold} "
          f"exit={params.exit_threshold} stop={params.stop_loss_pct}")

    start = time.time()
    result = engine.run(params, ohlcv)
    elapsed = time.time() - start

    _print_result(f"Backtest ({args.data})", result)
    print(f"  Time:           {elapsed:.2f}s")

    # Spread-based backtest
    if args.data == "synthetic":
        spreads = generate_synthetic_spreads(num_records=args.candles)
        spread_result = engine.run_on_spreads(params, spreads)
        _print_result("Spread-based Backtest", spread_result)

    return {
        "data_source": args.data,
        "candles": ohlcv.length,
        "params": {
            "min_spread_bps": params.min_spread_bps,
            "max_position_size": params.max_position_size,
            "entry_threshold": params.entry_threshold,
            "exit_threshold": params.exit_threshold,
            "stop_loss_pct": params.stop_loss_pct,
        },
        "result": {
            "total_pnl": result.total_pnl,
            "sharpe_ratio": result.sharpe_ratio,
            "max_drawdown": result.max_drawdown,
            "win_rate": result.win_rate,
            "num_trades": result.num_trades,
        },
        "elapsed_seconds": elapsed,
    }


def _run_optimization(args: argparse.Namespace) -> dict:
    """Run walk-forward optimization."""
    from src.tuning.optimizer import (
        TunerConfig,
        WalkForwardOptimizer,
    )

    loader = FileDataLoader()

    if args.data == "synthetic":
        ohlcv = generate_synthetic_ohlcv(
            num_candles=args.candles,
            spread_injection_rate=args.injection_rate,
            spread_injection_bps=args.injection_bps,
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

    print(f"\nRunning walk-forward optimization...")
    print(f"  Data: {args.data} ({ohlcv.length} candles)")
    print(f"  Trials: {args.trials}")
    print(f"  Train/Val: {args.train_periods}/{args.val_periods} candles")

    start = time.time()
    results = optimizer.optimize(ohlcv, loader)
    elapsed = time.time() - start

    if not results:
        print("\nNo optimization results — not enough data for walk-forward windows.")
        return {"error": "insufficient_data"}

    print(f"\n{'=' * 60}")
    print(f"  Walk-Forward Optimization Results ({len(results)} folds)")
    print(f"{'=' * 60}")

    fold_data = []
    for i, res in enumerate(results):
        tp = res.best_params
        tr = res.train_result
        vr = res.val_result
        print(f"\n  Fold {i + 1}:")
        print(f"    Train PnL:  ${tr.total_pnl:.4f}  Sharpe: {tr.sharpe_ratio:.4f}")
        print(f"    Val PnL:    ${vr.total_pnl:.4f}  Sharpe: {vr.sharpe_ratio:.4f}")
        print(f"    Best params: spread={tp.min_spread_bps:.1f} "
              f"entry={tp.entry_threshold:.6f} exit={tp.exit_threshold:.6f}")

        fold_data.append({
            "fold": i + 1,
            "train_pnl": tr.total_pnl,
            "train_sharpe": tr.sharpe_ratio,
            "val_pnl": vr.total_pnl,
            "val_sharpe": vr.sharpe_ratio,
            "best_params": {
                "min_spread_bps": tp.min_spread_bps,
                "max_position_size": tp.max_position_size,
                "entry_threshold": tp.entry_threshold,
                "exit_threshold": tp.exit_threshold,
                "stop_loss_pct": tp.stop_loss_pct,
            },
        })

    best = optimizer.select_best_fold(results)
    if best:
        print(f"\n  Best fold (by validation Sharpe):")
        print(f"    Val Sharpe: {best.val_result.sharpe_ratio:.4f}")
        print(f"    Val PnL:    ${best.val_result.total_pnl:.4f}")
        print(f"    Shadow:     {best.shadow_mode}")

    print(f"\n  Total time: {elapsed:.1f}s")
    print(f"{'=' * 60}")

    return {
        "data_source": args.data,
        "candles": ohlcv.length,
        "trials": args.trials,
        "folds": fold_data,
        "elapsed_seconds": elapsed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="LEVIATHAN Backtest CLI",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--data", default="synthetic",
        help="Data source: 'synthetic' or path to CSV file",
    )
    parser.add_argument("--candles", type=int, default=2000, help="Number of candles (synthetic)")
    parser.add_argument("--capital", type=float, default=70.0, help="Initial capital (USDT)")
    parser.add_argument("--fee-rate", type=float, default=0.001, help="Fee rate per leg")

    # Strategy params
    parser.add_argument("--min-spread-bps", type=float, default=5.0, help="Min spread (bps)")
    parser.add_argument("--max-position", type=float, default=35.0, help="Max position size (USDT)")
    parser.add_argument("--entry-threshold", type=float, default=0.0005, help="Entry threshold")
    parser.add_argument("--exit-threshold", type=float, default=0.0002, help="Exit threshold")
    parser.add_argument("--stop-loss", type=float, default=0.02, help="Stop loss %")

    # Spread injection
    parser.add_argument("--injection-rate", type=float, default=0.15, help="Spread injection rate")
    parser.add_argument("--injection-bps", type=float, default=30.0, help="Spread injection bps")

    # Optimization
    parser.add_argument("--optimize", action="store_true", help="Run walk-forward optimization")
    parser.add_argument("--trials", type=int, default=50, help="Optuna trials per fold")
    parser.add_argument("--train-periods", type=int, default=60, help="Train window (candles)")
    parser.add_argument("--val-periods", type=int, default=20, help="Validation window (candles)")

    # Output
    parser.add_argument("--output", type=str, default=None, help="Save results to JSON file")

    args = parser.parse_args()

    if args.optimize:
        result = _run_optimization(args)
    else:
        result = _run_backtest(args)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(result, f, indent=2)
        print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
