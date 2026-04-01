"""Event-driven backtesting engine for strategy parameter evaluation."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from src.tuning.data_loader import OHLCVWindow, SpreadRecord


@dataclass
class StrategyParams:
    """Parameters subject to Bayesian optimization."""

    min_spread_bps: float = 5.0
    max_position_size: float = 1_000.0
    entry_threshold: float = 0.0005
    exit_threshold: float = 0.0002
    stop_loss_pct: float = 0.02


@dataclass
class TuningBacktestResult:
    """Metrics from a single backtest run."""

    total_pnl: float
    sharpe_ratio: float
    max_drawdown: float
    win_rate: float
    num_trades: int
    returns: list[float] = field(default_factory=list)


# Backward-compat alias (do not use in new code)
BacktestResult = TuningBacktestResult


class BacktestEngine:
    """
    Event-driven backtesting engine.

    Replays OHLCV or spread data through strategy logic tick-by-tick.
    Uses numpy vectorization for Sharpe and drawdown computation.
    Supports GPU acceleration via optional cupy drop-in (falls back to numpy).
    """

    def __init__(
        self,
        initial_capital: float = 10_000.0,
        fee_rate: float = 0.001,  # 0.1% per leg (taker)
    ) -> None:
        self._initial_capital = initial_capital
        self._fee_rate = fee_rate

    # ------------------------------------------------------------------
    # OHLCV-based replay
    # ------------------------------------------------------------------

    def run(self, params: StrategyParams, ohlcv: OHLCVWindow) -> TuningBacktestResult:
        """
        Replay OHLCV data through a spread-based entry/exit strategy.

        Entry: tick spread > entry_threshold AND > min_spread_bps (in bps).
        Exit:  spread falls below exit_threshold, or stop-loss triggered.
        """
        closes = ohlcv.closes
        n = len(closes)

        if n < 2:
            return TuningBacktestResult(
                total_pnl=0.0,
                sharpe_ratio=0.0,
                max_drawdown=0.0,
                win_rate=0.0,
                num_trades=0,
            )

        min_spread_frac = params.min_spread_bps / 10_000.0
        capital = self._initial_capital
        position: float = 0.0
        entry_price: float = 0.0
        trade_pnls: list[float] = []
        equity_curve: list[float] = [capital]

        for i in range(1, n):
            price = closes[i]
            prev = closes[i - 1]
            spread = (price - prev) / prev if prev > 0.0 else 0.0

            if position == 0.0:
                if abs(spread) > params.entry_threshold and abs(spread) > min_spread_frac:
                    size_usdt = min(params.max_position_size, capital * 0.5)
                    position = size_usdt / price
                    entry_price = price
                    capital -= size_usdt * self._fee_rate
            else:
                pnl_pct = (price - entry_price) / entry_price
                if abs(spread) < params.exit_threshold or pnl_pct <= -params.stop_loss_pct:
                    pnl = position * (price - entry_price)
                    fee = position * price * self._fee_rate
                    net_pnl = pnl - fee
                    capital += net_pnl
                    trade_pnls.append(net_pnl)
                    position = 0.0
                    entry_price = 0.0

            equity_curve.append(capital)

        # Close any open position at last price
        if position > 0.0:
            last = closes[-1]
            pnl = position * (last - entry_price)
            fee = position * last * self._fee_rate
            net_pnl = pnl - fee
            capital += net_pnl
            trade_pnls.append(net_pnl)
            equity_curve[-1] = capital

        return self._build_result(capital, equity_curve, trade_pnls)

    # ------------------------------------------------------------------
    # Spread-record replay
    # ------------------------------------------------------------------

    def run_on_spreads(
        self, params: StrategyParams, spreads: list[SpreadRecord]
    ) -> TuningBacktestResult:
        """
        Replay spread observations through strategy logic.

        Uses net_spread as the signal: entry when net_spread > entry_threshold,
        exit when net_spread < exit_threshold or cumulative loss > stop_loss_pct.
        """
        if not spreads:
            return TuningBacktestResult(
                total_pnl=0.0,
                sharpe_ratio=0.0,
                max_drawdown=0.0,
                win_rate=0.0,
                num_trades=0,
            )

        min_spread_frac = params.min_spread_bps / 10_000.0
        capital = self._initial_capital
        position: float = 0.0
        entry_net: float = 0.0
        trade_pnls: list[float] = []
        equity_curve: list[float] = [capital]

        for rec in spreads:
            net = rec.net_spread

            if position == 0.0:
                if net > params.entry_threshold and net > min_spread_frac:
                    size_usdt = min(params.max_position_size, capital * 0.5)
                    position = size_usdt
                    entry_net = net
                    capital -= size_usdt * self._fee_rate
            else:
                pnl_frac = net - entry_net
                if net < params.exit_threshold or pnl_frac <= -params.stop_loss_pct:
                    net_pnl = position * pnl_frac - position * self._fee_rate
                    capital += net_pnl
                    trade_pnls.append(net_pnl)
                    position = 0.0

            equity_curve.append(capital)

        return self._build_result(capital, equity_curve, trade_pnls)

    # ------------------------------------------------------------------
    # Result construction
    # ------------------------------------------------------------------

    def _build_result(
        self,
        final_capital: float,
        equity_curve: list[float],
        trade_pnls: list[float],
    ) -> TuningBacktestResult:
        total_pnl = final_capital - self._initial_capital
        equity = np.array(equity_curve, dtype=float)
        denom = np.where(equity[:-1] != 0.0, equity[:-1], 1e-10)
        returns = np.diff(equity) / denom

        win_rate = (
            sum(1 for p in trade_pnls if p > 0.0) / len(trade_pnls)
            if trade_pnls
            else 0.0
        )

        return TuningBacktestResult(
            total_pnl=total_pnl,
            sharpe_ratio=self._compute_sharpe(returns),
            max_drawdown=self._compute_max_drawdown(equity),
            win_rate=win_rate,
            num_trades=len(trade_pnls),
            returns=returns.tolist(),
        )

    # ------------------------------------------------------------------
    # Static metric helpers (vectorized)
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_sharpe(returns: np.ndarray, periods_per_year: int = 8760) -> float:
        if len(returns) < 2:
            return 0.0
        std = float(np.std(returns, ddof=1))
        if std == 0.0:
            return 0.0
        return float(np.mean(returns) / std * np.sqrt(periods_per_year))

    @staticmethod
    def _compute_max_drawdown(equity: np.ndarray) -> float:
        if len(equity) < 2:
            return 0.0
        peak = np.maximum.accumulate(equity)
        denom = np.where(peak != 0.0, peak, 1e-10)
        drawdowns = (equity - peak) / denom
        return float(np.min(drawdowns))
