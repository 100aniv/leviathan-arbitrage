# US-067: Per-Strategy Isolated Shadow Validation (Profitability Gate)

**Phase**: G (Strategy Profitability Restoration)
**Priority**: 71
**Mode**: STANDARD (builds on existing ShadowMode infrastructure; env-var-driven control)
**Date**: 2026-03-11

---

## Context

### Problem

7 strategies are registered at startup (main.py:582-589) but there is no mechanism to objectively determine which strategies are profitable under current market conditions. The most recent 10min Shadow results (US-066) show:

| Strategy | Trades | WR | PnL |
|----------|--------|-----|-----|
| cross_exchange_v1 | 86 | 55.6% | +$14.21 |
| spot_futures_v1 | 32 | 28.1% | -$8.50 |
| latency_arb_v1 | 37 | 100% | +$14.47 |

Only 3 strategies produced signals in that run. The other 4 (futures_futures, triangular, funding_rate, statistical_arb) are structurally constrained (single futures exchange, sparse cycles, negative WFE) and need individual validation.

### Current State

| What Exists | Where | How It Helps |
|-------------|-------|-------------|
| Strategy blacklist via env var | `shadow.py:510-513` | `SHADOW_DISABLED_STRATEGIES` already supports comma-separated disable list |
| Per-strategy metrics tracking | `shadow.py:256-266, 350` | `ShadowStats.by_strategy` dict tracks pnl/trades/wins/losses per strategy_id |
| Strategy summary reporting | `shadow.py:1611-1688` | `_send_summary()` builds per-strategy breakdown for Telegram |
| 7 strategy IDs | `main.py:582-589` | `cross_exchange_v1`, `spot_futures_v1`, `futures_futures_v1`, `triangular_v1`, `funding_rate_v1`, `statistical_arb_v1`, `latency_arb_v1` |
| Progressive Shadow pattern | `progressive_shadow.py:130-150` | Demonstrates wrapping ShadowMode with staged gate evaluation |
| Stale orderbook defense | `stale_detector.py:23-178` | US-066 4-layer defense prevents Korean stale data losses |

### Key Files

| File | Role | Lines |
|------|------|-------|
| `engine/src/modes/shadow.py` | ShadowMode orchestrator, stats, disabled_strategies | ~1705 |
| `engine/src/main.py` | Engine entry, strategy registration, shadow loop | ~1200 |
| `engine/src/strategies/manager.py` | StrategyManager lifecycle, route_signal | 294 |
| `engine/src/modes/progressive_shadow.py` | Progressive gate pattern (reference) | ~400 |
| `engine/config/strategy_params.json` | Tuned strategy parameters | 71 |

---

## Design

### Architecture: StrategyValidationOrchestrator

A new class `StrategyValidationOrchestrator` that wraps an existing ShadowMode instance. It:
1. Starts ShadowMode once (WS collectors connect once)
2. Iterates over each strategy, enabling only that one via dynamic `_disabled_strategies` manipulation
3. Runs each strategy for `VALIDATION_DURATION_S` (default 600s = 10min)
4. Resets stats between runs via a new `ShadowMode.reset_stats()` method
5. Collects per-strategy results into a `StrategyValidationReport`
6. Optionally runs a combined validation of all profitable strategies
7. Writes results to `config/strategy_activation.json`
8. Stops ShadowMode

### Why Not Create Separate ShadowMode Instances?

Creating a new ShadowMode per strategy means re-creating CollectorManager, reconnecting 8 WebSocket connections, and waiting for orderbook hydration (5-30s). Over 7 strategies, this wastes 35-210 seconds of connection overhead and creates flaky reconnection scenarios. Reusing a single running ShadowMode with dynamic `_disabled_strategies` manipulation is cheaper and more reliable.

### Why 10 Minutes Per Strategy?

- Phase 7.3k validated 10min as meaningful: 3110 trades, 100% WR, +$21.10
- 7 strategies x 10min = 70min total (vs 7 hours for 1H each)
- Strategies with zero signals in 10min are structurally blocked (e.g., futures_futures needs 2+ futures exchanges)
- Adjustable via `STRATEGY_VALIDATION_DURATION_S` env var

### Strategy ID to Disable-List Mapping

All strategy IDs registered at `main.py:582-589`:

| strategy_id | STRATEGY_TYPE | Expected Behavior |
|-------------|--------------|-------------------|
| `cross_exchange_v1` | `cross_exchange_spot` | Active, profitable in prior runs |
| `spot_futures_v1` | `spot_futures_basis` | Conditional: cost > basis in most markets |
| `futures_futures_v1` | `futures_futures` | Conditional: only 1 futures exchange |
| `triangular_v1` | `triangular` | Conditional: sparse real market cycles |
| `funding_rate_v1` | `funding_rate_arb` | Verified: 4 exchanges x 8 symbols collecting |
| `statistical_arb_v1` | `statistical_arb` | Verified but WFE negative |
| `latency_arb_v1` | `latency_arb` | Active, profitable (100% WR in US-066) |

To isolate strategy X: set `_disabled_strategies` to all strategy IDs EXCEPT X.

Additionally, `cross_exchange_spot` signals from SignalGenerator (shadow.py:792) use `strategy_id=None` or `self.STRATEGY_ID = "shadow_arb_v1"`. These must also be handled: when isolating a non-cross-exchange strategy, the `shadow_arb_v1` default must also be disabled.

---

## Implementation Plan

### Step 1: Add `reset_stats()` and `get_strategy_report()` to ShadowMode

**File**: `engine/src/modes/shadow.py`
**Location**: After `_compute_drawdown()` at line 1704

