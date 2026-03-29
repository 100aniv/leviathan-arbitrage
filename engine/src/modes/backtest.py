"""LEVIATHAN Backtest Mode — Historical Data Replay + SimExecutor (Phase H-2).

Replays stored orderbook snapshots from TimescaleDB through the full signal
pipeline (SignalGenerator → StrategyManager → SimExecutor), producing PnL
curves, Sharpe ratios, and per-strategy breakdowns identical to paper mode
but using past data.

Architecture:
  1. Load orderbook_snapshots from TimescaleDB (time-ordered)
  2. Replay each snapshot as an _on_orderbook() event
  3. SignalGenerator evaluates opportunities (same code as paper/live)
  4. StrategyManager.route_signal() dispatches to strategies
  5. SimExecutor (PaperExecutor) fills orders with historical slippage
  6. Results saved to BacktestResult + optionally TimescaleDB

Key principle: Strategy code is IDENTICAL across backtest/paper/shadow/live.
Only the data feed (historical vs live) and executor (sim vs real) differ.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from src.core.models import Signal
from src.core.rust_bridge import get_orderbook_class
from src.friction.fee_model import FeeModel
from src.strategies.base import TradeRequest

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    """Results from a backtest run."""
    start_time: str = ""
    end_time: str = ""
    duration_s: float = 0.0
    snapshots_replayed: int = 0
    signals_generated: int = 0
    trades_executed: int = 0
    trades_won: int = 0
    trades_lost: int = 0
    total_pnl: float = 0.0
    peak_pnl: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    profit_factor: float = 0.0
    win_rate: float = 0.0
    by_strategy: dict[str, dict] = field(default_factory=dict)
    pnl_curve: list[float] = field(default_factory=list)


class BacktestMode:
    """Backtest Mode orchestrator — replays historical orderbooks.

    Uses the same SignalGenerator and StrategyManager as paper/live modes.
    Only the data source differs (DB replay instead of live WebSocket).
    """

    def __init__(
        self,
        signal_generator: Any,
        strategy_manager: Any,
        *,
        db_pool: Any | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        symbols: list[str] | None = None,
        exchanges: list[str] | None = None,
        replay_speed: float = 0.0,  # 0 = as fast as possible
    ) -> None:
        self._signal_generator = signal_generator
        self._strategy_manager = strategy_manager
        self._db_pool = db_pool
        self._start_time = start_time
        self._end_time = end_time
        self._symbols = symbols or ["BTC/USDT"]
        self._exchanges = exchanges
        self._replay_speed = replay_speed

        self._orderbook_cls = get_orderbook_class()
        self._books: dict[str, dict[str, Any]] = {}
        self._fee_model = FeeModel()
        self._result = BacktestResult()
        self._running = False

        # Per-strategy PnL tracking
        self._strategy_pnl: dict[str, float] = {}
        self._strategy_trades: dict[str, int] = {}
        self._strategy_wins: dict[str, int] = {}

        self._pnl_returns: list[float] = []

    async def run(self) -> BacktestResult:
        """Execute the backtest: load data → replay → compute results."""
        t0 = time.monotonic()
        self._running = True

        logger.info(
            "backtest.starting start=%s end=%s symbols=%s",
            self._start_time, self._end_time, self._symbols,
        )

        # Start strategies
        if self._strategy_manager is not None:
            for sid in self._strategy_manager.list_strategies():
                s = self._strategy_manager.get_strategy(sid)
                if s:
                    s.shadow_mode = True  # Sim execution
                try:
                    await self._strategy_manager.start_strategy(sid)
                except Exception as exc:
                    logger.warning("backtest.strategy_start_failed: %s %s", sid, exc)

        # Load and replay snapshots
        snapshots = await self._load_snapshots()
        self._result.snapshots_replayed = len(snapshots)
        self._result.start_time = self._start_time or ""
        self._result.end_time = self._end_time or ""

        if not snapshots:
            logger.warning("backtest.no_snapshots — check TimescaleDB orderbook_snapshots table")
            self._result.duration_s = time.monotonic() - t0
            self._running = False
            return self._result

        logger.info("backtest.replaying snapshots=%d", len(snapshots))

        prev_ts: float = 0.0
        for i, snap in enumerate(snapshots):
            if not self._running:
                break

            # Optional replay speed throttling
            if self._replay_speed > 0 and prev_ts > 0:
                delta = snap["timestamp"] - prev_ts
                await asyncio.sleep(delta / self._replay_speed)
            prev_ts = snap.get("timestamp", 0)

            await self._replay_snapshot(snap)

            # Progress log every 10%
            if (i + 1) % max(1, len(snapshots) // 10) == 0:
                pct = (i + 1) / len(snapshots) * 100
                logger.info(
                    "backtest.progress %.0f%% (%d/%d) pnl=%.2f signals=%d trades=%d",
                    pct, i + 1, len(snapshots),
                    self._result.total_pnl, self._result.signals_generated,
                    self._result.trades_executed,
                )

        # Compute final metrics
        self._compute_metrics()
        self._result.duration_s = time.monotonic() - t0
        self._running = False

        logger.info(
            "backtest.completed duration=%.1fs snapshots=%d trades=%d pnl=%.2f sharpe=%.2f mdd=%.4f",
            self._result.duration_s, self._result.snapshots_replayed,
            self._result.trades_executed, self._result.total_pnl,
            self._result.sharpe_ratio, self._result.max_drawdown_pct,
        )

        return self._result

    def stop(self) -> None:
        """Stop backtest early."""
        self._running = False

    async def _load_snapshots(self) -> list[dict]:
        """Load orderbook snapshots from TimescaleDB."""
        if self._db_pool is None:
            logger.warning("backtest.no_db_pool — cannot load historical data")
            return []

        try:
            query = """
                SELECT exchange, symbol, bids, asks,
                       EXTRACT(EPOCH FROM timestamp) as timestamp
                FROM orderbook_snapshots
                WHERE 1=1
            """
            params: list = []
            idx = 1

            if self._start_time:
                query += f" AND timestamp >= ${idx}::timestamptz"
                params.append(self._start_time)
                idx += 1
            if self._end_time:
                query += f" AND timestamp <= ${idx}::timestamptz"
                params.append(self._end_time)
                idx += 1
            if self._symbols:
                query += f" AND symbol = ANY(${idx}::text[])"
                params.append(self._symbols)
                idx += 1
            if self._exchanges:
                query += f" AND exchange = ANY(${idx}::text[])"
                params.append(self._exchanges)
                idx += 1

            query += " ORDER BY timestamp ASC LIMIT 100000"

            async with self._db_pool.pool.acquire() as conn:
                rows = await conn.fetch(query, *params)

            snapshots = []
            for row in rows:
                snapshots.append({
                    "exchange": row["exchange"],
                    "symbol": row["symbol"],
                    "bids": row["bids"] if isinstance(row["bids"], list) else [],
                    "asks": row["asks"] if isinstance(row["asks"], list) else [],
                    "timestamp": float(row["timestamp"]),
                })

            return snapshots

        except Exception as exc:
            logger.error("backtest.load_snapshots_failed: %s", exc)
            return []

    async def _replay_snapshot(self, snap: dict) -> None:
        """Replay a single orderbook snapshot through the signal pipeline."""
        exchange_id = snap["exchange"]
        symbol = snap["symbol"]
        bids = snap["bids"]
        asks = snap["asks"]

        if not bids or not asks:
            return

        # Build CoreOrderBook
        core_book = self._orderbook_cls(symbol=symbol, exchange=exchange_id)
        core_book.apply_snapshot(
            [(str(b[0]), str(b[1])) for b in bids[:20]],
            [(str(a[0]), str(a[1])) for a in asks[:20]],
        )

        # Update book store
        if symbol not in self._books:
            self._books[symbol] = {}
        self._books[symbol][exchange_id] = core_book

        # Feed SignalGenerator (same code as paper/live)
        if self._signal_generator and len(self._books.get(symbol, {})) >= 2:
            try:
                signal = await self._signal_generator.on_orderbook_update(
                    book=core_book,
                    books=self._books.get(symbol, {}),
                )
                if signal is not None:
                    self._result.signals_generated += 1
                    await self._route_and_execute(signal)
            except Exception as exc:
                logger.debug("backtest.signal_error: %s", exc)

    async def _route_and_execute(self, signal: Signal) -> None:
        """Route signal through strategies and track PnL."""
        if self._strategy_manager is None:
            return

        try:
            trade_requests = await self._strategy_manager.route_signal(signal)
            for request in trade_requests:
                self._execute_paper_trade(request)
        except Exception as exc:
            logger.debug("backtest.route_error: %s", exc)

    def _execute_paper_trade(self, trade_request: TradeRequest) -> None:
        """Simulate trade execution with fee deduction."""
        sid = trade_request.strategy_id or "unknown"

        # Compute PnL from legs (same logic as LiveMode._compute_pnl)
        net_pnl = Decimal("0")
        from src.core.models import OrderSide
        for leg in trade_request.legs:
            price = leg.price or Decimal("0")
            notional = price * leg.size
            ex = leg.exchange_id.removeprefix("paper_").removeprefix("sandbox_")
            try:
                fee = self._fee_model.taker_fee(ex, notional)
            except ValueError:
                fee = notional * Decimal("0.0025")
            if leg.side == OrderSide.SELL:
                net_pnl += notional - fee
            else:
                net_pnl -= notional + fee

        pnl_f = float(net_pnl)

        # Update stats
        self._result.trades_executed += 1
        self._result.total_pnl += pnl_f
        self._pnl_returns.append(pnl_f)
        self._result.pnl_curve.append(self._result.total_pnl)

        if pnl_f > 0:
            self._result.trades_won += 1
        else:
            self._result.trades_lost += 1

        # Peak and drawdown
        if self._result.total_pnl > self._result.peak_pnl:
            self._result.peak_pnl = self._result.total_pnl
        dd = self._result.peak_pnl - self._result.total_pnl
        if dd > self._result.max_drawdown:
            self._result.max_drawdown = dd

        # Per-strategy
        self._strategy_pnl[sid] = self._strategy_pnl.get(sid, 0.0) + pnl_f
        self._strategy_trades[sid] = self._strategy_trades.get(sid, 0) + 1
        if pnl_f > 0:
            self._strategy_wins[sid] = self._strategy_wins.get(sid, 0) + 1

    def _compute_metrics(self) -> None:
        """Compute final Sharpe, MDD%, profit factor, win rate."""
        n = self._result.trades_executed
        if n == 0:
            return

        # Win rate
        self._result.win_rate = self._result.trades_won / n

        # MDD %
        if self._result.peak_pnl > 0:
            self._result.max_drawdown_pct = self._result.max_drawdown / self._result.peak_pnl

        # Profit factor
        wins_sum = sum(r for r in self._pnl_returns if r > 0)
        loss_sum = abs(sum(r for r in self._pnl_returns if r < 0))
        self._result.profit_factor = wins_sum / max(0.01, loss_sum)

        # Sharpe ratio (annualized, assuming 1-minute intervals)
        import numpy as np
        if len(self._pnl_returns) >= 2:
            returns = np.array(self._pnl_returns)
            mean_r = np.mean(returns)
            std_r = np.std(returns, ddof=1)
            if std_r > 0:
                # Annualize: ~525,600 minutes/year
                self._result.sharpe_ratio = float(mean_r / std_r * np.sqrt(525600))

        # Per-strategy breakdown
        for sid in self._strategy_pnl:
            trades = self._strategy_trades.get(sid, 0)
            wins = self._strategy_wins.get(sid, 0)
            self._result.by_strategy[sid] = {
                "pnl": round(self._strategy_pnl[sid], 4),
                "trades": trades,
                "wins": wins,
                "win_rate": round(wins / max(1, trades), 3),
            }

    @property
    def result(self) -> BacktestResult:
        return self._result
