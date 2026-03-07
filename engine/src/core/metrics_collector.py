"""Real-time performance metrics collector.

Tracks PnL, Sharpe ratio, maximum drawdown, win rate, and per-strategy
statistics during live or paper trading.
"""
from __future__ import annotations

import math
import time
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class TradeRecord:
    """Single trade record for metrics computation."""

    strategy_id: str
    pnl: float
    timestamp: float = field(default_factory=time.time)


@dataclass
class StrategyMetrics:
    """Metrics for a single strategy."""

    strategy_id: str
    total_pnl: float = 0.0
    num_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    avg_pnl: float = 0.0
    max_win: float = 0.0
    max_loss: float = 0.0

    def to_dict(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "total_pnl": round(self.total_pnl, 6),
            "num_trades": self.num_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": round(self.win_rate, 4),
            "avg_pnl": round(self.avg_pnl, 6),
            "max_win": round(self.max_win, 6),
            "max_loss": round(self.max_loss, 6),
        }


@dataclass
class PerformanceReport:
    """Complete performance report."""

    total_pnl: float
    realized_pnl: float
    unrealized_pnl: float
    sharpe_ratio: float
    max_drawdown: float
    max_drawdown_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    profit_factor: float
    avg_trade_pnl: float
    duration_seconds: float
    strategy_metrics: dict[str, StrategyMetrics]

    def to_dict(self) -> dict:
        return {
            "total_pnl": round(self.total_pnl, 6),
            "realized_pnl": round(self.realized_pnl, 6),
            "unrealized_pnl": round(self.unrealized_pnl, 6),
            "sharpe_ratio": round(self.sharpe_ratio, 4),
            "max_drawdown": round(self.max_drawdown, 6),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": round(self.win_rate, 4),
            "profit_factor": round(self.profit_factor, 4),
            "avg_trade_pnl": round(self.avg_trade_pnl, 6),
            "duration_seconds": round(self.duration_seconds, 1),
            "strategies": {
                k: v.to_dict() for k, v in self.strategy_metrics.items()
            },
        }

    def passes_beta_gate(self) -> bool:
        """Check if results pass Beta gate criteria.

        Beta Gate: Net PnL > 0, Profit Factor > 1.2, MDD < 2%
        """
        return (
            self.total_pnl > 0
            and self.profit_factor > 1.2
            and abs(self.max_drawdown_pct) < 0.02
        )

    def summary(self) -> str:
        """Human-readable performance summary."""
        gate = "PASS" if self.passes_beta_gate() else "FAIL"
        lines = [
            "=" * 60,
            "LEVIATHAN Performance Report",
            "=" * 60,
            f"Duration:        {self.duration_seconds:.0f}s",
            f"Total PnL:       ${self.total_pnl:.4f}",
            f"  Realized:      ${self.realized_pnl:.4f}",
            f"  Unrealized:    ${self.unrealized_pnl:.4f}",
            f"Sharpe Ratio:    {self.sharpe_ratio:.4f}",
            f"Max Drawdown:    {self.max_drawdown_pct * 100:.2f}%",
            f"Total Trades:    {self.total_trades}",
            f"Win Rate:        {self.win_rate * 100:.1f}%",
            f"Profit Factor:   {self.profit_factor:.2f}",
            f"Avg Trade PnL:   ${self.avg_trade_pnl:.6f}",
            "-" * 60,
            f"Beta Gate:       [{gate}]",
            f"  Net PnL > 0:           {'Y' if self.total_pnl > 0 else 'N'}",
            f"  Profit Factor > 1.2:   {'Y' if self.profit_factor > 1.2 else 'N'}",
            f"  MDD < 2%:              {'Y' if abs(self.max_drawdown_pct) < 0.02 else 'N'}",
        ]

        if self.strategy_metrics:
            lines.append("-" * 60)
            lines.append("Per-Strategy Breakdown:")
            for sid, m in self.strategy_metrics.items():
                lines.append(
                    f"  {sid}: PnL=${m.total_pnl:.4f} "
                    f"trades={m.num_trades} win={m.win_rate * 100:.0f}%"
                )

        lines.append("=" * 60)
        return "\n".join(lines)