```python
# After line 1704, add:

def reset_stats(self) -> None:
    """Reset all cumulative stats for a new validation run.

    Preserves running state, collectors, orderbooks — only resets metrics.
    """
    self._stats = ShadowStats(start_time=time.monotonic())
    logger.info("shadow_mode.stats_reset")

def set_disabled_strategies(self, disabled: set[str]) -> None:
    """Dynamically update the strategy blacklist.

    Args:
        disabled: Set of strategy_id strings to block.
    """
    self._disabled_strategies = disabled
    logger.info(
        "shadow_mode.disabled_strategies_updated",
        disabled=sorted(disabled),
        count=len(disabled),
    )

def get_strategy_report(self) -> dict[str, Any]:
    """Return current per-strategy metrics as a serializable dict.

    Returns:
        Dict with keys: elapsed_s, total trades/pnl/win_rate,
        and by_strategy breakdown.
    """
    stats = self._stats
    elapsed = time.monotonic() - stats.start_time
    total_trades = stats.trades_executed

    by_strategy: list[dict[str, Any]] = []
    for s_id, ss in sorted(stats.by_strategy.items()):
        s_wr = ss.wins / ss.trades if ss.trades > 0 else 0.0
        by_strategy.append({
            "strategy_id": s_id,
            "trades": ss.trades,
            "wins": ss.wins,
            "losses": ss.losses,
            "win_rate": round(s_wr, 4),
            "pnl": round(ss.pnl, 6),
            "rejections": ss.rejections,
            "partial_fills": ss.partial_fills,
        })

    return {
        "elapsed_s": round(elapsed, 1),
        "total_trades": total_trades,
        "total_pnl": round(stats.total_pnl, 6),
        "total_win_rate": round(
            stats.trades_won / total_trades if total_trades > 0 else 0.0, 4
        ),
        "max_drawdown": round(stats.max_drawdown, 6),
        "trades_rejected": stats.trades_rejected,
        "trades_rate_limited": stats.trades_rate_limited,
        "by_strategy": by_strategy,
    }
```

**Rationale**: `reset_stats()` preserves the live WebSocket connections and orderbook state while zeroing metrics. `set_disabled_strategies()` allows the orchestrator to dynamically swap which strategies are active without recreating ShadowMode. `get_strategy_report()` provides a structured snapshot for the report.

### Step 2: Create StrategyValidationOrchestrator

**File**: `engine/src/modes/strategy_validation.py` (NEW)

