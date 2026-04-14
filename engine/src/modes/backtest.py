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
import os
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
    error: str = ""  # "insufficient_data" if no snapshots found
    # US-361 meta fields
    strategy_ids: list[str] = field(default_factory=list)
    exchange_ids: list[str] = field(default_factory=list)
    seed_capital: float = 0.0
    period_label: str = ""
    by_exchange: dict[str, dict] = field(default_factory=dict)
    # US-368~371 batch meta
    run_id: str = ""
    batch_id: str = ""
    metadata: dict = field(default_factory=dict)


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
        market_recorder: Any | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        symbols: list[str] | None = None,
        exchanges: list[str] | None = None,
        replay_speed: float = 0.0,  # 0 = as fast as possible
        strategy_ids: list[str] | None = None,
        seed_capital: float | None = None,
        run_id: str = "",
        batch_id: str = "",
        metadata: dict | None = None,
        triangular_scanner: Any | None = None,
        multi_signal_producer: Any | None = None,
    ) -> None:
        self._signal_generator = signal_generator
        self._strategy_manager = strategy_manager
        self._db_pool = db_pool
        self._market_recorder = market_recorder
        self._start_time = start_time
        self._end_time = end_time
        self._symbols = symbols or ["BTC/USDT"]
        self._exchanges = exchanges
        self._replay_speed = replay_speed
        self._strategy_ids = strategy_ids or []
        self._seed_capital = seed_capital or 0.0
        self._run_id = run_id
        self._batch_id = batch_id
        self._metadata = metadata or {}

        self._orderbook_cls = get_orderbook_class()
        self._books: dict[str, dict[str, Any]] = {}
        self._fee_model = FeeModel()
        self._result = BacktestResult()
        self._running = False

        # RealDataSignalProducer for triangular / stat_arb / funding_rate strategies
        self._real_signal_producer: Any | None = None
        if multi_signal_producer is not None and triangular_scanner is not None:
            from src.core.real_signal_producer import RealDataSignalProducer
            self._real_signal_producer = RealDataSignalProducer(
                multi_signal_producer=multi_signal_producer,
                triangular_scanner=triangular_scanner,
                backtest_mode=True,  # disables wall-clock cooldowns and Korean exchange skip
            )

        # Per-strategy PnL tracking
        self._strategy_pnl: dict[str, float] = {}
        self._strategy_trades: dict[str, int] = {}
        self._strategy_wins: dict[str, int] = {}

        self._pnl_returns: list[float] = []

        # Backtest latency injection: delay signal execution by N ms
        try:
            from src.core.config_loader import get_config as _gc_bt
            self._latency_ms: float = float(_gc_bt("backtest.latency_ms") or 0)
        except Exception:
            self._latency_ms = 0.0
        self._pending_signals: list[tuple[float, Signal]] = []

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
        self._result.strategy_ids = self._strategy_ids
        self._result.exchange_ids = self._exchanges or []
        self._result.seed_capital = self._seed_capital
        self._result.period_label = f"{self._start_time or ''} ~ {self._end_time or ''}"

        if not snapshots:
            logger.warning("backtest.no_snapshots — check TimescaleDB orderbook_snapshots table")
            self._result.error = "insufficient_data"
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

        # Drain remaining pending signals (latency injection)
        for _, sig in self._pending_signals:
            await self._route_and_execute(sig)
        self._pending_signals.clear()

        # Compute final metrics
        self._compute_metrics()
        self._result.duration_s = time.monotonic() - t0
        self._result.run_id = self._run_id
        self._result.batch_id = self._batch_id
        self._result.metadata = self._metadata
        self._running = False

        # Save to file if run_id set (US-368~371)
        if self._run_id:
            self._save_result()

        logger.info(
            "backtest.completed duration=%.1fs snapshots=%d trades=%d pnl=%.2f sharpe=%.2f mdd=%.4f",
            self._result.duration_s, self._result.snapshots_replayed,
            self._result.trades_executed, self._result.total_pnl,
            self._result.sharpe_ratio, self._result.max_drawdown_pct,
        )

        return self._result

    def _save_result(self) -> None:
        """Save backtest result to .omc/state/backtest-results-{run_id}.json (US-368~371)."""
        import dataclasses  # noqa: PLC0415
        import json  # noqa: PLC0415
        from pathlib import Path  # noqa: PLC0415

        state_dir = Path(__file__).resolve().parents[3] / ".omc" / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        out_path = state_dir / f"backtest-results-{self._run_id}.json"
        try:
            data = dataclasses.asdict(self._result)
            out_path.write_text(json.dumps(data, indent=2, default=str))
            logger.info("backtest.result_saved path=%s", out_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("backtest.result_save_failed: %s", exc)

    def stop(self) -> None:
        """Stop backtest early."""
        self._running = False

    async def _load_snapshots(self) -> list[dict]:
        """Load orderbook snapshots from TimescaleDB.

        Uses BACKTEST_MAX_ROWS env var (default 1,000,000) for LIMIT.
        Auto-detects start/end from MIN/MAX ts when not specified.
        Logs backtest.data_check with count and span after load.
        """
        if self._db_pool is None:
            logger.warning("backtest.no_db_pool — cannot load historical data")
            return []

        _max_rows = int(os.environ.get("BACKTEST_MAX_ROWS", "1000000"))

        try:
            # Auto-detect time range from DB when not specified
            start_time = self._start_time
            end_time = self._end_time
            if start_time is None or end_time is None:
                async with self._db_pool.pool.acquire() as conn:
                    row = await conn.fetchrow(
                        "SELECT MIN(ts) as min_ts, MAX(ts) as max_ts, COUNT(*) as cnt"
                        " FROM orderbook_snapshots"
                    )
                    if row and row["cnt"]:
                        if start_time is None:
                            start_time = str(row["min_ts"])
                        if end_time is None:
                            end_time = str(row["max_ts"])
                    else:
                        logger.info("backtest.data_check: count=0, span=0 min")
                        return []

            # asyncpg requires datetime objects, not strings, for timestamptz params
            from datetime import datetime, timezone  # noqa: PLC0415
            def _to_dt(val: str | None) -> datetime | None:
                if not val:
                    return None
                if isinstance(val, datetime):
                    return val
                # Accept "YYYY-MM-DD" or full ISO string
                if "T" not in val and " " not in val:
                    val = val + "T00:00:00"
                try:
                    dt = datetime.fromisoformat(val)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt
                except ValueError:
                    return None

            query = """
                SELECT exchange, symbol, bids_json, asks_json,
                       EXTRACT(EPOCH FROM ts) as timestamp
                FROM orderbook_snapshots
                WHERE 1=1
            """
            params: list = []
            idx = 1

            start_dt = _to_dt(start_time)
            end_dt = _to_dt(end_time)

            if start_dt:
                query += f" AND ts >= ${idx}"
                params.append(start_dt)
                idx += 1
            if end_dt:
                query += f" AND ts <= ${idx}"
                params.append(end_dt)
                idx += 1
            if self._symbols:
                query += f" AND symbol = ANY(${idx}::text[])"
                params.append(self._symbols)
                idx += 1
            if self._exchanges:
                query += f" AND exchange = ANY(${idx}::text[])"
                params.append(self._exchanges)
                idx += 1
            _source = getattr(self, "_source", None)
            if _source:
                query += f" AND source = ${idx}"
                params.append(_source)
                idx += 1

            query += f" ORDER BY ts ASC LIMIT {_max_rows}"

            async with self._db_pool.pool.acquire() as conn:
                rows = await conn.fetch(query, *params)

            import json as _json  # noqa: PLC0415

            def _parse_levels(raw) -> list:
                if isinstance(raw, list):
                    return raw
                if isinstance(raw, str):
                    try:
                        parsed = _json.loads(raw)
                        return parsed if isinstance(parsed, list) else []
                    except Exception:
                        return []
                return []

            snapshots = []
            for row in rows:
                snapshots.append({
                    "exchange": row["exchange"],
                    "symbol": row["symbol"],
                    "bids": _parse_levels(row["bids_json"]),
                    "asks": _parse_levels(row["asks_json"]),
                    "timestamp": float(row["timestamp"]),
                })

            # data_check log: count + time span
            if snapshots:
                span_min = (snapshots[-1]["timestamp"] - snapshots[0]["timestamp"]) / 60
                logger.info(
                    "backtest.data_check: count=%d, span=%.1f min",
                    len(snapshots), span_min,
                )
            else:
                logger.info("backtest.data_check: count=0, span=0 min")

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

        # Backtest latency injection: process matured pending signals
        if self._latency_ms > 0 and self._pending_signals:
            current_ts = snap.get("timestamp", 0)
            matured = [(ts, sig) for ts, sig in self._pending_signals if current_ts >= ts]
            self._pending_signals = [
                (ts, sig) for ts, sig in self._pending_signals if current_ts < ts
            ]
            for _, sig in matured:
                await self._route_and_execute(sig)

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

        # Feed SignalGenerator — cross_exchange path (requires books from >=2 exchanges)
        if self._signal_generator and len(self._books.get(symbol, {})) >= 2:
            try:
                signal = await self._signal_generator.on_orderbook_update(
                    book=core_book,
                    books=self._books.get(symbol, {}),
                )
                if signal is not None:
                    self._result.signals_generated += 1
                    if self._latency_ms > 0:
                        eligible_ts = snap.get("timestamp", 0) + self._latency_ms / 1000.0
                        self._pending_signals.append((eligible_ts, signal))
                    else:
                        await self._route_and_execute(signal)
            except Exception as exc:
                logger.debug("backtest.signal_error: %s", exc)

        # RealDataSignalProducer — triangular / stat_arb / spot_futures / futures_futures
        if self._real_signal_producer is not None:
            try:
                # Build all_books dict (symbol → exchange_id → book) for multi-strategy eval
                all_books = self._books
                # futures_books: exchanges with "_futures" suffix
                futures_books: dict[str, dict[str, Any]] = {}
                for sym, ex_map in self._books.items():
                    for ex_id, book in ex_map.items():
                        if "_futures" in ex_id:
                            futures_books.setdefault(sym, {})[ex_id] = book
                signals = await self._real_signal_producer.on_orderbook_update(
                    exchange_id=exchange_id,
                    symbol=symbol,
                    book=core_book,
                    all_books=all_books,
                    futures_books=futures_books,
                    simulated_ts=snap.get("timestamp"),  # use historical time for cooldowns
                )
                for sig in signals:
                    self._result.signals_generated += 1
                    if self._latency_ms > 0:
                        eligible_ts = snap.get("timestamp", 0) + self._latency_ms / 1000.0
                        self._pending_signals.append((eligible_ts, sig))
                    else:
                        await self._route_and_execute(sig)
            except Exception as exc:
                logger.debug("backtest.real_signal_error: %s", exc)

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
        """Simulate trade execution with fee deduction and execution_log recording."""
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

        # Record execution to TimescaleDB for WFA input (mode='backtest')
        if self._market_recorder is not None:
            try:
                buy_leg = next(
                    (l for l in trade_request.legs if l.side == OrderSide.BUY), None
                )
                sell_leg = next(
                    (l for l in trade_request.legs if l.side == OrderSide.SELL), None
                )
                if buy_leg and sell_leg:
                    self._market_recorder.record_execution(
                        strategy_id=sid,
                        buy_exchange=buy_leg.exchange_id,
                        sell_exchange=sell_leg.exchange_id,
                        symbol=buy_leg.symbol,
                        buy_price=buy_leg.price or Decimal("0"),
                        sell_price=sell_leg.price or Decimal("0"),
                        size=buy_leg.size,
                        net_pnl=Decimal(str(pnl_f)),
                        status="filled",
                        mode="backtest",
                    )
            except Exception as exc:
                logger.debug("backtest.record_execution_failed: %s", exc)

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

        # Profit factor — no losses → infinity (use 9999 as sentinel)
        wins_sum = sum(r for r in self._pnl_returns if r > 0)
        loss_sum = abs(sum(r for r in self._pnl_returns if r < 0))
        if loss_sum > 0:
            self._result.profit_factor = wins_sum / loss_sum
        elif wins_sum > 0:
            self._result.profit_factor = 9999.0  # no losses at all
        else:
            self._result.profit_factor = 0.0

        # Sharpe ratio — annualized per SSOT §4.5: sqrt(8760) for hourly intervals
        import numpy as np
        if len(self._pnl_returns) >= 2:
            returns = np.array(self._pnl_returns)
            mean_r = np.mean(returns)
            std_r = np.std(returns, ddof=1)
            if std_r > 0:
                self._result.sharpe_ratio = float(mean_r / std_r * np.sqrt(8760))

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
