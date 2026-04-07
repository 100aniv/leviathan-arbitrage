"""Standalone backtest runner for Phase K batch cases (US-368~371).

Runs all 23 backtest cases defined in engine/config/backtest_batches.json
against the available TimescaleDB data (2026-03-29 ~ 2026-04-02).

Results are saved to .omc/state/backtest-results-{case_id}.json.

Usage:
    cd /Users/100aniv/Development/arbitrage_OMC/engine
    python scripts/run_k2b_backtests.py [--cases K-B-01,K-B-02] [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import os
import pathlib
import sys
from decimal import Decimal

# Ensure engine src/ is importable regardless of cwd
_ENGINE_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ENGINE_ROOT))

# Load root .env (engine/.env was deleted as part of K-0-ENV)
_ROOT_ENV = _ENGINE_ROOT.parent / ".env"
if _ROOT_ENV.exists():
    from dotenv import load_dotenv
    load_dotenv(str(_ROOT_ENV), override=False)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_k2b_backtests")

# Output directory for result JSON files
_STATE_DIR = _ENGINE_ROOT.parent / ".omc" / "state"


async def _build_multi_signal_producer():
    """Create MultiStrategySignalProducer + TriangularScanner for triangular/stat_arb strategies."""
    from src.core.multi_signal import MultiStrategySignalProducer
    from src.core.triangular_scanner import TriangularScanner
    from src.infra.redis.memory_bus import InMemoryEventBus
    from decimal import Decimal

    event_bus = InMemoryEventBus()
    producer = MultiStrategySignalProducer(event_bus=event_bus)
    scanner = TriangularScanner(min_profit_bps=Decimal("10"))
    logger.info("MultiStrategySignalProducer + TriangularScanner initialized")
    return producer, scanner


async def _build_signal_generator(settings):
    """Create a minimal SignalGenerator matching main.py _init_signal_pipeline."""
    from src.core.price_hub import PriceHub
    from src.core.signal import SignalConfig, SignalGenerator
    from src.core.stale_detector import StaleOrderbookDetector
    from src.friction.cost_calculator import CostCalculator
    from src.friction.fee_model import FeeModel
    from src.friction.slippage_model import CEXOrderbookSlippage
    from src.infra.redis.memory_bus import InMemoryEventBus

    price_hub = PriceHub()
    event_bus = InMemoryEventBus()

    try:
        fee_model = FeeModel()
        slippage_model = CEXOrderbookSlippage()
        cost_calculator = CostCalculator(fee_model=fee_model, slippage_model=slippage_model)
    except Exception as exc:
        logger.warning("CostCalculator init failed, using None: %s", exc)
        cost_calculator = None

    _op = settings.operational
    signal_config = SignalConfig(
        min_edge=Decimal(str(_op.min_edge_bps)) / Decimal("10000"),
        max_spread_pct=Decimal(str(_op.max_spread_pct)),
        cooldown_seconds=_op.signal_cooldown_sec,
        min_price_usd=_op.min_price_usd,
        min_volume_usd=_op.signal_min_volume_usd,
    )
    stale_detector = StaleOrderbookDetector(
        deviation_pct=_op.stale_cross_deviation_pct,
        blacklist_ttl_s=_op.stale_blacklist_ttl_s,
    )

    signal_generator = SignalGenerator(
        price_hub=price_hub,
        cost_calculator=cost_calculator,
        config=signal_config,
        event_bus=event_bus,
        stale_detector=stale_detector,
    )
    return signal_generator


async def _build_strategy_manager(settings):
    """Create a StrategyManager with all 6 strategies registered."""
    from src.strategies.manager import StrategyManager
    from src.strategies.cross_exchange import CrossExchangeStrategy, CrossExchangeConfig
    from src.strategies.spot_futures import SpotFuturesStrategy, SpotFuturesConfig
    from src.strategies.futures_futures import FuturesFuturesStrategy, FuturesFuturesConfig
    from src.strategies.triangular import TriangularStrategy, TriangularConfig
    from src.strategies.funding_rate import FundingRateStrategy, FundingRateConfig
    from src.strategies.statistical_arb import StatisticalArbStrategy
    from src.friction.cost_calculator import CostCalculator
    from src.friction.fee_model import FeeModel
    from src.friction.slippage_model import CEXOrderbookSlippage
    from src.infra.redis.memory_bus import InMemoryEventBus

    event_bus = InMemoryEventBus()

    try:
        cost_calc = CostCalculator(
            fee_model=FeeModel(),
            slippage_model=CEXOrderbookSlippage(),
        )
    except Exception:
        cost_calc = _StubCostCalculator()

    strategy_manager = StrategyManager(
        event_bus=event_bus,
        consumer_name="backtest-runner",
    )

    # Default configs — conservative sizing for backtest
    _max_pos = Decimal("0.001")  # ~0.001 BTC
    _book_depth = Decimal("10")

    strategies = [
        CrossExchangeStrategy(
            "cross_exchange_v1", cost_calc,
            config=CrossExchangeConfig(
                min_spread_bps=Decimal("10"),
                max_position_size=_max_pos,
                min_book_depth_usd=_book_depth,
            ),
        ),
        SpotFuturesStrategy(
            "spot_futures_v1", cost_calc,
            config=SpotFuturesConfig(
                min_basis_bps=Decimal("15"),
                max_position_size=_max_pos,
            ),
        ),
        FuturesFuturesStrategy(
            "futures_futures_v1", cost_calc,
            config=FuturesFuturesConfig(
                min_spread_bps=Decimal("8"),
                max_position_size=_max_pos,
                min_book_depth_usd=_book_depth,
            ),
        ),
        TriangularStrategy(
            "triangular_v1", cost_calc,
            config=TriangularConfig(
                min_profit_bps=Decimal("10"),
                max_position_usdt=Decimal("100"),
            ),
        ),
        FundingRateStrategy(
            "funding_rate_v1", cost_calc,
            config=FundingRateConfig(
                min_funding_diff_bps=Decimal("5"),
                max_position_size=_max_pos,
            ),
        ),
        StatisticalArbStrategy("statistical_arb_v1", cost_calc),
    ]

    for strategy in strategies:
        strategy_manager.register(strategy)

    logger.info("StrategyManager initialized with %d strategies", len(strategies))
    return strategy_manager


class _StubCostCalculator:
    """Minimal stub when CostCalculator cannot be initialized."""
    async def calculate(self, *args, **kwargs):
        from decimal import Decimal
        return Decimal("0")


async def _build_db_pool(settings):
    """Create and initialize a DatabasePool."""
    from src.infra.db.connection import DatabasePool

    dsn = settings.operational.database_url
    if not dsn:
        dsn = "postgresql://leviathan:leviathan@localhost:5432/leviathan"
    # asyncpg needs postgres:// DSN
    asyncpg_dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")

    db_pool = DatabasePool(dsn=asyncpg_dsn, min_size=2, max_size=5)
    await db_pool.initialize()
    logger.info("DatabasePool initialized: %s", asyncpg_dsn.split("@")[-1])
    return db_pool


async def run_batch_case(
    case: dict,
    signal_generator,
    strategy_manager,
    db_pool,
    period_start: str,
    period_end: str,
    multi_signal_producer=None,
    triangular_scanner=None,
) -> dict:
    """Run a single backtest case and return a summary dict."""
    from src.modes.backtest import BacktestMode

    case_id = case["id"]
    exchange_ids = case["exchange_ids"]
    strategy_ids = case.get("strategy_ids", [])
    seed_capital = case.get("seed_capital", 1000.0)
    note = case.get("note", "")

    logger.info(
        "Starting case %s — exchanges=%s strategies=%s seed=$%.0f",
        case_id, exchange_ids, strategy_ids, seed_capital,
    )

    # Use per-case symbols override if provided, else default cross-quote set.
    # Include cross-quote pairs (ETH/BTC, SOL/BTC, SOL/ETH) so TriangularScanner
    # can detect USDT→BTC→ETH→USDT cycles (DB has these for binance/upbit/okx)
    _default_symbols = [
        "BTC/USDT", "ETH/USDT", "XRP/USDT", "SOL/USDT", "BNB/USDT",
        "ETH/BTC", "SOL/BTC", "SOL/ETH",
    ]
    symbols = case.get("symbols") or _default_symbols

    backtest = BacktestMode(
        signal_generator=signal_generator,
        strategy_manager=strategy_manager,
        db_pool=db_pool,
        start_time=period_start,
        end_time=period_end,
        symbols=symbols,
        exchanges=exchange_ids,
        strategy_ids=strategy_ids,
        seed_capital=seed_capital,
        run_id=case_id,
        batch_id=case_id.rsplit("-", 1)[0],  # e.g. "K-B"
        metadata={"note": note, "proxy": bool(note)},
        multi_signal_producer=multi_signal_producer,
        triangular_scanner=triangular_scanner,
    )

    result = await backtest.run()

    # Build summary
    summary = {
        "case_id": case_id,
        "exchange_ids": exchange_ids,
        "strategy_ids": strategy_ids,
        "seed_capital": seed_capital,
        "period": f"{period_start} ~ {period_end}",
        "note": note,
        "snapshots_replayed": result.snapshots_replayed,
        "signals_generated": result.signals_generated,
        "trades": result.trades_executed,
        "pnl": round(result.total_pnl, 4),
        "sharpe": round(result.sharpe_ratio, 4),
        "mdd_pct": round(result.max_drawdown_pct, 4),
        "win_rate": round(result.win_rate, 4),
        "profit_factor": round(result.profit_factor, 4),
        "error": result.error,
        "by_strategy": result.by_strategy,
        "duration_s": round(result.duration_s, 2),
    }

    # Persist the full BacktestResult dataclass to the standard path
    # BacktestMode._save_result() already handles this when run_id is set.
    # We additionally write a lightweight summary.
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    summary_path = _STATE_DIR / f"backtest-summary-{case_id}.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str))

    status = "OK" if not result.error else f"ERROR:{result.error}"
    logger.info(
        "Case %s done — %s snapshots=%d trades=%d pnl=%.4f sharpe=%.4f",
        case_id, status, result.snapshots_replayed, result.trades_executed,
        result.total_pnl, result.sharpe_ratio,
    )
    return summary


def _print_table(summaries: list[dict]) -> None:
    """Print a summary table of all results."""
    header = f"{'Case':<10} {'Exchanges':<30} {'Strategy':<22} {'Snapshots':>10} {'Trades':>7} {'PnL':>10} {'Sharpe':>8} {'MDD%':>7} {'Status':<20}"
    print("\n" + "=" * 130)
    print("Phase K-2-B Backtest Results (5-day window: 2026-03-29 ~ 2026-04-02)")
    print("=" * 130)
    print(header)
    print("-" * 130)
    for s in summaries:
        exchanges = ",".join(s["exchange_ids"])[:28]
        strategies = ",".join(s["strategy_ids"])[:20]
        status = s["error"] if s["error"] else "PASS"
        note = f" [{s['note'][:15]}]" if s.get("note") else ""
        print(
            f"{s['case_id']:<10} {exchanges:<30} {strategies:<22} "
            f"{s['snapshots_replayed']:>10} {s['trades']:>7} "
            f"{s['pnl']:>10.4f} {s['sharpe']:>8.4f} {s['mdd_pct']*100:>6.2f}% "
            f"{status:<20}{note}"
        )
    print("=" * 130)

    # Summary statistics
    total = len(summaries)
    errors = sum(1 for s in summaries if s["error"])
    passed = total - errors
    has_trades = sum(1 for s in summaries if s["trades"] >= 1)
    print(f"\nTotal: {total} cases | Passed: {passed} | Errors: {errors} | Has trades (>=1): {has_trades}")

    # Per-batch breakdown
    for batch_label, start, end in [
        ("Batch1 (B-01~B-04 Binance)", "K-B-01", "K-B-04"),
        ("Batch2 (B-05~B-11 Bitget+KRW)", "K-B-05", "K-B-11"),
        ("Batch3 (B-12~B-16 Multi)", "K-B-12", "K-B-16"),
        ("Batch4 (B-17~B-23 Tier4 proxy)", "K-B-17", "K-B-23"),
    ]:
        batch_cases = [s for s in summaries if start <= s["case_id"] <= end]
        if batch_cases:
            b_err = sum(1 for s in batch_cases if s["error"])
            b_trades = sum(1 for s in batch_cases if s["trades"] >= 1)
            print(f"  {batch_label}: {len(batch_cases)} cases, {b_err} errors, {b_trades} with trades")


async def main(case_filter: list[str] | None = None, dry_run: bool = False) -> None:
    """Run all batch cases sequentially, reusing the same engine components."""
    # Load settings
    from src.core.config import get_settings
    settings = get_settings()

    # Load batch config
    batches_path = _ENGINE_ROOT / "config" / "backtest_batches.json"
    batch_config = json.loads(batches_path.read_text())
    period_start = batch_config["period"]["start"]
    period_end = batch_config["period"]["end"]
    all_cases = batch_config["batches"]

    # Filter cases if requested
    if case_filter:
        all_cases = [c for c in all_cases if c["id"] in case_filter]
        if not all_cases:
            logger.error("No cases matched filter: %s", case_filter)
            return

    logger.info(
        "Running %d backtest cases | period: %s ~ %s",
        len(all_cases), period_start, period_end,
    )

    if dry_run:
        logger.info("Dry-run mode — skipping actual execution")
        for case in all_cases:
            print(f"  {case['id']}: exchanges={case['exchange_ids']} strategies={case['strategy_ids']}")
        return

    # Build shared engine components once (reused across all cases)
    logger.info("Initializing engine components (signal_generator, strategy_manager, db_pool)...")
    try:
        signal_generator = await _build_signal_generator(settings)
    except Exception as exc:
        logger.error("Failed to initialize SignalGenerator: %s", exc)
        raise

    try:
        strategy_manager = await _build_strategy_manager(settings)
    except Exception as exc:
        logger.error("Failed to initialize StrategyManager: %s", exc)
        raise

    multi_signal_producer = None
    triangular_scanner = None
    try:
        multi_signal_producer, triangular_scanner = await _build_multi_signal_producer()
    except Exception as exc:
        logger.warning("MultiStrategySignalProducer init failed (triangular/stat_arb disabled): %s", exc)

    try:
        db_pool = await _build_db_pool(settings)
    except Exception as exc:
        logger.error(
            "Failed to connect to TimescaleDB: %s\n"
            "Ensure DB is running: docker compose up -d timescaledb", exc
        )
        raise

    # Verify DB has data in the target period
    try:
        async with db_pool.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*) as cnt, MIN(ts) as min_ts, MAX(ts) as max_ts "
                "FROM orderbook_snapshots "
                "WHERE ts >= $1::timestamptz AND ts <= $2::timestamptz",
                period_start, period_end,
            )
            if row:
                logger.info(
                    "DB data check: count=%d span=%s ~ %s",
                    row["cnt"], row["min_ts"], row["max_ts"],
                )
                if row["cnt"] == 0:
                    logger.warning(
                        "No data in period %s ~ %s. All cases will return insufficient_data.",
                        period_start, period_end,
                    )
    except Exception as exc:
        logger.warning("DB data pre-check failed (non-fatal): %s", exc)

    summaries: list[dict] = []
    try:
        for i, case in enumerate(all_cases, 1):
            logger.info("--- Case %d/%d: %s ---", i, len(all_cases), case["id"])
            try:
                summary = await run_batch_case(
                    case=case,
                    signal_generator=signal_generator,
                    strategy_manager=strategy_manager,
                    db_pool=db_pool,
                    period_start=period_start,
                    period_end=period_end,
                    multi_signal_producer=multi_signal_producer,
                    triangular_scanner=triangular_scanner,
                )
                summaries.append(summary)
            except Exception as exc:
                logger.error("Case %s failed with exception: %s", case["id"], exc, exc_info=True)
                summaries.append({
                    "case_id": case["id"],
                    "exchange_ids": case["exchange_ids"],
                    "strategy_ids": case.get("strategy_ids", []),
                    "seed_capital": case.get("seed_capital", 1000.0),
                    "period": f"{period_start} ~ {period_end}",
                    "note": case.get("note", ""),
                    "snapshots_replayed": 0,
                    "signals_generated": 0,
                    "trades": 0,
                    "pnl": 0.0,
                    "sharpe": 0.0,
                    "mdd_pct": 0.0,
                    "win_rate": 0.0,
                    "profit_factor": 0.0,
                    "error": str(exc),
                    "by_strategy": {},
                    "duration_s": 0.0,
                })

    finally:
        # Always close DB pool
        try:
            await db_pool.close()
        except Exception as exc:
            logger.warning("DB pool close error: %s", exc)

    # Print summary table
    _print_table(summaries)

    # Save combined results
    combined_path = _STATE_DIR / "backtest-results-all.json"
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    combined_path.write_text(json.dumps(summaries, indent=2, default=str))
    logger.info("Combined results saved: %s", combined_path)

    # Return exit code based on results
    errors = sum(1 for s in summaries if s["error"] and "insufficient_data" not in s["error"])
    if errors > 0:
        logger.warning("%d cases had unexpected errors (not insufficient_data)", errors)
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Phase K-2-B backtest cases")
    parser.add_argument(
        "--cases",
        type=str,
        default="",
        help="Comma-separated case IDs to run (default: all). E.g. K-B-01,K-B-02",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print cases without running them",
    )
    args = parser.parse_args()

    case_filter = [c.strip() for c in args.cases.split(",") if c.strip()] or None

    asyncio.run(main(case_filter=case_filter, dry_run=args.dry_run))