```python
"""Strategy Validation Orchestrator — per-strategy isolated Shadow testing.

US-067: Runs each registered strategy in isolation for N minutes, collects
per-strategy PnL, and produces an activation report.

Architecture:
  1. Start ShadowMode once (collectors connect, orderbooks hydrate)
  2. For each strategy: enable only that one, reset stats, run N minutes
  3. Collect results into StrategyValidationReport
  4. Optionally run combined validation of all profitable strategies
  5. Write results to config/strategy_activation.json
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class StrategyResult:
    """Validation result for a single strategy."""

    strategy_id: str
    trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    pnl: float = 0.0
    elapsed_s: float = 0.0
    profitable: bool = False
    reason: str = ""  # human-readable verdict


@dataclass
class StrategyValidationReport:
    """Aggregated report for all strategies."""

    started_at: str = ""
    completed_at: str = ""
    duration_per_strategy_s: int = 600
    strategies: list[StrategyResult] = field(default_factory=list)
    profitable_strategies: list[str] = field(default_factory=list)
    unprofitable_strategies: list[str] = field(default_factory=list)
    combined_result: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

# The default signal ID that SignalGenerator uses for cross_exchange signals
# when no strategy_id is set on the signal. Must be disabled when isolating
# non-cross-exchange strategies.
_SHADOW_DEFAULT_SID = "shadow_arb_v1"


class StrategyValidationOrchestrator:
    """Per-strategy isolated Shadow validation orchestrator.

    Usage::

        orchestrator = StrategyValidationOrchestrator(
            shadow_mode=shadow_mode,
            strategy_ids=["cross_exchange_v1", "spot_futures_v1", ...],
        )
        report = await orchestrator.run()
        # report.profitable_strategies = ["cross_exchange_v1", "latency_arb_v1"]

    Args:
        shadow_mode: Initialized but NOT started ShadowMode instance.
        strategy_ids: List of strategy IDs to validate (default: all 7).
        duration_s: Seconds to run each strategy (default: 600 = 10min).
                    Override via STRATEGY_VALIDATION_DURATION_S env var.
        min_trades: Minimum trades required to consider a strategy valid.
                    Strategies with fewer trades are marked "insufficient_data".
                    Default: 5. Override via STRATEGY_VALIDATION_MIN_TRADES.
        run_combined: Whether to run a combined validation of profitable
                      strategies after individual runs. Default: True.
        combined_duration_s: Seconds for the combined run. Default: same as
                             duration_s. Override via STRATEGY_VALIDATION_COMBINED_DURATION_S.
        telegram: Optional TelegramAlerter for progress notifications.
        output_path: Path for strategy_activation.json output.
                     Default: engine/config/strategy_activation.json
    """

    ALL_STRATEGY_IDS: list[str] = [
        "cross_exchange_v1",
        "spot_futures_v1",
        "futures_futures_v1",
        "triangular_v1",
        "funding_rate_v1",
        "statistical_arb_v1",
        "latency_arb_v1",
    ]

    def __init__(
        self,
        shadow_mode: Any,
        strategy_ids: list[str] | None = None,
        duration_s: int | None = None,
        min_trades: int | None = None,
        run_combined: bool = True,
        combined_duration_s: int | None = None,
        telegram: Any | None = None,
        output_path: str | Path | None = None,
    ) -> None:
        self._shadow = shadow_mode
        self._strategy_ids = strategy_ids or self.ALL_STRATEGY_IDS
        self._duration_s = duration_s or int(
            os.getenv("STRATEGY_VALIDATION_DURATION_S", "600")
        )
        self._min_trades = min_trades or int(
            os.getenv("STRATEGY_VALIDATION_MIN_TRADES", "5")
        )
        self._run_combined = run_combined
        self._combined_duration_s = combined_duration_s or int(
            os.getenv("STRATEGY_VALIDATION_COMBINED_DURATION_S", str(self._duration_s))
        )
        self._telegram = telegram
        self._output_path = Path(
            output_path
            or os.getenv(
                "STRATEGY_ACTIVATION_PATH",
                str(Path(__file__).parent.parent.parent / "config" / "strategy_activation.json"),
            )
        )
        self._report = StrategyValidationReport(
            duration_per_strategy_s=self._duration_s,
        )

    async def run(self) -> StrategyValidationReport:
        """Execute the full validation pipeline.

        Returns:
            StrategyValidationReport with per-strategy results and
            final activation list.
        """
        self._report.started_at = datetime.now(timezone.utc).isoformat()

        logger.info(
            "strategy_validation.starting",
            strategies=self._strategy_ids,
            duration_per_s=self._duration_s,
            min_trades=self._min_trades,
        )

        if self._telegram:
            try:
                await self._telegram.send_alert(
                    f"Strategy Validation starting\n"
                    f"Strategies: {len(self._strategy_ids)}\n"
                    f"Duration per strategy: {self._duration_s}s\n"
                    f"Total estimated: {len(self._strategy_ids) * self._duration_s // 60}min",
                    level="INFO",
                )
            except Exception:
                pass

        # Phase 1: Start ShadowMode (connects WS, hydrates orderbooks)
        await self._shadow.start()

        # Wait for orderbook hydration (collectors need time to receive data)
        hydration_wait = int(os.getenv("STRATEGY_VALIDATION_HYDRATION_S", "30"))
        logger.info("strategy_validation.hydrating", wait_s=hydration_wait)
        await asyncio.sleep(hydration_wait)

        # Phase 2: Individual strategy validation
        for sid in self._strategy_ids:
            result = await self._validate_single_strategy(sid)
            self._report.strategies.append(result)

            logger.info(
                "strategy_validation.strategy_complete",
                strategy_id=sid,
                trades=result.trades,
                pnl=result.pnl,
                win_rate=result.win_rate,
                profitable=result.profitable,
                reason=result.reason,
            )

        # Phase 3: Classify strategies
        self._report.profitable_strategies = [
            r.strategy_id for r in self._report.strategies if r.profitable
        ]
        self._report.unprofitable_strategies = [
            r.strategy_id for r in self._report.strategies if not r.profitable
        ]

        # Phase 4: Combined validation of profitable strategies
        if self._run_combined and self._report.profitable_strategies:
            combined = await self._validate_combined(
                self._report.profitable_strategies
            )
            self._report.combined_result = combined

        # Phase 5: Stop ShadowMode
        await self._shadow.stop()

        self._report.completed_at = datetime.now(timezone.utc).isoformat()

        # Phase 6: Write results
        self._write_activation_config()
        self._log_final_report()

        if self._telegram:
            try:
                await self._send_telegram_report()
            except Exception:
                pass

        return self._report

    async def _validate_single_strategy(self, strategy_id: str) -> StrategyResult:
        """Run ShadowMode with only one strategy enabled for duration_s.

        Disables all other strategies via set_disabled_strategies(),
        resets stats, waits, then collects results.
        """
        # Build disable set: all strategies EXCEPT the one under test
        # Also include _SHADOW_DEFAULT_SID when testing non-cross-exchange
        # strategies, because SignalGenerator emits signals with strategy_id=None
        # which defaults to "shadow_arb_v1" — those are cross_exchange signals.
        all_others = set(self._strategy_ids) - {strategy_id}
        if strategy_id != "cross_exchange_v1":
            all_others.add(_SHADOW_DEFAULT_SID)

        self._shadow.set_disabled_strategies(all_others)
        self._shadow.reset_stats()

        logger.info(
            "strategy_validation.isolating",
            strategy_id=strategy_id,
            disabled_count=len(all_others),
            duration_s=self._duration_s,
        )

        # Run for duration_s
        await asyncio.sleep(self._duration_s)

        # Collect results
        report = self._shadow.get_strategy_report()
        result = StrategyResult(
            strategy_id=strategy_id,
            elapsed_s=report["elapsed_s"],
        )

        # Find this strategy's metrics in the by_strategy breakdown
        for entry in report.get("by_strategy", []):
            if entry["strategy_id"] == strategy_id:
                result.trades = entry["trades"]
                result.wins = entry["wins"]
                result.losses = entry["losses"]
                result.win_rate = entry["win_rate"]
                result.pnl = entry["pnl"]
                break

        # Also check if the default shadow_arb_v1 produced trades
        # (cross_exchange signals may appear under that ID)
        if strategy_id == "cross_exchange_v1":
            for entry in report.get("by_strategy", []):
                if entry["strategy_id"] == _SHADOW_DEFAULT_SID:
                    result.trades += entry["trades"]
                    result.wins += entry["wins"]
                    result.losses += entry["losses"]
                    result.pnl += entry["pnl"]
                    if result.trades > 0:
                        result.win_rate = round(result.wins / result.trades, 4)

        # Classify
        if result.trades < self._min_trades:
            result.profitable = False
            result.reason = f"insufficient_data (trades={result.trades} < min={self._min_trades})"
        elif result.pnl > 0:
            result.profitable = True
            result.reason = f"profitable (PnL=${result.pnl:+.4f}, WR={result.win_rate:.1%})"
        else:
            result.profitable = False
            result.reason = f"unprofitable (PnL=${result.pnl:+.4f}, WR={result.win_rate:.1%})"

        return result

    async def _validate_combined(
        self, profitable_ids: list[str]
    ) -> dict[str, Any]:
        """Run combined validation with all profitable strategies enabled.

        Returns the strategy report dict from the combined run.
        """
        # Disable only unprofitable strategies
        disabled = set(self._strategy_ids) - set(profitable_ids)
        # If cross_exchange is profitable, allow shadow_arb_v1 default signals
        if "cross_exchange_v1" not in profitable_ids:
            disabled.add(_SHADOW_DEFAULT_SID)

        self._shadow.set_disabled_strategies(disabled)
        self._shadow.reset_stats()

        logger.info(
            "strategy_validation.combined_start",
            profitable=profitable_ids,
            disabled=sorted(disabled),
            duration_s=self._combined_duration_s,
        )

        await asyncio.sleep(self._combined_duration_s)

        report = self._shadow.get_strategy_report()

        logger.info(
            "strategy_validation.combined_complete",
            trades=report["total_trades"],
            pnl=report["total_pnl"],
            win_rate=report["total_win_rate"],
        )

        return report

    def _write_activation_config(self) -> None:
        """Write strategy_activation.json with validation results."""
        activation = {
            "_meta": {
                "source": "US-067 StrategyValidationOrchestrator",
                "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "duration_per_strategy_s": self._duration_s,
                "min_trades_threshold": self._min_trades,
            },
            "active_strategies": self._report.profitable_strategies,
            "disabled_strategies": self._report.unprofitable_strategies,
            "shadow_disabled_env": ",".join(self._report.unprofitable_strategies),
            "results": {},
        }

        for r in self._report.strategies:
            activation["results"][r.strategy_id] = {
                "profitable": r.profitable,
                "trades": r.trades,
                "pnl": r.pnl,
                "win_rate": r.win_rate,
                "reason": r.reason,
                "elapsed_s": r.elapsed_s,
            }

        if self._report.combined_result:
            activation["combined_validation"] = self._report.combined_result

        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._output_path, "w") as f:
            json.dump(activation, f, indent=2, default=str)

        logger.info(
            "strategy_validation.activation_written",
            path=str(self._output_path),
            active=self._report.profitable_strategies,
            disabled=self._report.unprofitable_strategies,
        )

    def _log_final_report(self) -> None:
        """Log the final validation report."""
        logger.info(
            "strategy_validation.complete",
            started_at=self._report.started_at,
            completed_at=self._report.completed_at,
            total_strategies=len(self._report.strategies),
            profitable=len(self._report.profitable_strategies),
            unprofitable=len(self._report.unprofitable_strategies),
            active_list=self._report.profitable_strategies,
            disabled_list=self._report.unprofitable_strategies,
        )

        for r in self._report.strategies:
            logger.info(
                "strategy_validation.result",
                strategy_id=r.strategy_id,
                trades=r.trades,
                pnl=f"${r.pnl:+.4f}",
                win_rate=f"{r.win_rate:.1%}",
                profitable=r.profitable,
                reason=r.reason,
            )

    async def _send_telegram_report(self) -> None:
        """Send validation report via Telegram."""
        lines = [
            "=== Strategy Validation Report ===",
            f"Duration: {self._duration_s}s per strategy",
            f"Min trades: {self._min_trades}",
            "",
            "PROFITABLE:",
        ]
        for r in self._report.strategies:
            if r.profitable:
                lines.append(
                    f"  + {r.strategy_id}: {r.trades}T / "
                    f"{r.win_rate:.0%} WR / ${r.pnl:+.4f}"
                )
        lines.append("")
        lines.append("UNPROFITABLE / INSUFFICIENT DATA:")
        for r in self._report.strategies:
            if not r.profitable:
                lines.append(
                    f"  - {r.strategy_id}: {r.trades}T / "
                    f"${r.pnl:+.4f} ({r.reason})"
                )

        if self._report.combined_result:
            cr = self._report.combined_result
            lines.append("")
            lines.append(
                f"COMBINED: {cr.get('total_trades', 0)}T / "
                f"{cr.get('total_win_rate', 0):.0%} WR / "
                f"${cr.get('total_pnl', 0):+.4f}"
            )

        lines.append("")
        lines.append(
            f"SHADOW_DISABLED_STRATEGIES="
            f"{','.join(self._report.unprofitable_strategies)}"
        )

        await self._telegram.send_alert("\n".join(lines), level="INFO")
```

