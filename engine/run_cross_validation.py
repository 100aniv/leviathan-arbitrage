"""Cross-validation: seeds 42/123/777, capitals $1K/$10K/$100K, tuned vs default params."""
import json
import sys
import os
import statistics
sys.path.insert(0, os.path.dirname(__file__))

from src.tuning.strategy_backtest import StrategyBacktestEngine, STRATEGY_TYPES
from src.tuning.backtest import StrategyParams
from src.tuning.file_data_loader import generate_synthetic_ohlcv

SEEDS = [42, 123, 777]
CAPITALS = [1_000, 10_000, 100_000]
STRATEGIES = STRATEGY_TYPES

# Load tuned params
TUNED_PARAMS = {}
for st in STRATEGIES:
    path = f"../reports/tune_{st}_final.json"
    with open(path) as f:
        d = json.load(f)
    bp = d["best_params"]
    TUNED_PARAMS[st] = StrategyParams(
        min_spread_bps=bp["min_spread_bps"],
        max_position_size=bp["max_position_size"],
        entry_threshold=bp["entry_threshold"],
        exit_threshold=bp["exit_threshold"],
        stop_loss_pct=bp["stop_loss_pct"],
    )

DEFAULT_PARAMS = StrategyParams()  # defaults

print("=" * 70)
print("CROSS-VALIDATION: 3 seeds x 3 capitals x 7 strategies x 2 param sets")
print("=" * 70)

results = {}  # {strategy: {tuned/default: {seed: {capital: BacktestResult}}}}

for st in STRATEGIES:
    results[st] = {"tuned": {}, "default": {}}
    for seed in SEEDS:
        ohlcv = generate_synthetic_ohlcv(num_candles=2000, seed=seed)
        results[st]["tuned"][seed] = {}
        results[st]["default"][seed] = {}
        for cap in CAPITALS:
            for label, params in [("tuned", TUNED_PARAMS[st]), ("default", DEFAULT_PARAMS)]:
                engine = StrategyBacktestEngine(
                    strategy_type=st, initial_capital=cap, fee_rate=0.001, seed=seed
                )
                r = engine.run(params, ohlcv)
                results[st][label][seed][cap] = r

# ── Print summary table ──────────────────────────────────────────────────────
print(f"\n{'Strategy':<20} {'Mode':<8} {'S42:$10K':>12} {'S123:$10K':>12} {'S777:$10K':>12} {'StdDev':>8} {'±30%?':>6}")
print("-" * 80)

summary_rows = []
for st in STRATEGIES:
    for label in ["tuned", "default"]:
        sharpes = [results[st][label][seed][10_000].sharpe_ratio for seed in SEEDS]
        mean_s = statistics.mean(sharpes)
        std_s = statistics.stdev(sharpes) if len(sharpes) > 1 else 0
        pct_dev = (std_s / abs(mean_s) * 100) if abs(mean_s) > 0.001 else 999
        ok = "OK" if pct_dev <= 30 else "FAIL"
        print(f"{st:<20} {label:<8} {sharpes[0]:>12.3f} {sharpes[1]:>12.3f} {sharpes[2]:>12.3f} {std_s:>8.3f} {ok:>6}")
        summary_rows.append({
            "strategy": st, "mode": label,
            "sharpes": sharpes, "mean_sharpe": mean_s, "std_sharpe": std_s,
            "pct_dev": pct_dev, "consistent": ok == "OK"
        })
    print()

# ── Capital scaling check ───────────────────────────────────────────────────
print(f"\n{'Strategy':<20} {'Capital':>10} {'Mean Sharpe':>12} {'Mean WR%':>10} {'Mean MDD%':>10}")
print("-" * 64)
for st in STRATEGIES:
    for cap in CAPITALS:
        sharpes = [results[st]["tuned"][seed][cap].sharpe_ratio for seed in SEEDS]
        wrs = [results[st]["tuned"][seed][cap].win_rate * 100 for seed in SEEDS]
        mdds = [results[st]["tuned"][seed][cap].max_drawdown * 100 for seed in SEEDS]
        print(f"{st:<20} {f'${cap:,}':>10} {statistics.mean(sharpes):>12.3f} {statistics.mean(wrs):>10.1f} {statistics.mean(mdds):>10.2f}")

# ── Tuned vs Default improvement ────────────────────────────────────────────
print(f"\n{'Strategy':<20} {'Default Sharpe':>14} {'Tuned Sharpe':>13} {'Improvement':>12}")
print("-" * 62)
for st in STRATEGIES:
    d_sharpes = [results[st]["default"][seed][10_000].sharpe_ratio for seed in SEEDS]
    t_sharpes = [results[st]["tuned"][seed][10_000].sharpe_ratio for seed in SEEDS]
    d_mean = statistics.mean(d_sharpes)
    t_mean = statistics.mean(t_sharpes)
    if abs(d_mean) > 0.001:
        impr = (t_mean - d_mean) / abs(d_mean) * 100
        impr_str = f"{impr:+.1f}%"
    else:
        impr_str = "N/A (default~0)"
    print(f"{st:<20} {d_mean:>14.3f} {t_mean:>13.3f} {impr_str:>12}")

# ── Save structured results ──────────────────────────────────────────────────
export = {}
for st in STRATEGIES:
    export[st] = {
        "tuned_params": {
            "min_spread_bps": TUNED_PARAMS[st].min_spread_bps,
            "max_position_size": TUNED_PARAMS[st].max_position_size,
            "entry_threshold": TUNED_PARAMS[st].entry_threshold,
            "exit_threshold": TUNED_PARAMS[st].exit_threshold,
            "stop_loss_pct": TUNED_PARAMS[st].stop_loss_pct,
        },
        "cross_val": {}
    }
    for label in ["tuned", "default"]:
        export[st]["cross_val"][label] = {}
        for seed in SEEDS:
            export[st]["cross_val"][label][str(seed)] = {}
            for cap in CAPITALS:
                r = results[st][label][seed][cap]
                export[st]["cross_val"][label][str(seed)][str(cap)] = {
                    "sharpe": r.sharpe_ratio,
                    "total_pnl": r.total_pnl,
                    "win_rate": r.win_rate,
                    "max_drawdown": r.max_drawdown,
                    "n_trades": r.num_trades,
                }

with open("../reports/cross_validation_results.json", "w") as f:
    json.dump(export, f, indent=2)
print("\n\nResults saved to ../reports/cross_validation_results.json")
