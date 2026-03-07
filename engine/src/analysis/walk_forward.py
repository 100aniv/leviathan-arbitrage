"""Walk-Forward analysis — rolling window performance evaluation.

Splits historical signal data into rolling windows, computes per-window
metrics (Sharpe, MDD, win rate, profit per trade), and determines whether
the strategy meets the live gate threshold.

Sharpe < 2.5 → LIVE BLOCKED
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import asyncpg
import structlog

logger = structlog.get_logger(__name__)


@dataclass
class WindowResult:
    """Metrics for a single walk-forward window."""
    window_start: datetime
    window_end: datetime
    trade_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0     # as fraction (e.g. 0.05 = 5%)
    sharpe_ratio: float = 0.0
    win_rate: float = 0.0
    avg_profit_per_trade: float = 0.0
    profit_factor: float = 0.0    # gross_profit / gross_loss


@dataclass
class WalkForwardResult:
    """Aggregated walk-forward analysis result."""
    windows: list[WindowResult] = field(default_factory=list)
    overall_sharpe: float = 0.0
    overall_mdd: float = 0.0
    overall_win_rate: float = 0.0
    overall_trades: int = 0
    overall_pnl: float = 0.0
    avg_signals_per_day: float = 0.0
    live_eligible: bool = False    # True if Sharpe >= 2.5 and MDD < 5%
    block_reason: str = ""


# Sharpe threshold for live eligibility
SHARPE_GATE = 2.5
MDD_GATE = 0.05  # 5%
MIN_DAILY_SIGNALS = 100


class WalkForwardAnalyzer:
    """Rolling-window walk-forward analysis on execution_log data.

    Evaluates strategy performance using stored paper execution results:
    - Splits data into rolling windows (default 1 hour)
    - Computes Sharpe ratio, MDD, win rate per window
    - Determines live eligibility based on gates:
      * 7-day rolling Sharpe >= 2.5
      * MDD < 5%
      * Daily signals > 100

    Usage:
        analyzer = WalkForwardAnalyzer(pool)
        result = await analyzer.analyze(days=7)
        if result.live_eligible:
            print("Ready for live!")
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def analyze(
        self,
        strategy_id: str = "cross_exchange_arb_v1",
        days: int = 7,
        window_hours: int = 1,
        risk_free_rate: float = 0.0,
    ) -> WalkForwardResult:
        """Run walk-forward analysis over the specified period.

        Args:
            strategy_id: Strategy to analyze
            days: Number of days of data to use
            window_hours: Rolling window size in hours
            risk_free_rate: Annualized risk-free rate for Sharpe

        Returns:
            WalkForwardResult with per-window and overall metrics
        """
        from datetime import timedelta

        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)

        # Load execution data
        query = """
            SELECT ts, net_pnl, gross_spread_bps, fee_total, slippage_total, status
            FROM execution_log
            WHERE strategy_id = $1 AND ts >= $2 AND ts <= $3
            ORDER BY ts ASC
        """

        trades: list[dict] = []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, strategy_id, start, end)
            for row in rows:
                trades.append({
                    "ts": row["ts"],
                    "pnl": float(row["net_pnl"]) if row["net_pnl"] is not None else 0.0,
                    "status": row["status"],
                })

        if not trades:
            logger.warning("walk_forward_no_data", strategy_id=strategy_id, days=days)
            result = WalkForwardResult()
            result.block_reason = "No execution data found"
            return result

        # Split into rolling windows
        result = WalkForwardResult()
        window_delta = timedelta(hours=window_hours)
        current_start = start

        all_pnls: list[float] = []

        while current_start < end:
            current_end = current_start + window_delta
            window_trades = [t for t in trades if current_start <= t["ts"] < current_end]

            if window_trades:
                wr = self._compute_window(current_start, current_end, window_trades)
                result.windows.append(wr)
                all_pnls.extend([t["pnl"] for t in window_trades])

            current_start = current_end

        # Overall metrics
        result.overall_trades = len(trades)
        result.overall_pnl = sum(t["pnl"] for t in trades)

        wins = [t for t in trades if t["pnl"] > 0]
        result.overall_win_rate = len(wins) / len(trades) if trades else 0.0

        total_hours = (end - start).total_seconds() / 3600
        total_days_actual = total_hours / 24
        result.avg_signals_per_day = len(trades) / total_days_actual if total_days_actual > 0 else 0.0

        # Overall Sharpe (annualized from hourly window returns)
        if result.windows:
            window_returns = [w.total_pnl for w in result.windows]
            result.overall_sharpe = self._compute_sharpe(
                window_returns, risk_free_rate, periods_per_year=365 * 24 / window_hours
            )

        # Overall MDD
        result.overall_mdd = self._compute_mdd(all_pnls)

        # Live eligibility gate
        result.live_eligible = True
        reasons = []

        if result.overall_sharpe < SHARPE_GATE:
            result.live_eligible = False
            reasons.append(f"Sharpe {result.overall_sharpe:.2f} < {SHARPE_GATE}")

        if result.overall_mdd > MDD_GATE:
            result.live_eligible = False
            reasons.append(f"MDD {result.overall_mdd*100:.2f}% > {MDD_GATE*100:.0f}%")

        if result.avg_signals_per_day < MIN_DAILY_SIGNALS:
            result.live_eligible = False
            reasons.append(f"Signals/day {result.avg_signals_per_day:.0f} < {MIN_DAILY_SIGNALS}")

        result.block_reason = "; ".join(reasons) if reasons else ""

        logger.info(
            "walk_forward_complete",
            strategy_id=strategy_id,
            days=days,
            trades=result.overall_trades,
            sharpe=f"{result.overall_sharpe:.2f}",
            mdd=f"{result.overall_mdd*100:.2f}%",
            win_rate=f"{result.overall_win_rate*100:.1f}%",
            pnl=f"${result.overall_pnl:.2f}",
            live_eligible=result.live_eligible,
            block_reason=result.block_reason or "NONE",
        )

        return result

    def _compute_window(
        self, start: datetime, end: datetime, trades: list[dict]
    ) -> WindowResult:
        """Compute metrics for a single time window."""
        wr = WindowResult(window_start=start, window_end=end)
        wr.trade_count = len(trades)

        pnls = [t["pnl"] for t in trades]
        wr.total_pnl = sum(pnls)
        wr.win_count = sum(1 for p in pnls if p > 0)
        wr.loss_count = sum(1 for p in pnls if p <= 0)
        wr.win_rate = wr.win_count / wr.trade_count if wr.trade_count else 0.0
        wr.avg_profit_per_trade = wr.total_pnl / wr.trade_count if wr.trade_count else 0.0

        # Profit factor
        gross_profit = sum(p for p in pnls if p > 0)
        gross_loss = abs(sum(p for p in pnls if p < 0))
        wr.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        # MDD for this window
        wr.max_drawdown = self._compute_mdd(pnls)

        return wr

    @staticmethod
    def _compute_sharpe(
        returns: list[float],
        risk_free_rate: float = 0.0,
        periods_per_year: float = 8760,
    ) -> float:
        """Compute annualized Sharpe ratio from period returns."""
        if len(returns) < 2:
            return 0.0

        mean_r = sum(returns) / len(returns)
        var_r = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
        std_r = math.sqrt(var_r) if var_r > 0 else 0.0

        if std_r == 0:
            return 0.0

        rf_per_period = risk_free_rate / periods_per_year
        sharpe = (mean_r - rf_per_period) / std_r * math.sqrt(periods_per_year)
        return sharpe

    @staticmethod
    def _compute_mdd(pnls: list[float]) -> float:
        """Compute maximum drawdown from a sequence of PnL values."""
        if not pnls:
            return 0.0

        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0

        for pnl in pnls:
            cumulative += pnl
            if cumulative > peak:
                peak = cumulative
            dd = (peak - cumulative) / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd

        return max_dd