### Step 3: Add `--strategy-validation` Mode to main.py

**File**: `engine/src/main.py`
**Location**: After `_progressive_shadow_loop` (line ~1200), add new method

```python
# Add after _progressive_shadow_loop, around line 1218:

async def _strategy_validation_loop(self) -> None:
    """Run Strategy Validation: per-strategy isolated Shadow testing.

    Enabled when STRATEGY_VALIDATION=true.
    Each strategy runs in isolation for STRATEGY_VALIDATION_DURATION_S
    seconds (default 600). Results written to config/strategy_activation.json.
    """
    from src.collectors.funding_rate_collector import FundingRateCollector
    from src.modes.shadow import ShadowMode
    from src.modes.strategy_validation import StrategyValidationOrchestrator

    symbols = self._settings.trading.symbols if self._settings else ["BTC/USDT"]
    exchanges = (
        self._settings.trading.active_exchanges
        if self._settings
        else ["binance", "bybit", "okx", "bitget"]
    )

    from src.core.multi_signal import MultiStrategySignalProducer

    multi_signal_producer = MultiStrategySignalProducer(
        event_bus=self._event_bus,
        latency_tracker=getattr(self, "_latency_tracker", None),
    )

    funding_rate_collector = FundingRateCollector(
        symbols=symbols,
        http_client=getattr(self, "_http_client", None),
    )

    shadow_mode = ShadowMode(
        signal_generator=self._signal_generator,
        paper_executor=None,
        collector_manager=None,
        market_recorder=self._market_recorder,
        telegram=self._telegram,
        symbols=symbols,
        exchanges=exchanges,
        multi_signal_producer=multi_signal_producer,
        funding_rate_collector=funding_rate_collector,
        strategy_manager=self._strategy_manager,
    )

    # Set all registered strategies to shadow mode
    if self._strategy_manager is not None:
        for sid in self._strategy_manager.list_strategies():
            s = self._strategy_manager.get_strategy(sid)
            if s:
                s.shadow_mode = True
        for sid in self._strategy_manager.list_strategies():
            try:
                await self._strategy_manager.start_strategy(sid)
            except Exception as exc:
                logger.warning("Validation strategy %s start failed: %s", sid, exc)

    # Determine strategy IDs to validate
    strategy_ids = (
        self._strategy_manager.list_strategies()
        if self._strategy_manager
        else StrategyValidationOrchestrator.ALL_STRATEGY_IDS
    )

    orchestrator = StrategyValidationOrchestrator(
        shadow_mode=shadow_mode,
        strategy_ids=strategy_ids,
        telegram=self._telegram,
    )

    try:
        report = await orchestrator.run()
        logger.info(
            "Strategy validation complete: %d profitable, %d unprofitable",
            len(report.profitable_strategies),
            len(report.unprofitable_strategies),
        )

        # Apply results: update SHADOW_DISABLED_STRATEGIES for future runs
        if report.unprofitable_strategies:
            disabled_env = ",".join(report.unprofitable_strategies)
            logger.info(
                "Recommended SHADOW_DISABLED_STRATEGIES=%s", disabled_env
            )

    except Exception as exc:
        logger.error("Strategy validation failed: %s", exc, exc_info=True)
```

