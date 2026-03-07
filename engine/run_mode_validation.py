#!/usr/bin/env python3
"""LEVIATHAN Engine — Mode Validation Pipeline.

Runs all trading modes sequentially to verify the engine works end-to-end.
No real API keys required for stages 1-3.

Usage:
    python run_mode_validation.py                    # Run stages 1-3 (no API needed)
    python run_mode_validation.py --stage 1          # Run specific stage
    python run_mode_validation.py --stage 4          # Requires real API keys
    python run_mode_validation.py --all              # Run all stages including live gate

Mode Progression:
    1. BACKTEST     — Walk-forward optimization with synthetic data
    2. PAPER        — Real public WebSocket data + paper execution (no API keys)
    3. SHADOW       — Real data + paper execution + full metrics + LiveGate
    4. LIVE_GATE    — Live gate evaluation (requires shadow history)

Note: Testnet/Sandbox stage removed — most exchanges have unstable or
      discontinued testnets. Paper mode with real public data is sufficient.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class StageResult:
    """Result of a mode validation stage."""
    stage: int
    name: str
    passed: bool
    duration_s: float
    details: dict = field(default_factory=dict)
    error: str = ""


def _set_env(**kwargs: str) -> None:
    for k, v in kwargs.items():
        os.environ[k] = v


# ── Stage 1: Backtest ────────────────────────────────────────────────────

def run_backtest(trials: int = 3, candles: int = 500) -> StageResult:
    """Walk-forward optimization with synthetic OHLCV data."""
    t0 = time.time()
    try:
        from src.cli.tune_cli import _run_tune
        args = argparse.Namespace(
            data="synthetic", candles=candles, strategy="cross_exchange",
            trials=trials, shadow=False, output=None, verbose=False,
            train_periods=60, val_periods=20, capital=70.0, fee_rate=0.001,
        )
        result = _run_tune(args)
        best_sharpe = result.get("best_val_sharpe", 0)
        folds = result.get("folds", [])
        positive_folds = sum(1 for f in folds if f.get("val_sharpe", 0) > 0)
        return StageResult(
            stage=1, name="BACKTEST", passed=True,
            duration_s=time.time() - t0,
            details={
                "data_source": "synthetic (GBM)",
                "folds": len(folds),
                "positive_folds": positive_folds,
                "best_val_sharpe": round(best_sharpe, 4),
                "best_val_pnl": round(result.get("best_val_pnl", 0), 4),
                "best_params": result.get("best_params", {}),
            },
        )
    except Exception as e:
        return StageResult(
            stage=1, name="BACKTEST", passed=False,
            duration_s=time.time() - t0, error=str(e),
        )


# ── Stage 2: Paper Trading (Real Public Data) ───────────────────────────

async def _run_paper(duration: int = 10) -> StageResult:
    """Paper trading with REAL public WebSocket data — no API keys needed.

    The engine connects to real exchange WebSocket feeds for live orderbook
    data, runs strategies against it, and executes trades on paper (virtual).
    This validates the full pipeline with real market conditions.
    """
    t0 = time.time()
    _set_env(
        EXECUTION_MODE="paper",
        DATA_MODE="real_public",
        ENGINE_ENV="test",
        TRADING_ACTIVE_EXCHANGES='["binance","upbit"]',
        TRADING_SYMBOLS='["BTC/USDT"]',
    )
    try:
        from src.main import Engine
        engine = Engine()
        task = asyncio.create_task(engine.run())
        await asyncio.sleep(duration)
        engine._shutdown_event.set()
        try:
            await asyncio.wait_for(task, timeout=15)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

        details = {
            "data_source": "real_public (WebSocket)",
            "execution_mode": engine._settings.execution_mode if engine._settings else "N/A",
            "data_mode": engine._data_mode,
            "event_bus": type(engine._event_bus).__name__ if engine._event_bus else "None",
            "exchanges": list(engine._exchanges.keys()) if engine._exchanges else [],
            "strategy_manager": engine._strategy_manager is not None,
            "signal_generator": engine._signal_generator is not None,
            "executor": engine._executor is not None,
        }
        return StageResult(
            stage=2, name="PAPER (REAL DATA)", passed=True,
            duration_s=time.time() - t0, details=details,
        )
    except Exception as e:
        return StageResult(
            stage=2, name="PAPER (REAL DATA)", passed=False,
            duration_s=time.time() - t0, error=str(e),
        )


def run_paper(duration: int = 10) -> StageResult:
    return asyncio.run(_run_paper(duration))


# ── Stage 3: Shadow Mode ─────────────────────────────────────────────────

async def _run_shadow(duration: int = 10) -> StageResult:
    """Shadow mode: real data feed + paper execution + full metrics + LiveGate.

    Identical to Paper mode but adds:
    - ShadowMode orchestrator tracking all signal/trade metrics
    - LiveGate readiness evaluation
    - Full PnL tracking and Sharpe calculation
    - PowerLaw slippage simulation
    """
    t0 = time.time()
    _set_env(
        EXECUTION_MODE="paper",
        DATA_MODE="shadow",
        ENGINE_ENV="test",
        TRADING_ACTIVE_EXCHANGES='["binance","upbit"]',
        TRADING_SYMBOLS='["BTC/USDT"]',
    )
    try:
        from src.main import Engine
        engine = Engine()
        task = asyncio.create_task(engine.run())
        await asyncio.sleep(duration)
        engine._shutdown_event.set()
        try:
            await asyncio.wait_for(task, timeout=15)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

        shadow = engine._shadow_mode
        details = {
            "data_source": "real_public (WebSocket) via Shadow orchestrator",
            "data_mode": engine._data_mode,
            "shadow_mode": shadow is not None,
            "live_gate": engine._live_gate is not None,
            "exchanges": list(engine._exchanges.keys()) if engine._exchanges else [],
        }
        if shadow and hasattr(shadow, "_stats"):
            stats = shadow._stats
            details.update({
                "signals": getattr(stats, "total_signals", 0),
                "trades": getattr(stats, "total_trades", 0),
                "total_pnl": getattr(stats, "total_pnl", 0.0),
            })
        return StageResult(
            stage=3, name="SHADOW", passed=True,
            duration_s=time.time() - t0, details=details,
        )
    except Exception as e:
        return StageResult(
            stage=3, name="SHADOW", passed=False,
            duration_s=time.time() - t0, error=str(e),
        )


def run_shadow(duration: int = 10) -> StageResult:
    return asyncio.run(_run_shadow(duration))


# ── Stage 4: Live Gate ────────────────────────────────────────────────────

async def _run_live_gate() -> StageResult:
    """Live gate evaluation — checks if strategies are ready for live trading.

    Evaluates performance metrics accumulated during shadow mode:
    - Sharpe ratio threshold
    - Maximum drawdown limit
    - Minimum trade count
    - Win rate requirement
    """
    t0 = time.time()
    _set_env(
        EXECUTION_MODE="paper",
        DATA_MODE="shadow",
        ENGINE_ENV="test",
        TRADING_ACTIVE_EXCHANGES='["binance","upbit"]',
        TRADING_SYMBOLS='["BTC/USDT"]',
    )
    try:
        from src.main import Engine
        engine = Engine()
        task = asyncio.create_task(engine.run())
        await asyncio.sleep(3)

        details = {"live_gate": engine._live_gate is not None}
        if engine._live_gate:
            gate_result = await engine._live_gate.evaluate()
            details.update({
                "eligible": gate_result.eligible,
                "block_reasons": gate_result.block_reasons,
                "evaluation_duration_ms": gate_result.evaluation_duration_ms,
            })
            for check in gate_result.checks:
                details[f"check_{check.name}"] = {
                    "passed": check.passed,
                    "value": str(check.value),
                    "threshold": str(check.threshold),
                }

        engine._shutdown_event.set()
        try:
            await asyncio.wait_for(task, timeout=15)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

        return StageResult(
            stage=4, name="LIVE_GATE", passed=True,
            duration_s=time.time() - t0, details=details,
        )
    except Exception as e:
        return StageResult(
            stage=4, name="LIVE_GATE", passed=False,
            duration_s=time.time() - t0, error=str(e),
        )


def run_live_gate() -> StageResult:
    return asyncio.run(_run_live_gate())


# ── Main ──────────────────────────────────────────────────────────────────

STAGES = {
    1: ("BACKTEST", run_backtest),
    2: ("PAPER (REAL DATA)", run_paper),
    3: ("SHADOW", run_shadow),
    4: ("LIVE_GATE", run_live_gate),
}


def main() -> None:
    parser = argparse.ArgumentParser(description="LEVIATHAN Mode Validation Pipeline")
    parser.add_argument("--stage", type=int, choices=[1, 2, 3, 4], help="Run specific stage")
    parser.add_argument("--all", action="store_true", help="Run all stages including live gate")
    parser.add_argument("--duration", type=int, default=10, help="Duration per stage (seconds)")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    args = parser.parse_args()

    if args.stage:
        stages_to_run = [args.stage]
    elif args.all:
        stages_to_run = [1, 2, 3, 4]
    else:
        stages_to_run = [1, 2, 3]  # Default: no live gate

    print("\n" + "=" * 60)
    print("LEVIATHAN Mode Validation Pipeline")
    print("=" * 60)
    print(f"Stages: {stages_to_run}")
    print(f"Duration per stage: {args.duration}s")
    print()
    print("  1. BACKTEST   — Synthetic data, walk-forward optimization")
    print("  2. PAPER      — Real WebSocket data, paper execution")
    print("  3. SHADOW     — Real data + metrics + LiveGate eval")
    print("  4. LIVE_GATE  — Strategy readiness check (needs history)")
    print("=" * 60 + "\n")

    results: list[StageResult] = []
    for stage_num in stages_to_run:
        name, func = STAGES[stage_num]
        print(f"[Stage {stage_num}] {name} ...", end=" ", flush=True)

        if stage_num == 1:
            result = func()
        elif stage_num in (2, 3):
            result = func(duration=args.duration)
        else:
            result = func()

        status = "PASS" if result.passed else "FAIL"
        print(f"{status} ({result.duration_s:.1f}s)")

        if not result.passed:
            print(f"  Error: {result.error}")
        elif not args.json:
            for k, v in result.details.items():
                if k != "best_params":
                    print(f"  {k}: {v}")

        results.append(result)

    # Summary
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    print(f"\n{'=' * 60}")
    print(f"SUMMARY: {passed}/{total} stages passed")
    print("=" * 60)

    for r in results:
        icon = "+" if r.passed else "X"
        print(f"  [{icon}] Stage {r.stage}: {r.name} ({r.duration_s:.1f}s)")

    if args.json:
        output = [
            {
                "stage": r.stage, "name": r.name, "passed": r.passed,
                "duration_s": round(r.duration_s, 2),
                "details": r.details, "error": r.error,
            }
            for r in results
        ]
        print("\n" + json.dumps(output, indent=2, default=str))

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