class MetricsCollector:
    """Collects and computes real-time trading metrics.

    Thread-safe for use in async contexts (single-threaded event loop).
    """

    def __init__(self, initial_capital: float = 70.0) -> None:
        self._initial_capital = initial_capital
        self._start_time = time.time()

        # Trade records
        self._trades: list[TradeRecord] = []
        self._strategy_trades: dict[str, list[TradeRecord]] = defaultdict(list)

        # Equity tracking
        self._equity_curve: list[float] = [initial_capital]
        self._peak_equity: float = initial_capital
        self._current_equity: float = initial_capital

        # Running PnL
        self._realized_pnl: float = 0.0
        self._unrealized_pnl: float = 0.0

        # Returns for Sharpe
        self._returns: list[float] = []

    def record_trade(self, strategy_id: str, pnl: float) -> None:
        """Record a completed trade."""
        record = TradeRecord(strategy_id=strategy_id, pnl=pnl)
        self._trades.append(record)
        self._strategy_trades[strategy_id].append(record)

        self._realized_pnl += pnl
        self._current_equity += pnl
        self._equity_curve.append(self._current_equity)

        if self._current_equity > self._peak_equity:
            self._peak_equity = self._current_equity

        # Compute return
        prev = self._equity_curve[-2] if len(self._equity_curve) >= 2 else self._initial_capital
        if prev > 0:
            self._returns.append((self._current_equity - prev) / prev)

    def update_unrealized(self, unrealized_pnl: float) -> None:
        """Update unrealized PnL (from PositionManager)."""
        self._unrealized_pnl = unrealized_pnl

    def get_report(self) -> PerformanceReport:
        """Generate a complete performance report."""
        duration = time.time() - self._start_time
        total_trades = len(self._trades)
        winning = sum(1 for t in self._trades if t.pnl > 0)
        losing = sum(1 for t in self._trades if t.pnl <= 0)
        win_rate = winning / total_trades if total_trades > 0 else 0.0

        # Profit factor
        gross_profit = sum(t.pnl for t in self._trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in self._trades if t.pnl < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (
            float("inf") if gross_profit > 0 else 0.0
        )

        avg_pnl = self._realized_pnl / total_trades if total_trades > 0 else 0.0

        # Max drawdown
        mdd, mdd_pct = self._compute_max_drawdown()

        # Sharpe ratio (annualized, assuming ~525600 1-min candles per year)
        sharpe = self._compute_sharpe()

        # Per-strategy metrics
        strategy_metrics: dict[str, StrategyMetrics] = {}
        for sid, trades in self._strategy_trades.items():
            s_pnl = sum(t.pnl for t in trades)
            s_win = sum(1 for t in trades if t.pnl > 0)
            s_lose = sum(1 for t in trades if t.pnl <= 0)
            s_total = len(trades)
            pnl_values = [t.pnl for t in trades]

            strategy_metrics[sid] = StrategyMetrics(
                strategy_id=sid,
                total_pnl=s_pnl,
                num_trades=s_total,
                winning_trades=s_win,
                losing_trades=s_lose,
                win_rate=s_win / s_total if s_total > 0 else 0.0,
                avg_pnl=s_pnl / s_total if s_total > 0 else 0.0,
                max_win=max(pnl_values) if pnl_values else 0.0,
                max_loss=min(pnl_values) if pnl_values else 0.0,
            )

        total_pnl = self._realized_pnl + self._unrealized_pnl

        return PerformanceReport(
            total_pnl=total_pnl,
            realized_pnl=self._realized_pnl,
            unrealized_pnl=self._unrealized_pnl,
            sharpe_ratio=sharpe,
            max_drawdown=mdd,
            max_drawdown_pct=mdd_pct,
            total_trades=total_trades,
            winning_trades=winning,
            losing_trades=losing,
            win_rate=win_rate,
            profit_factor=profit_factor,
            avg_trade_pnl=avg_pnl,
            duration_seconds=duration,
            strategy_metrics=strategy_metrics,
        )

    def _compute_max_drawdown(self) -> tuple[float, float]:
        """Compute maximum drawdown in absolute and percentage terms."""
        if len(self._equity_curve) < 2:
            return 0.0, 0.0

        peak = self._initial_capital
        max_dd = 0.0
        max_dd_pct = 0.0

        for eq in self._equity_curve:
            if eq > peak:
                peak = eq
            dd = peak - eq
            dd_pct = dd / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd
            if dd_pct > max_dd_pct:
                max_dd_pct = dd_pct

        return max_dd, max_dd_pct

    def _compute_sharpe(self, periods_per_year: int = 252) -> float:
        """Compute annualized Sharpe ratio from returns."""
        if len(self._returns) < 2:
            return 0.0
        mean_r = sum(self._returns) / len(self._returns)
        var = sum((r - mean_r) ** 2 for r in self._returns) / (len(self._returns) - 1)
        std = math.sqrt(var) if var > 0 else 0.0
        if std == 0:
            return 0.0
        return (mean_r / std) * math.sqrt(periods_per_year)