**Additionally**, modify `_start_background_tasks()` at line ~802 to add a new branch:

```python
# In _start_background_tasks(), after the SHADOW_PROGRESSIVE check (line 804):
# Add BEFORE the else branch for normal shadow:

        if self._data_mode == DataMode.SHADOW:
            strategy_validation = os.getenv("STRATEGY_VALIDATION", "false").lower() == "true"
            shadow_progressive = os.getenv("SHADOW_PROGRESSIVE", "false").lower() == "true"
            if strategy_validation:
                tasks.append(
                    asyncio.create_task(self._strategy_validation_loop(), name="strategy_validation")
                )
                logger.info("Data mode: SHADOW (STRATEGY VALIDATION) — validating each strategy individually")
            elif shadow_progressive:
                tasks.append(
                    asyncio.create_task(self._progressive_shadow_loop(), name="progressive_shadow")
                )
                logger.info("Data mode: SHADOW (PROGRESSIVE) — starting ProgressiveShadowOrchestrator")
            else:
                tasks.append(
                    asyncio.create_task(self._shadow_mode_loop(), name="shadow_mode")
                )
                logger.info("Data mode: SHADOW — starting Shadow Mode orchestrator")
```

### Step 4: Unit Tests

**File**: `engine/tests/unit/test_strategy_validation.py` (NEW)

Test plan (minimum 15 tests):

