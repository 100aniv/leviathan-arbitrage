"""Per-strategy isolated Shadow validation orchestrator (US-067)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time

from src.core.config import get_settings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Strategy ID list (registration order from main.py)
ALL_STRATEGY_IDS = [
    "cross_exchange_v1",
    "spot_futures_v1",
    "futures_futures_v1",
    "triangular_v1",
    "funding_rate_v1",
    "statistical_arb_v1",
    "latency_arb_v1",
]

# cross_exchange special handling: SignalGenerator emits without strategy_id → mapped to shadow_arb_v1
SHADOW_ARB_ALIAS = "shadow_arb_v1"

# Mapping from registration ID → signal.strategy_id used in by_strategy dict
# (see handoff US-067 strategy ID mapping table)
STRATEGY_SIGNAL_ID_MAP: dict[str, str] = {
    "cross_exchange_v1": SHADOW_ARB_ALIAS,
    "spot_futures_v1": "spot_futures_basis",
    "futures_futures_v1": "futures_futures_spread",
    "triangular_v1": "triangular",
    "funding_rate_v1": "funding_rate_arb",
    "statistical_arb_v1": "statistical_arb_zscore",
    "latency_arb_v1": "latency_arb",
}


@dataclass
class StrategyResult:
    strategy_id: str
    trades: int = 0
    pnl: float = 0.0
    win_rate: float = 0.0
    wins: int = 0
    losses: int = 0
    profitable: bool = False
    reason: str = ""
    elapsed_s: float = 0.0


@dataclass
class StrategyValidationReport:
    timestamp: str = ""
    duration_per_strategy_s: int = 600
    min_trades_threshold: int = 5
    strategies: dict[str, StrategyResult] = field(default_factory=dict)
    profitable: list[str] = field(default_factory=list)
    unprofitable: list[str] = field(default_factory=list)
    insufficient_data: list[str] = field(default_factory=list)
    combined_result: dict[str, Any] | None = None


class StrategyValidationOrchestrator:
    """Iterates through strategies, validates each in isolation, produces activation config."""

    def __init__(self, shadow_mode: Any, telegram_sender: Any | None = None):
        self._shadow = shadow_mode
        self._telegram = telegram_sender

        # Config from settings singleton
        _op = get_settings().operational
        self._duration_s = _op.strategy_validation_duration_s
        self._combined_duration_s = _op.strategy_validation_combined_duration_s
        self._min_trades = _op.strategy_validation_min_trades
        self._hydration_s = _op.strategy_validation_hydration_s
        self._output_path = Path(_op.strategy_activation_path)

        self._report = StrategyValidationReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
            duration_per_strategy_s=self._duration_s,
            min_trades_threshold=self._min_trades,
        )

    async def run(self) -> StrategyValidationReport:
        """Full validation pipeline."""
        logger.info("=" * 60)
        logger.info("StrategyValidationOrchestrator: Starting per-strategy validation")
        logger.info(
            "Duration per strategy: %ds, Min trades: %d",
            self._duration_s, self._min_trades,
        )
        logger.info("=" * 60)

        # Phase 1: Hydrate orderbooks (wait for WS data)
        logger.info("Hydrating orderbooks for %ds...", self._hydration_s)
        await asyncio.sleep(self._hydration_s)

        # Phase 2: Validate each strategy in isolation
        for strategy_id in ALL_STRATEGY_IDS:
            result = await self._validate_single_strategy(strategy_id)
            self._report.strategies[strategy_id] = result

            if result.profitable:
                self._report.profitable.append(strategy_id)
                logger.info(
                    "PROFITABLE %s: PnL=$%+.4f WR=%.1f%% trades=%d",
                    strategy_id, result.pnl, result.win_rate * 100, result.trades,
                )
            elif result.reason.startswith("unverified/insufficient"):
                self._report.insufficient_data.append(strategy_id)
                logger.warning(
                    "UNVERIFIED %s: %d trades < %d min",
                    strategy_id, result.trades, self._min_trades,
                )
            else:
                self._report.unprofitable.append(strategy_id)
                logger.warning(
                    "UNPROFITABLE %s: PnL=$%+.4f WR=%.1f%% trades=%d",
                    strategy_id, result.pnl, result.win_rate * 100, result.trades,
                )

        # Phase 3: Combined validation with profitable strategies only
        if self._report.profitable:
            logger.info(
                "Combined validation with %d profitable strategies...",
                len(self._report.profitable),
            )
            self._report.combined_result = await self._validate_combined(self._report.profitable)
        else:
            logger.warning("No profitable strategies found! Skipping combined validation.")
            self._report.combined_result = {"error": "no_profitable_strategies"}

        # Phase 4: Write activation config
        self._write_activation_config()

        # Phase 5: Send Telegram report
        await self._send_telegram_report()

        logger.info("=" * 60)
        logger.info(
            "Validation complete: %d profitable, %d unprofitable, %d insufficient",
            len(self._report.profitable),
            len(self._report.unprofitable),
            len(self._report.insufficient_data),
        )
        logger.info("=" * 60)

        return self._report

    async def _validate_single_strategy(self, strategy_id: str) -> StrategyResult:
        """Run Shadow with only one strategy enabled."""
        logger.info("--- Validating: %s (%ds) ---", strategy_id, self._duration_s)

        # Build disabled set using SIGNAL IDs (shadow.py checks signal.strategy_id, not registration ID)
        disabled: set[str] = set()
        for sid in ALL_STRATEGY_IDS:
            if sid != strategy_id:
                signal_id = STRATEGY_SIGNAL_ID_MAP.get(sid, sid)
                disabled.add(signal_id)

        # cross_exchange special handling: SignalGenerator emits with strategy_id=None → shadow_arb_v1
        if strategy_id != "cross_exchange_v1":
            # Block shadow_arb_v1 to prevent cross_exchange signals leaking in
            disabled.add(SHADOW_ARB_ALIAS)

        # Reset and configure
        self._shadow.reset_stats()
        self._shadow.set_disabled_strategies(disabled)
        # Brief drain delay for in-flight signals to complete (M1 fix)
        await asyncio.sleep(0.1)

        # Run for duration
        start = time.monotonic()
        await asyncio.sleep(self._duration_s)
        elapsed = time.monotonic() - start

        # Collect results
        report = self._shadow.get_strategy_report()

        # Find this strategy's stats using signal ID mapping (by_strategy keyed by signal.strategy_id)
        signal_id = STRATEGY_SIGNAL_ID_MAP.get(strategy_id, strategy_id)
        stats = report.get(signal_id, {})

        trades = stats.get("trades", 0)
        pnl = stats.get("pnl", 0.0)
        win_rate = stats.get("win_rate", 0.0)
        wins = stats.get("wins", 0)
        losses = stats.get("losses", 0)

        # Classify
        # US-185: insufficient_data classified as unverified (not disabled, may activate on next run)
        if trades < self._min_trades:
            profitable = False
            reason = f"unverified/insufficient_data ({trades} trades < {self._min_trades} min)"
        elif pnl > 0:
            profitable = True
            reason = f"profitable (PnL=${pnl:+.4f}, WR={win_rate:.1%})"
        else:
            profitable = False
            reason = f"unprofitable (PnL=${pnl:+.4f}, WR={win_rate:.1%})"

        return StrategyResult(
            strategy_id=strategy_id,
            trades=trades,
            pnl=pnl,
            win_rate=win_rate,
            wins=wins,
            losses=losses,
            profitable=profitable,
            reason=reason,
            elapsed_s=elapsed,
        )

    async def _validate_combined(self, profitable_ids: list[str]) -> dict:
        """Run Shadow with only profitable strategies."""
        logger.info(
            "--- Combined validation: %s (%ds) ---",
            profitable_ids, self._combined_duration_s,
        )

        # Build disabled set using SIGNAL IDs (same as _validate_single_strategy)
        disabled: set[str] = set()
        for sid in ALL_STRATEGY_IDS:
            if sid not in profitable_ids:
                signal_id = STRATEGY_SIGNAL_ID_MAP.get(sid, sid)
                disabled.add(signal_id)

        # Handle shadow_arb_v1 for cross_exchange
        if "cross_exchange_v1" not in profitable_ids:
            disabled.add(SHADOW_ARB_ALIAS)

        self._shadow.reset_stats()
        self._shadow.set_disabled_strategies(disabled)
        await asyncio.sleep(0.1)  # drain in-flight signals

        start = time.monotonic()
        await asyncio.sleep(self._combined_duration_s)
        elapsed = time.monotonic() - start

        report = self._shadow.get_strategy_report()

        total_trades = sum(s.get("trades", 0) for s in report.values())
        total_pnl = sum(s.get("pnl", 0.0) for s in report.values())
        total_wins = sum(s.get("wins", 0) for s in report.values())
        total_wr = total_wins / total_trades if total_trades > 0 else 0.0

        return {
            "elapsed_s": elapsed,
            "total_trades": total_trades,
            "total_pnl": total_pnl,
            "total_win_rate": total_wr,
            "per_strategy": report,
        }

    def _write_activation_config(self) -> None:
        """Write strategy activation config to JSON file."""
        self._output_path.parent.mkdir(parents=True, exist_ok=True)

        results_dict = {}
        for sid, result in self._report.strategies.items():
            # US-185: status field (enabled/disabled/unverified)
            if result.profitable:
                status = "enabled"
            elif result.reason.startswith("unverified/insufficient"):
                status = "unverified"
            else:
                status = "disabled"
            results_dict[sid] = {
                "status": status,
                "profitable": result.profitable,
                "trades": result.trades,
                "pnl": result.pnl,
                "win_rate": result.win_rate,
                "reason": result.reason,
                "elapsed_s": result.elapsed_s,
            }

        # US-185: unverified strategies are NOT disabled — they may activate on next run
        config = {
            "_meta": {
                "source": "US-067 StrategyValidationOrchestrator",
                "date": self._report.timestamp,
                "duration_per_strategy_s": self._report.duration_per_strategy_s,
                "min_trades_threshold": self._report.min_trades_threshold,
            },
            "active_strategies": self._report.profitable,
            "unverified_strategies": self._report.insufficient_data,
            "disabled_strategies": self._report.unprofitable,
            "shadow_disabled_env": ",".join(self._report.unprofitable),
            "results": results_dict,
            "combined_validation": self._report.combined_result,
        }

        self._output_path.write_text(json.dumps(config, indent=2, default=str))
        logger.info("Strategy activation config written to %s", self._output_path)

    async def _send_telegram_report(self) -> None:
        """Send summary via Telegram if configured."""
        if self._telegram is None:
            return

        lines = ["🔍 *Strategy Validation Report*\n"]

        if self._report.profitable:
            lines.append("✅ *Profitable:*")
            for sid in self._report.profitable:
                r = self._report.strategies[sid]
                lines.append(f"  • {sid}: PnL=${r.pnl:+.2f}, WR={r.win_rate:.1%}, {r.trades}T")

        if self._report.unprofitable:
            lines.append("\n❌ *Unprofitable:*")
            for sid in self._report.unprofitable:
                r = self._report.strategies[sid]
                lines.append(f"  • {sid}: PnL=${r.pnl:+.2f}, WR={r.win_rate:.1%}, {r.trades}T")

        if self._report.insufficient_data:
            lines.append("\n⚠️ *Insufficient Data:*")
            for sid in self._report.insufficient_data:
                r = self._report.strategies[sid]
                lines.append(f"  • {sid}: {r.trades} trades (min={self._min_trades})")

        if self._report.combined_result and "error" not in self._report.combined_result:
            c = self._report.combined_result
            lines.append(
                f"\n📊 *Combined:* {c['total_trades']}T, "
                f"PnL=${c['total_pnl']:+.2f}, WR={c['total_win_rate']:.1%}"
            )

        try:
            await self._telegram.send_alert("\n".join(lines))
        except Exception as e:
            logger.warning("Failed to send Telegram report: %s", e)
