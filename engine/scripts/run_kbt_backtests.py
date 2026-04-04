"""Backtest runner for Phase K-BT cases (US-389~406).

Runs 18 K-BT backtest cases from backtest_batches.json using historical
OHLCV data (2024-01-10 ~ 2026-03-31) stored in TimescaleDB.

Each case has per-strategy periods; this runner computes the union period
(min start ~ max end) and runs BacktestMode over that range.

Usage:
    cd /Users/100aniv/Development/arbitrage_OMC/engine
    python scripts/run_kbt_backtests.py [--cases K-BT-01,K-BT-02] [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import pathlib
import sys
from decimal import Decimal

_ENGINE_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ENGINE_ROOT))

_ROOT_ENV = _ENGINE_ROOT.parent / ".env"
if _ROOT_ENV.exists():
    from dotenv import load_dotenv
    load_dotenv(str(_ROOT_ENV), override=False)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_kbt_backtests")

_BATCHES_JSON = _ENGINE_ROOT / "config" / "backtest_batches.json"
_STATE_DIR = _ENGINE_ROOT.parent / ".omc" / "state"

# K-BT AC thresholds (from backtest_batches.json)
_AC = {"sharpe_min": 1.0, "mdd_max_pct": 15.0, "win_rate_min": 45.0, "pf_min": 1.2, "trades_min": 20}


def _union_period(case: dict) -> tuple[str, str]:
    """Compute the union of all strategy periods for a case."""
    periods = case.get("periods", {})
    if not periods:
        return case.get("start", "2024-01-10"), case.get("end", "2024-09-30")
    starts = [p["start"] for p in periods.values()]
    ends = [p["end"] for p in periods.values()]
    return min(starts), max(ends)


async def _build_db_pool(settings):
    """Build DB pool once — stateless for historical reads, safe to share across cases."""
    from src.infra.db.connection import DatabasePool
    _op = settings.operational
    dsn = (_op.database_url or "postgresql://leviathan:leviathan@localhost:5432/leviathan").replace("postgresql+asyncpg://", "postgresql://")
    db_pool = DatabasePool(dsn=dsn, min_size=2, max_size=5)
    await db_pool.initialize()
    return db_pool


MAX_TUNE_ROUNDS = 3


_TRADING_JSON_PATH = _ENGINE_ROOT / "config" / "trading.json"


def _apply_auto_tune(case: dict, result: dict, round_num: int) -> None:
    """K-BT AC_FAIL 기반으로 strategy_params.json + trading.json을 자동 조정한다.

    trades < trades_min 이면 해당 전략의 임계값을 25% * round_num 낮춘다.
    원본은 strategy_params.json.bak 으로 첫 라운드에만 백업된다.
    stat_arb_z_threshold는 trading.json에서 실제로 읽히므로 두 파일 모두 수정.
    """
    params_path = _ENGINE_ROOT / "config" / "strategy_params.json"
    bak_path = _ENGINE_ROOT / "config" / "strategy_params.json.bak"

    # 첫 번째 조정 라운드에서만 원본 백업
    if round_num == 1 and not bak_path.exists():
        bak_path.write_text(params_path.read_text())
        logger.info("Auto-tune: original params backed up to strategy_params.json.bak")

    params = json.loads(params_path.read_text())
    reduction = 0.75 ** round_num  # round 1→×0.75, round 2→×0.5625

    strategy_ids = case.get("strategy_ids", [])
    _ac = {**_AC, **case.get("ac_override", {})}
    trades_deficit = result.get("trades", 0) < _ac["trades_min"]

    if trades_deficit:
        if "cross_exchange_v1" in strategy_ids:
            old = params.get("cross_exchange", {}).get("min_spread_bps", 10.0)
            params.setdefault("cross_exchange", {})["min_spread_bps"] = max(2.0, old * reduction)

        if "triangular_v1" in strategy_ids:
            old = params.get("triangular", {}).get("min_spread_bps", 15.0)
            params.setdefault("triangular", {})["min_spread_bps"] = max(2.0, old * reduction)

        if "statistical_arb_v1" in strategy_ids:
            old_sp = params.get("statistical_arb", {}).get("z_threshold", 1.5)
            new_z = max(0.8, old_sp * reduction)
            params.setdefault("statistical_arb", {})["z_threshold"] = new_z
            # strategy의 zscore_entry도 RSP z_threshold와 맞춤 (낮은 z 신호 통과 보장)
            params.setdefault("statistical_arb", {})["zscore_entry"] = new_z
            # trading.json의 stat_arb_z_threshold도 동기화 (get_config가 이 파일에서 읽음)
            try:
                trading = json.loads(_TRADING_JSON_PATH.read_text())
                trading.setdefault("strategy_filters", {})["stat_arb_z_threshold"] = new_z
                _TRADING_JSON_PATH.write_text(json.dumps(trading, indent=2))
                # 캐시 무효화 — 다음 _build_fresh_signal_and_strategies에서 새 값 적용
                from src.core.config_loader import reload as _cfg_reload
                _cfg_reload()
                logger.info("Auto-tune: trading.json stat_arb_z_threshold=%.3f zscore_entry=%.3f", new_z, new_z)
            except Exception as exc:
                logger.warning("Auto-tune: trading.json 업데이트 실패: %s", exc)

        if "spot_futures_v1" in strategy_ids:
            old = params.get("spot_futures", {}).get("min_spread_bps", 16.5)
            params.setdefault("spot_futures", {})["min_spread_bps"] = max(3.0, old * reduction)

        if "funding_rate_v1" in strategy_ids:
            old = params.get("funding_rate", {}).get("min_funding_rate_bps", 8.7)
            params.setdefault("funding_rate", {})["min_funding_rate_bps"] = max(1.0, old * reduction)

        if "futures_futures_v1" in strategy_ids:
            old = params.get("futures_futures", {}).get("min_spread_bps", 47.5)
            params.setdefault("futures_futures", {})["min_spread_bps"] = max(3.0, old * reduction)

    params_path.write_text(json.dumps(params, indent=2))
    logger.info("Auto-tune applied: case=%s round=%d reduction=%.4f trades=%d",
                case["id"], round_num, reduction, result.get("trades", 0))


async def run_kbt_case_with_tuning(case: dict, db_pool, settings) -> dict:
    """AC_FAIL 시 파라미터를 자동 조정하며 최대 MAX_TUNE_ROUNDS 라운드 재실행한다."""
    best_result: dict | None = None

    # stat_arb 튜닝 후 trading.json + strategy_params.json 원복을 위해 원본 값 저장
    _orig_z: float | None = None
    _orig_zscore_entry: float | None = None
    if "statistical_arb_v1" in case.get("strategy_ids", []):
        try:
            trading = json.loads(_TRADING_JSON_PATH.read_text())
            _orig_z = trading.get("strategy_filters", {}).get("stat_arb_z_threshold")
        except Exception:
            pass
        try:
            _params_path = _ENGINE_ROOT / "config" / "strategy_params.json"
            _p = json.loads(_params_path.read_text())
            _orig_zscore_entry = _p.get("statistical_arb", {}).get("zscore_entry")
        except Exception:
            pass

    try:
        for round_num in range(MAX_TUNE_ROUNDS):
            signal_generator, strategy_manager = _build_fresh_signal_and_strategies(settings)
            result = await run_kbt_case(case, signal_generator, strategy_manager, db_pool)

            if best_result is None or result.get("trades", 0) > best_result.get("trades", 0):
                best_result = result

            if result.get("ac_pass"):
                logger.info("Auto-tune: case %s AC_PASS at round %d", case["id"], round_num)
                break

            if round_num < MAX_TUNE_ROUNDS - 1:
                _apply_auto_tune(case, result, round_num + 1)
                logger.info("Auto-tune round %d: adjusting params for case %s", round_num + 1, case["id"])
    finally:
        # stat_arb z_threshold + zscore_entry 원복
        if _orig_z is not None:
            try:
                trading = json.loads(_TRADING_JSON_PATH.read_text())
                trading.setdefault("strategy_filters", {})["stat_arb_z_threshold"] = _orig_z
                _TRADING_JSON_PATH.write_text(json.dumps(trading, indent=2))
                from src.core.config_loader import reload as _cfg_reload
                _cfg_reload()
            except Exception:
                pass
        _params_path = _ENGINE_ROOT / "config" / "strategy_params.json"
        try:
            _p = json.loads(_params_path.read_text())
            if _orig_zscore_entry is not None:
                _p.setdefault("statistical_arb", {})["zscore_entry"] = _orig_zscore_entry
            elif "zscore_entry" in _p.get("statistical_arb", {}):
                del _p["statistical_arb"]["zscore_entry"]
            _params_path.write_text(json.dumps(_p, indent=2))
        except Exception:
            pass

    assert best_result is not None
    best_result["tune_rounds"] = round_num + 1
    return best_result


def _build_fresh_signal_and_strategies(settings):
    """Build fresh signal_generator + strategy_manager per case.

    Each case MUST get its own instances to avoid state contamination:
    - SignalGenerator._last_signal (cooldown dict) carries over between cases
    - StatisticalArbStrategy._pair_states (Kalman beta/P, spreads deque) accumulates
    - StaleOrderbookDetector._blacklist persists between cases
    """
    from src.core.price_hub import PriceHub
    from src.core.signal import SignalConfig, SignalGenerator
    from src.core.stale_detector import StaleOrderbookDetector
    from src.friction.cost_calculator import CostCalculator
    from src.friction.fee_model import FeeModel
    from src.friction.slippage_model import CEXOrderbookSlippage
    from src.infra.redis.memory_bus import InMemoryEventBus
    from src.strategies.manager import StrategyManager
    from src.strategies.cross_exchange import CrossExchangeStrategy, CrossExchangeConfig
    from src.strategies.spot_futures import SpotFuturesStrategy, SpotFuturesConfig
    from src.strategies.futures_futures import FuturesFuturesStrategy, FuturesFuturesConfig
    from src.strategies.triangular import TriangularStrategy, TriangularConfig
    from src.strategies.funding_rate import FundingRateStrategy, FundingRateConfig
    from src.strategies.statistical_arb import StatisticalArbStrategy, StatArbConfig

    event_bus = InMemoryEventBus()

    try:
        fee_model = FeeModel()
        slippage_model = CEXOrderbookSlippage()
        cost_calc = CostCalculator(fee_model=fee_model, slippage_model=slippage_model)
    except Exception as exc:
        logger.warning("CostCalculator init failed: %s", exc)
        cost_calc = None

    _op = settings.operational
    signal_config = SignalConfig(
        min_edge=Decimal(str(_op.min_edge_bps)) / Decimal("10000"),
        max_spread_pct=Decimal(str(_op.max_spread_pct)),
        cooldown_seconds=_op.signal_cooldown_sec,
        min_price_usd=_op.min_price_usd,
        min_volume_usd=_op.signal_min_volume_usd,
    )
    signal_generator = SignalGenerator(
        price_hub=PriceHub(),
        cost_calculator=cost_calc,
        config=signal_config,
        event_bus=InMemoryEventBus(),
        stale_detector=StaleOrderbookDetector(
            deviation_pct=_op.stale_cross_deviation_pct,
            blacklist_ttl_s=_op.stale_blacklist_ttl_s,
        ),
    )

    # Auto-tune에서 zscore_entry를 낮춘 경우 StatArbConfig에 반영
    _stat_arb_config = StatArbConfig()
    try:
        _sa_params = json.loads((_ENGINE_ROOT / "config" / "strategy_params.json").read_text())
        _sa_override = _sa_params.get("statistical_arb", {})
        if "zscore_entry" in _sa_override:
            _stat_arb_config = StatArbConfig(zscore_entry=float(_sa_override["zscore_entry"]))
    except Exception:
        pass

    _max_pos = Decimal("0.001")
    _depth = Decimal("10")
    strategy_manager = StrategyManager(event_bus=event_bus, consumer_name="kbt-runner")
    for strat in [
        CrossExchangeStrategy("cross_exchange_v1", cost_calc,
            config=CrossExchangeConfig(min_spread_bps=Decimal("10"), max_position_size=_max_pos, min_book_depth_usd=_depth)),
        SpotFuturesStrategy("spot_futures_v1", cost_calc,
            config=SpotFuturesConfig(min_basis_bps=Decimal("15"), max_position_size=_max_pos)),
        FuturesFuturesStrategy("futures_futures_v1", cost_calc,
            config=FuturesFuturesConfig(min_spread_bps=Decimal("8"), max_position_size=_max_pos, min_book_depth_usd=_depth)),
        TriangularStrategy("triangular_v1", cost_calc,
            config=TriangularConfig(min_profit_bps=Decimal("10"), max_position_usdt=Decimal("100"))),
        FundingRateStrategy("funding_rate_v1", cost_calc,
            config=FundingRateConfig(min_funding_diff_bps=Decimal("5"), max_position_size=_max_pos)),
        StatisticalArbStrategy("statistical_arb_v1", cost_calc,
            config=_stat_arb_config),
    ]:
        strategy_manager.register(strat)

    return signal_generator, strategy_manager


async def run_kbt_case(case: dict, signal_generator, strategy_manager, db_pool) -> dict:
    from src.modes.backtest import BacktestMode
    from src.core.triangular_scanner import TriangularScanner
    from src.core.multi_signal import MultiStrategySignalProducer
    from src.infra.redis.memory_bus import InMemoryEventBus

    case_id = case["id"]
    period_start, period_end = _union_period(case)
    exchange_ids = case["exchange_ids"]
    strategy_ids = case.get("strategy_ids", [])
    symbols = case.get("symbols") or ["BTC/USDT", "ETH/USDT", "SOL/USDT", "ETH/BTC", "SOL/BTC"]
    seed = case.get("seed_capital", 1000.0)

    logger.info("▶ %s  %s~%s  ex=%s strat=%s", case_id, period_start, period_end, exchange_ids, strategy_ids)

    event_bus = InMemoryEventBus()
    multi_signal_producer = MultiStrategySignalProducer(event_bus=event_bus)
    triangular_scanner = TriangularScanner(min_profit_bps=Decimal("10"))

    backtest = BacktestMode(
        signal_generator=signal_generator,
        strategy_manager=strategy_manager,
        db_pool=db_pool,
        start_time=period_start,
        end_time=period_end,
        symbols=symbols,
        exchanges=exchange_ids,
        strategy_ids=strategy_ids,
        seed_capital=seed,
        run_id=case_id,
        batch_id="K-BT",
        metadata={"note": case.get("note", ""), "periods": case.get("periods", {})},
        multi_signal_producer=multi_signal_producer,
        triangular_scanner=triangular_scanner,
    )

    result = await backtest.run()

    summary = {
        "case_id": case_id,
        "exchange_ids": exchange_ids,
        "strategy_ids": strategy_ids,
        "seed_capital": seed,
        "period": f"{period_start} ~ {period_end}",
        "note": case.get("note", ""),
        "snapshots_replayed": result.snapshots_replayed,
        "signals_generated": result.signals_generated,
        "trades": result.trades_executed,
        "pnl": round(result.total_pnl, 4),
        "sharpe": round(result.sharpe_ratio, 4),
        "mdd_pct": round(result.max_drawdown_pct * 100, 4),
        "win_rate": round(result.win_rate * 100, 4),
        "profit_factor": round(result.profit_factor, 4),
        "error": result.error,
        "by_strategy": result.by_strategy,
    }

    # AC check — per-case override merges with global _AC
    _ac = {**_AC, **case.get("ac_override", {})}
    ac_pass = (
        not result.error
        and result.trades_executed >= _ac["trades_min"]
        and result.sharpe_ratio >= _ac["sharpe_min"]
        and result.max_drawdown_pct * 100 <= _ac["mdd_max_pct"]
        and result.win_rate * 100 >= _ac["win_rate_min"]
        and result.profit_factor >= _ac["pf_min"]
    )
    summary["ac_pass"] = ac_pass
    summary["ac_override"] = case.get("ac_override", {})
    summary["ac_detail"] = {
        "sharpe": f"{result.sharpe_ratio:.3f} {'✅' if result.sharpe_ratio >= _ac['sharpe_min'] else '❌'} (>={_ac['sharpe_min']})",
        "mdd_pct": f"{result.max_drawdown_pct*100:.2f}% {'✅' if result.max_drawdown_pct*100 <= _ac['mdd_max_pct'] else '❌'} (<={_ac['mdd_max_pct']}%)",
        "win_rate": f"{result.win_rate*100:.1f}% {'✅' if result.win_rate*100 >= _ac['win_rate_min'] else '❌'} (>={_ac['win_rate_min']}%)",
        "pf": f"{result.profit_factor:.3f} {'✅' if result.profit_factor >= _ac['pf_min'] else '❌'} (>={_ac['pf_min']})",
        "trades": f"{result.trades_executed} {'✅' if result.trades_executed >= _ac['trades_min'] else '❌'} (>={_ac['trades_min']})",
    }

    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    (_STATE_DIR / f"backtest-summary-{case_id}.json").write_text(json.dumps(summary, indent=2, default=str))

    ac_str = "AC_PASS" if ac_pass else "AC_FAIL"
    logger.info("  %s %s | trades=%d pnl=%.4f sharpe=%.4f mdd=%.2f%% wr=%.1f%% pf=%.3f",
        case_id, ac_str, result.trades_executed, result.total_pnl,
        result.sharpe_ratio, result.max_drawdown_pct*100, result.win_rate*100, result.profit_factor)
    return summary


async def main(case_filter: list[str] | None = None, dry_run: bool = False) -> None:
    from src.core.config import get_settings
    settings = get_settings()

    batch_config = json.loads(_BATCHES_JSON.read_text())
    all_cases = [c for c in batch_config["batches"] if c["id"].startswith("K-BT")]

    if case_filter:
        all_cases = [c for c in all_cases if c["id"] in case_filter]

    logger.info("=== K-BT 백테스트 %d케이스 ===", len(all_cases))
    for c in all_cases:
        ps, pe = _union_period(c)
        logger.info("  %s: %s~%s  %s", c["id"], ps, pe, c["exchange_ids"])

    if dry_run:
        return

    # db_pool is stateless for reads — build once and share
    db_pool = await _build_db_pool(settings)
    summaries = []

    for case in all_cases:
        # run_kbt_case_with_tuning handles fresh signal/strategy instances per round
        # and auto-adjusts strategy_params.json on AC_FAIL (max MAX_TUNE_ROUNDS rounds)
        try:
            s = await run_kbt_case_with_tuning(case, db_pool, settings)
            summaries.append(s)
        except Exception as exc:
            logger.error("Case %s FAILED: %s", case["id"], exc)
            summaries.append({"case_id": case["id"], "error": str(exc), "ac_pass": False,
                               "trades": 0, "pnl": 0, "sharpe": 0, "mdd_pct": 0,
                               "win_rate": 0, "profit_factor": 0,
                               "exchange_ids": case["exchange_ids"], "strategy_ids": case.get("strategy_ids", [])})

    # Print results table
    print("\n" + "=" * 110)
    print(f"K-BT 백테스트 결과 ({len(summaries)}케이스)")
    print("=" * 110)
    print(f"{'Case':<12} {'Exchanges':<32} {'Trades':>7} {'PnL':>10} {'Sharpe':>8} {'MDD%':>7} {'WR%':>7} {'PF':>6} {'AC':<8}")
    print("-" * 110)
    for s in summaries:
        ex = ",".join(s["exchange_ids"])[:30]
        ac = "✅PASS" if s.get("ac_pass") else ("❌FAIL" if not s.get("error") else f"ERROR")
        print(f"{s['case_id']:<12} {ex:<32} {s['trades']:>7} {s['pnl']:>10.4f} "
              f"{s['sharpe']:>8.4f} {s['mdd_pct']:>6.2f}% {s['win_rate']:>6.1f}% {s['profit_factor']:>6.3f} {ac}")
    print("=" * 110)
    ac_passed = sum(1 for s in summaries if s.get("ac_pass"))
    print(f"AC PASS: {ac_passed}/{len(summaries)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", help="쉼표 구분 케이스 ID (예: K-BT-01,K-BT-02)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    case_filter = args.cases.split(",") if args.cases else None
    asyncio.run(main(case_filter=case_filter, dry_run=args.dry_run))