```python
"""Tests for StrategyValidationOrchestrator (US-067).

Coverage targets:
1. StrategyResult dataclass serialization
2. StrategyValidationReport dataclass
3. Orchestrator initialization with defaults / env vars
4. Strategy isolation: disabled_strategies set correctly
5. Stats reset between runs
6. Profitability classification (PnL > 0, PnL <= 0, insufficient trades)
7. Combined validation runs only profitable strategies
8. Output file (strategy_activation.json) written correctly
9. Telegram report formatting
10. cross_exchange_v1 includes shadow_arb_v1 default signals
11. shadow_arb_v1 disabled when testing non-cross-exchange strategies
12. Edge case: zero strategies profitable
13. Edge case: all strategies profitable
14. Edge case: strategy produces zero trades
15. ShadowMode.reset_stats() clears all counters
16. ShadowMode.set_disabled_strategies() updates the set
17. ShadowMode.get_strategy_report() returns correct structure
"""

import asyncio
import json
import os
import time
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.modes.strategy_validation import (
    StrategyResult,
    StrategyValidationOrchestrator,
    StrategyValidationReport,
    _SHADOW_DEFAULT_SID,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_shadow(by_strategy: dict | None = None):
    """Create a mock ShadowMode with controllable stats."""
    shadow = MagicMock()
    shadow.start = AsyncMock()
    shadow.stop = AsyncMock()
    shadow.reset_stats = MagicMock()
    shadow.set_disabled_strategies = MagicMock()

    # Default report
    default_report = {
        "elapsed_s": 600.0,
        "total_trades": 0,
        "total_pnl": 0.0,
        "total_win_rate": 0.0,
        "max_drawdown": 0.0,
        "trades_rejected": 0,
        "trades_rate_limited": 0,
        "by_strategy": [],
    }

    if by_strategy:
        entries = []
        for sid, data in by_strategy.items():
            entries.append({
                "strategy_id": sid,
                "trades": data.get("trades", 0),
                "wins": data.get("wins", 0),
                "losses": data.get("losses", 0),
                "win_rate": data.get("win_rate", 0.0),
                "pnl": data.get("pnl", 0.0),
                "rejections": 0,
                "partial_fills": 0,
            })
        default_report["by_strategy"] = entries
        default_report["total_trades"] = sum(d.get("trades", 0) for d in by_strategy.values())
        default_report["total_pnl"] = sum(d.get("pnl", 0.0) for d in by_strategy.values())

    shadow.get_strategy_report = MagicMock(return_value=default_report)
    return shadow


# ---------------------------------------------------------------------------
# Dataclass tests
# ---------------------------------------------------------------------------

class TestDataclasses:
    def test_strategy_result_defaults(self):
        r = StrategyResult(strategy_id="test_v1")
        assert r.trades == 0
        assert r.pnl == 0.0
        assert r.profitable is False

    def test_strategy_validation_report_defaults(self):
        rpt = StrategyValidationReport()
        assert rpt.strategies == []
        assert rpt.profitable_strategies == []


# ---------------------------------------------------------------------------
# Orchestrator initialization tests
# ---------------------------------------------------------------------------

class TestOrchestratorInit:
    def test_default_strategy_ids(self):
        shadow = _make_mock_shadow()
        orch = StrategyValidationOrchestrator(shadow_mode=shadow)
        assert len(orch._strategy_ids) == 7
        assert "cross_exchange_v1" in orch._strategy_ids

    def test_custom_strategy_ids(self):
        shadow = _make_mock_shadow()
        orch = StrategyValidationOrchestrator(
            shadow_mode=shadow,
            strategy_ids=["cross_exchange_v1", "latency_arb_v1"],
        )
        assert orch._strategy_ids == ["cross_exchange_v1", "latency_arb_v1"]

    def test_env_var_duration(self, monkeypatch):
        monkeypatch.setenv("STRATEGY_VALIDATION_DURATION_S", "120")
        shadow = _make_mock_shadow()
        orch = StrategyValidationOrchestrator(shadow_mode=shadow)
        assert orch._duration_s == 120

    def test_env_var_min_trades(self, monkeypatch):
        monkeypatch.setenv("STRATEGY_VALIDATION_MIN_TRADES", "10")
        shadow = _make_mock_shadow()
        orch = StrategyValidationOrchestrator(shadow_mode=shadow)
        assert orch._min_trades == 10


# ---------------------------------------------------------------------------
# Strategy isolation tests
# ---------------------------------------------------------------------------

class TestStrategyIsolation:
    @pytest.mark.asyncio
    async def test_cross_exchange_allows_shadow_arb_v1(self, monkeypatch, tmp_path):
        """cross_exchange_v1 isolation should NOT disable shadow_arb_v1."""
        monkeypatch.setenv("STRATEGY_VALIDATION_DURATION_S", "0")
        monkeypatch.setenv("STRATEGY_VALIDATION_HYDRATION_S", "0")

        shadow = _make_mock_shadow({
            "cross_exchange_v1": {"trades": 10, "wins": 8, "pnl": 5.0, "win_rate": 0.8},
        })
        orch = StrategyValidationOrchestrator(
            shadow_mode=shadow,
            strategy_ids=["cross_exchange_v1"],
            duration_s=0,
            run_combined=False,
            output_path=tmp_path / "activation.json",
        )

        await orch.run()

        # Verify shadow_arb_v1 was NOT in the disabled set for cross_exchange
        call_args = shadow.set_disabled_strategies.call_args_list[0]
        disabled_set = call_args[0][0]
        assert _SHADOW_DEFAULT_SID not in disabled_set

    @pytest.mark.asyncio
    async def test_non_cross_exchange_disables_shadow_arb_v1(self, monkeypatch, tmp_path):
        """Non-cross-exchange strategy isolation should disable shadow_arb_v1."""
        monkeypatch.setenv("STRATEGY_VALIDATION_DURATION_S", "0")
        monkeypatch.setenv("STRATEGY_VALIDATION_HYDRATION_S", "0")

        shadow = _make_mock_shadow({
            "latency_arb_v1": {"trades": 10, "wins": 10, "pnl": 3.0, "win_rate": 1.0},
        })
        orch = StrategyValidationOrchestrator(
            shadow_mode=shadow,
            strategy_ids=["latency_arb_v1"],
            duration_s=0,
            run_combined=False,
            output_path=tmp_path / "activation.json",
        )

        await orch.run()

        call_args = shadow.set_disabled_strategies.call_args_list[0]
        disabled_set = call_args[0][0]
        assert _SHADOW_DEFAULT_SID in disabled_set


# ---------------------------------------------------------------------------
# Profitability classification tests
# ---------------------------------------------------------------------------

class TestProfitabilityClassification:
    @pytest.mark.asyncio
    async def test_profitable_strategy(self, monkeypatch, tmp_path):
        monkeypatch.setenv("STRATEGY_VALIDATION_DURATION_S", "0")
        monkeypatch.setenv("STRATEGY_VALIDATION_HYDRATION_S", "0")

        shadow = _make_mock_shadow({
            "cross_exchange_v1": {"trades": 20, "wins": 15, "pnl": 10.0, "win_rate": 0.75},
        })
        orch = StrategyValidationOrchestrator(
            shadow_mode=shadow,
            strategy_ids=["cross_exchange_v1"],
            duration_s=0,
            run_combined=False,
            output_path=tmp_path / "activation.json",
        )

        report = await orch.run()
        assert "cross_exchange_v1" in report.profitable_strategies
        assert report.strategies[0].profitable is True

    @pytest.mark.asyncio
    async def test_unprofitable_strategy(self, monkeypatch, tmp_path):
        monkeypatch.setenv("STRATEGY_VALIDATION_DURATION_S", "0")
        monkeypatch.setenv("STRATEGY_VALIDATION_HYDRATION_S", "0")

        shadow = _make_mock_shadow({
            "spot_futures_v1": {"trades": 20, "wins": 5, "pnl": -8.0, "win_rate": 0.25},
        })
        orch = StrategyValidationOrchestrator(
            shadow_mode=shadow,
            strategy_ids=["spot_futures_v1"],
            duration_s=0,
            run_combined=False,
            output_path=tmp_path / "activation.json",
        )

        report = await orch.run()
        assert "spot_futures_v1" in report.unprofitable_strategies
        assert report.strategies[0].profitable is False

    @pytest.mark.asyncio
    async def test_insufficient_trades(self, monkeypatch, tmp_path):
        monkeypatch.setenv("STRATEGY_VALIDATION_DURATION_S", "0")
        monkeypatch.setenv("STRATEGY_VALIDATION_HYDRATION_S", "0")

        shadow = _make_mock_shadow({
            "triangular_v1": {"trades": 2, "wins": 2, "pnl": 0.5, "win_rate": 1.0},
        })
        orch = StrategyValidationOrchestrator(
            shadow_mode=shadow,
            strategy_ids=["triangular_v1"],
            duration_s=0,
            min_trades=5,
            run_combined=False,
            output_path=tmp_path / "activation.json",
        )

        report = await orch.run()
        assert "triangular_v1" in report.unprofitable_strategies
        assert "insufficient_data" in report.strategies[0].reason


# ---------------------------------------------------------------------------
# Output file tests
# ---------------------------------------------------------------------------

class TestOutputFile:
    @pytest.mark.asyncio
    async def test_activation_json_written(self, monkeypatch, tmp_path):
        monkeypatch.setenv("STRATEGY_VALIDATION_DURATION_S", "0")
        monkeypatch.setenv("STRATEGY_VALIDATION_HYDRATION_S", "0")

        shadow = _make_mock_shadow({
            "cross_exchange_v1": {"trades": 10, "wins": 8, "pnl": 5.0, "win_rate": 0.8},
            "spot_futures_v1": {"trades": 10, "wins": 3, "pnl": -2.0, "win_rate": 0.3},
        })
        output = tmp_path / "strategy_activation.json"
        orch = StrategyValidationOrchestrator(
            shadow_mode=shadow,
            strategy_ids=["cross_exchange_v1", "spot_futures_v1"],
            duration_s=0,
            run_combined=False,
            output_path=output,
        )

        await orch.run()

        assert output.exists()
        data = json.loads(output.read_text())
        assert "cross_exchange_v1" in data["active_strategies"]
        assert "spot_futures_v1" in data["disabled_strategies"]
        assert "shadow_disabled_env" in data
        assert data["results"]["cross_exchange_v1"]["profitable"] is True

    @pytest.mark.asyncio
    async def test_all_strategies_unprofitable(self, monkeypatch, tmp_path):
        monkeypatch.setenv("STRATEGY_VALIDATION_DURATION_S", "0")
        monkeypatch.setenv("STRATEGY_VALIDATION_HYDRATION_S", "0")

        shadow = _make_mock_shadow({
            "cross_exchange_v1": {"trades": 10, "wins": 2, "pnl": -5.0, "win_rate": 0.2},
        })
        orch = StrategyValidationOrchestrator(
            shadow_mode=shadow,
            strategy_ids=["cross_exchange_v1"],
            duration_s=0,
            run_combined=True,  # should skip combined since no profitable
            output_path=tmp_path / "activation.json",
        )

        report = await orch.run()
        assert len(report.profitable_strategies) == 0
        assert report.combined_result is None


# ---------------------------------------------------------------------------
# ShadowMode method tests (reset_stats, set_disabled, get_strategy_report)
# ---------------------------------------------------------------------------

class TestShadowModeNewMethods:
    def test_reset_stats_clears_counters(self, monkeypatch):
        """reset_stats() should zero all counters."""
        from src.modes.shadow import ShadowMode, ShadowStats, StrategyStats

        shadow = ShadowMode(
            signal_generator=MagicMock(),
            paper_executor=MagicMock(),
        )
        # Simulate some activity
        shadow._stats.trades_executed = 50
        shadow._stats.total_pnl = 100.0
        shadow._stats.by_strategy["test"] = StrategyStats(trades=10, pnl=5.0)

        shadow.reset_stats()

        assert shadow._stats.trades_executed == 0
        assert shadow._stats.total_pnl == 0.0
        assert len(shadow._stats.by_strategy) == 0

    def test_set_disabled_strategies(self, monkeypatch):
        """set_disabled_strategies() should replace the disabled set."""
        from src.modes.shadow import ShadowMode

        shadow = ShadowMode(
            signal_generator=MagicMock(),
            paper_executor=MagicMock(),
        )
        shadow.set_disabled_strategies({"a", "b"})
        assert shadow._disabled_strategies == {"a", "b"}

        shadow.set_disabled_strategies({"c"})
        assert shadow._disabled_strategies == {"c"}

    def test_get_strategy_report_structure(self, monkeypatch):
        """get_strategy_report() should return valid dict structure."""
        from src.modes.shadow import ShadowMode, StrategyStats

        shadow = ShadowMode(
            signal_generator=MagicMock(),
            paper_executor=MagicMock(),
        )
        shadow._stats.trades_executed = 5
        shadow._stats.trades_won = 3
        shadow._stats.trades_lost = 2
        shadow._stats.total_pnl = 1.5
        shadow._stats.by_strategy["test_v1"] = StrategyStats(
            signals=5, trades=5, wins=3, losses=2, pnl=1.5,
        )

        report = shadow.get_strategy_report()

        assert "elapsed_s" in report
        assert report["total_trades"] == 5
        assert report["total_pnl"] == 1.5
        assert len(report["by_strategy"]) == 1
        assert report["by_strategy"][0]["strategy_id"] == "test_v1"
        assert report["by_strategy"][0]["pnl"] == 1.5
```

### Step 5: Integration Test

**File**: `engine/tests/integration/test_strategy_validation_integration.py` (NEW)

```python
"""Integration test: StrategyValidationOrchestrator with real ShadowMode wiring.

Verifies that:
1. ShadowMode can be started/stopped by the orchestrator
2. reset_stats() + set_disabled_strategies() work on a real ShadowMode
3. get_strategy_report() returns data after stats manipulation
4. Output file is written with correct structure

Uses mocked collectors (no real WS connections).
"""
import asyncio
import json

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.modes.shadow import ShadowMode, StrategyStats
from src.modes.strategy_validation import StrategyValidationOrchestrator


@pytest.mark.asyncio
async def test_orchestrator_lifecycle(tmp_path, monkeypatch):
    """Full lifecycle: start -> validate -> stop -> output written."""
    monkeypatch.setenv("STRATEGY_VALIDATION_DURATION_S", "0")
    monkeypatch.setenv("STRATEGY_VALIDATION_HYDRATION_S", "0")

    # Create real ShadowMode with mocked dependencies
    mock_collector = MagicMock()
    mock_collector.start = AsyncMock()
    mock_collector.stop = AsyncMock()
    mock_collector.get_collector = MagicMock(return_value=None)

    shadow = ShadowMode(
        signal_generator=MagicMock(),
        paper_executor=MagicMock(),
        collector_manager=mock_collector,
    )

    # Inject fake stats to simulate trade activity
    shadow._stats.trades_executed = 20
    shadow._stats.trades_won = 15
    shadow._stats.total_pnl = 10.0
    shadow._stats.by_strategy["cross_exchange_v1"] = StrategyStats(
        signals=20, trades=20, wins=15, losses=5, pnl=10.0,
    )

    output = tmp_path / "activation.json"

    orch = StrategyValidationOrchestrator(
        shadow_mode=shadow,
        strategy_ids=["cross_exchange_v1"],
        duration_s=0,
        run_combined=False,
        output_path=output,
    )

    report = await orch.run()

    # Verify lifecycle calls
    mock_collector.start.assert_called_once()
    mock_collector.stop.assert_called_once()

    # Verify output file
    assert output.exists()
    data = json.loads(output.read_text())
    assert "active_strategies" in data
    assert "results" in data
```

---

## Environment Variables

| Env Var | Default | Description |
|---------|---------|-------------|
| `STRATEGY_VALIDATION` | `false` | Enable strategy validation mode (set `true`) |
| `STRATEGY_VALIDATION_DURATION_S` | `600` | Seconds per strategy (10 minutes) |
| `STRATEGY_VALIDATION_COMBINED_DURATION_S` | `600` | Seconds for combined validation |
| `STRATEGY_VALIDATION_MIN_TRADES` | `5` | Minimum trades to classify a strategy |
| `STRATEGY_VALIDATION_HYDRATION_S` | `30` | Seconds to wait for orderbook hydration |
| `STRATEGY_ACTIVATION_PATH` | `config/strategy_activation.json` | Output path |

---

## Execution Flow

```
Engine.run() with DATA_MODE=shadow, STRATEGY_VALIDATION=true
  |
  v
_strategy_validation_loop()
  |
  v
StrategyValidationOrchestrator.run()
  |
  +---> ShadowMode.start() [WS connections open, collectors running]
  |
  +---> await 30s hydration
  |
  +---> FOR each strategy_id in [cross_exchange_v1, spot_futures_v1, ...]:
  |       |
  |       +---> set_disabled_strategies(all EXCEPT this one)
  |       +---> reset_stats()
  |       +---> await duration_s
  |       +---> get_strategy_report() -> classify: profitable / unprofitable / insufficient
  |
  +---> Combined validation (profitable strategies only)
  |       +---> set_disabled_strategies(unprofitable only)
  |       +---> reset_stats()
  |       +---> await combined_duration_s
  |       +---> get_strategy_report()
  |
  +---> ShadowMode.stop()
  |
  +---> Write config/strategy_activation.json
  +---> Send Telegram report
  +---> Log SHADOW_DISABLED_STRATEGIES recommendation
```

---

## How To Run

```bash
# From engine/ directory:
cd engine

# Individual strategy validation (10min each, ~80min total)
DATA_MODE=shadow STRATEGY_VALIDATION=true python -m src.main

# Quick validation (2min each, ~16min total)
DATA_MODE=shadow STRATEGY_VALIDATION=true STRATEGY_VALIDATION_DURATION_S=120 python -m src.main

# Ultra-fast for testing (10s each)
DATA_MODE=shadow STRATEGY_VALIDATION=true STRATEGY_VALIDATION_DURATION_S=10 STRATEGY_VALIDATION_HYDRATION_S=5 python -m src.main

# Run tests
python -m pytest tests/unit/test_strategy_validation.py -v
python -m pytest tests/integration/test_strategy_validation_integration.py -v
```

---

## Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| WS disconnection during 80min run | ShadowMode collectors have auto-reconnect built in |
| Strategy produces no signals in 10min | Classified as "insufficient_data" (not profitable), logged clearly |
| Stats bleed between runs | `reset_stats()` creates fresh ShadowStats; by_strategy dict is empty |
| cross_exchange signals via shadow_arb_v1 default | Explicitly handled: shadow_arb_v1 allowed only for cross_exchange_v1 isolation |
| Existing tests broken | All new code; no modification to existing logic paths |

---

## Acceptance Criteria Mapping

| AC | Implementation |
|----|---------------|
| 1. Each strategy individually activated for 1H Shadow | `_validate_single_strategy()` with `set_disabled_strategies()` isolation (10min default, configurable) |
| 2. PnL > 0 strategies included in activation list | `_report.profitable_strategies` based on `pnl > 0 AND trades >= min_trades` |
| 3. PnL <= 0 strategies disabled + logged | `_report.unprofitable_strategies` + structured logging at INFO level |
| 4. Per-strategy report generated | `strategy_activation.json` with full breakdown per strategy |
| 5. Combined 1H Shadow with profitable strategies | `_validate_combined()` runs all profitable strategies together |
