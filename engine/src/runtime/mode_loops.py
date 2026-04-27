"""Mode dispatch loops — Phase 4-7 main.py 모듈화 (2026-04-26).

Extracted from main.py (7 methods, ~1050 LOC):
- backtest_mode_task          (snapshot replay)
- orderbook_feed_loop         (synthetic orderbook feed for paper)
- real_data_feed_loop         (real WS data feed → SignalGenerator)
- live_mode_loop              (LiveMode lifecycle)
- paper_mode_loop             (PaperMode lifecycle, Phase 2B PreTradeValidator+Journal)
- strategy_validation_loop    (env-gated, STRATEGY_VALIDATION=true)
- progressive_shadow_loop     (env-gated, SHADOW_PROGRESSIVE=true)

각 함수는 ``engine: "Engine"`` 첫 인자.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import time
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.main import Engine

from src.core.config import Settings, get_settings
from src.core.config_loader import get_bool_flag

logger = logging.getLogger(__name__)


async def backtest_mode_task(engine: "Engine") -> None:
    """Run BacktestMode replay + WFA for 6 strategies, save results, then shutdown."""
    import json
    import pathlib
    from src.modes.backtest import BacktestMode
    from src.analysis.walk_forward import WalkForwardAnalyzer

    settings = get_settings()
    backtest = BacktestMode(
        signal_generator=engine._signal_generator,
        strategy_manager=engine._strategy_manager,
        db_pool=engine._db_pool,
        market_recorder=engine._market_recorder,
        start_time=getattr(settings, "backtest_start", None),
        end_time=getattr(settings, "backtest_end", None),
        symbols=getattr(settings.operational, "symbols", None),
    )
    result = await backtest.run()
    engine._backtest_result = result
    engine.context.backtest_result = result

    # WFA 6-strategy loop (US-353)
    _STRATEGIES = [
        "cross_exchange", "spot_futures", "futures_futures",
        "triangular", "funding_rate", "statistical_arb",
    ]
    wfa_results: dict = {}
    if engine._db_pool is not None:
        try:
            wfa = WalkForwardAnalyzer(engine._db_pool.pool)
            for strategy_id in _STRATEGIES:
                logger.info("wfa.starting strategy=%s", strategy_id)
                try:
                    wfa_result = await wfa.analyze(strategy_id=strategy_id)
                    wfa_results[strategy_id] = {
                        "overall_sharpe": wfa_result.overall_sharpe,
                        "overall_mdd": wfa_result.overall_mdd,
                        "overall_trades": wfa_result.overall_trades,
                        "overall_pnl": wfa_result.overall_pnl,
                        "live_eligible": wfa_result.live_eligible,
                        "block_reason": wfa_result.block_reason,
                    }
                    logger.info(
                        "wfa.completed strategy=%s sharpe=%.2f trades=%d",
                        strategy_id, wfa_result.overall_sharpe, wfa_result.overall_trades,
                    )
                except Exception as exc:
                    logger.warning("wfa.strategy_failed strategy=%s: %s", strategy_id, exc)
                    wfa_results[strategy_id] = {"error": str(exc)}
        except Exception as exc:
            logger.error("wfa.failed: %s", exc)

    engine.context.wfa_results = wfa_results

    # Save results to .omc/state/backtest_results.json
    try:
        _project_root = pathlib.Path(__file__).parent.parent.parent
        state_dir = _project_root / ".omc" / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        output = {
            "backtest": {
                "snapshots_replayed": result.snapshots_replayed,
                "signals_generated": result.signals_generated,
                "trades_executed": result.trades_executed,
                "total_pnl": result.total_pnl,
                "sharpe_ratio": result.sharpe_ratio,
                "max_drawdown_pct": result.max_drawdown_pct,
                "win_rate": result.win_rate,
                "profit_factor": result.profit_factor,
                "duration_s": result.duration_s,
                "by_strategy": result.by_strategy,
                "error": result.error,
            },
            "wfa": wfa_results,
        }
        results_path = state_dir / "backtest_results.json"
        results_path.write_text(json.dumps(output, indent=2, default=str))
        logger.info("backtest.results_saved path=%s", results_path)
    except Exception as exc:
        logger.error("backtest.save_results_failed: %s", exc)

    # ML A/B test (US-354): baseline vs ML-enhanced signal comparison
    try:
        import numpy as np
        from src.analysis.ml_backtest import MLSignalBacktester
        ml_backtester = MLSignalBacktester(ml_scorer=None)
        ml_ab_result = ml_backtester.ab_test(
            signals=[],
            prices=np.array([1.0]),
            features=None,
        )
        engine.context.backtest_result = getattr(engine.context, "backtest_result", None)
        # Store on context for API access
        if hasattr(engine.context, "__dict__"):
            engine.context.__dict__["ml_ab_result"] = ml_ab_result
        logger.info(
            "backtest.ml_ab_test_done comparison_valid=%s",
            ml_ab_result.comparison_valid,
        )
    except Exception as exc:
        logger.warning("backtest.ml_ab_test_failed: %s", exc)

    # Signal engine shutdown after backtest completes
    engine._shutdown_event.set()


async def orderbook_feed_loop(engine: "Engine") -> None:
    """Subscribe to orderbook feeds from all exchanges and feed SignalGenerator."""
    from src.core.rust_bridge import get_orderbook_class

    CoreOrderBook = get_orderbook_class()

    symbols = engine._settings.trading.symbols if engine._settings else ["BTC/USDT"]

    all_books: dict[str, CoreOrderBook] = {}

    def make_callback(exchange_id: str, symbol: str):
        """Create callback that converts Pydantic OrderBook → core OrderBook."""
        def on_orderbook(pydantic_book) -> None:
            # Convert Pydantic OrderBook to core OrderBook
            core_book = CoreOrderBook(symbol=symbol, exchange=exchange_id)
            bids = [(str(level.price), str(level.amount)) for level in pydantic_book.bids]
            asks = [(str(level.price), str(level.amount)) for level in pydantic_book.asks]
            core_book.apply_snapshot(bids, asks)

            all_books[exchange_id] = core_book

            # Feed to SignalGenerator (fire and forget)
            if engine._signal_generator and len(all_books) >= 2:
                asyncio.create_task(
                    engine._signal_generator.on_orderbook_update(
                        book=core_book,
                        books=all_books,
                    )
                )
        return on_orderbook

    for exchange_id, adapter in engine._exchanges.items():
        for symbol in symbols:
            callback = make_callback(exchange_id, symbol)
            await adapter.subscribe_orderbook(symbol, callback)
            logger.info("Subscribed to orderbook: %s %s", exchange_id, symbol)

    # Keep the task alive until cancelled
    try:
        while engine.state.running:
            await asyncio.sleep(1.0)
    except asyncio.CancelledError:
        pass


async def real_data_feed_loop(engine: "Engine") -> None:
    """Start real public WebSocket collectors and feed SignalGenerator.

    Collectors deliver raw orderbook data (no API keys needed).
    Data flows: WS → CoreOrderBook → SignalGenerator → PaperExecutor (observation).
    Optionally records to MarketRecorder (TimescaleDB).

    When USE_RUST_ORDERBOOK=true, uses Rust BTreeMap orderbook (<5μs updates).
    """
    from src.collectors.manager import CollectorManager
    from src.core.rust_bridge import get_orderbook_class

    CoreOrderBook = get_orderbook_class()

    all_books: dict[str, CoreOrderBook] = {}

    async def on_orderbook(exchange_id: str, symbol: str, bids: list, asks: list) -> None:
        """Callback from collectors: convert raw data → CoreOrderBook → SignalGenerator."""
        core_book = CoreOrderBook(symbol=symbol, exchange=exchange_id)
        # bids/asks are [[price_str, qty_str], ...]
        core_book.apply_snapshot(
            [(b[0], b[1]) for b in bids],
            [(a[0], a[1]) for a in asks],
        )
        all_books[exchange_id] = core_book

        # Record to TimescaleDB if available
        if engine._market_recorder:
            best_bid = core_book.best_bid()
            best_ask = core_book.best_ask()
            if best_bid and best_ask:
                engine._market_recorder.record_orderbook(
                    exchange=exchange_id,
                    symbol=symbol,
                    bids=bids[:20],
                    asks=asks[:20],
                    best_bid=best_bid,
                    best_ask=best_ask,
                )

        # US-170: TriangularScanner — detect triangular cycles
        if engine._triangular_scanner is not None:
            try:
                cycles = engine._triangular_scanner.on_orderbook_update(
                    exchange_id=exchange_id, symbol=symbol, book=core_book
                )
                if cycles and engine._multi_signal_producer is not None:
                    for cycle in cycles:
                        asyncio.create_task(
                            engine._multi_signal_producer.produce_triangular_signal(cycle)
                        )
            except Exception as exc:
                logger.debug("TriangularScanner error: %s", exc)

        # Feed to SignalGenerator when we have data from 2+ exchanges
        if engine._signal_generator and len(all_books) >= 2:
            try:
                sig = await engine._signal_generator.on_orderbook_update(
                    book=core_book,
                    books=all_books,
                )
                if sig and engine._telegram and engine._telegram._enabled:
                    await engine._telegram.send_signal_found(sig)
            except Exception as exc:
                logger.warning("Signal generation error: %s", exc)

        # Update Prometheus metrics
        try:
            from src.infra.metrics import SIGNALS_TOTAL, EXCHANGE_HEALTH_SCORE
            EXCHANGE_HEALTH_SCORE.labels(exchange=exchange_id).set(1.0)
        except Exception:
            pass

    symbols = engine._settings.trading.symbols if engine._settings else ["BTC/USDT"]
    exchanges = engine._active_exchanges or _get_fallback_exchanges()

    engine._collector_manager = CollectorManager(
        symbols=symbols,
        exchanges=exchanges,
        on_orderbook=on_orderbook,
    )
    await engine._collector_manager.start()
    logger.info("Real data collectors started: %s for %s", exchanges, symbols)

    # Send Telegram notification
    if engine._telegram and engine._telegram._enabled:
        await engine._telegram.send_alert_kr("data_collector_start", {
            "exchanges": ", ".join(exchanges),
            "symbols": ", ".join(symbols),
        })

    # Keep alive until cancelled
    try:
        while engine.state.running:
            await asyncio.sleep(5.0)
            # Log collector stats periodically
            if engine._collector_manager:
                stats = engine._collector_manager.stats
                connected = engine._collector_manager.connected_count
                logger.debug("Collector stats: connected=%d/%d", connected, len(stats))
    except asyncio.CancelledError:
        pass
    finally:
        if engine._collector_manager:
            await engine._collector_manager.stop()


async def live_mode_loop(engine: "Engine") -> None:
    """Phase H: Live mode — direct in-process routing via LiveMode class.

    Uses the same proven architecture as ShadowMode:
    - Direct StrategyManager.route_signal() (no Redis dependency)
    - DI executor: PaperExecutor for validation, AtomicExecutor for live
    - All signal producers wired (cross_exchange, multi-strategy, real_signal)
    - LiveGate with safe Shadow fallback (not silent return)
    """
    from src.collectors.funding_rate_collector import FundingRateCollector
    from src.core.multi_signal import MultiStrategySignalProducer
    from src.modes.live import LiveMode, LiveGateFailed

    from src.core.config import load_engine_config

    _engine_cfg = load_engine_config()
    _mode_cfg = _engine_cfg.get(engine._engine_mode.value, {}) if hasattr(engine, '_engine_mode') else {}

    symbols = engine._settings.trading.symbols if engine._settings else ["BTC/USDT"]
    # Phase H-2: use mode-specific exchanges from config/engine.json
    exchanges = _mode_cfg.get("exchanges") or engine._active_exchanges or _get_fallback_exchanges()

    # Create MultiStrategySignalProducer
    engine._multi_signal_producer = MultiStrategySignalProducer(
        event_bus=engine._event_bus,
        latency_tracker=getattr(engine, "_latency_tracker", None),
    )

    # Dynamic symbol + exchange discovery — reads engine.json at runtime.
    funding_rate_collector = None
    try:
        _fr_symbols = await FundingRateCollector.fetch_paired_symbols(
            http_client=getattr(engine, "_http_client", None),
        )
        funding_rate_collector = FundingRateCollector(
            symbols=_fr_symbols,
            exchanges=FundingRateCollector.get_poll_exchanges(),
            http_client=getattr(engine, "_http_client", None),
        )
    except Exception as exc:
        logger.warning("FundingRateCollector init failed (non-fatal): %s", exc)

    # Phase H-2: execution mode from EngineMode (config/engine.json)
    execution_mode = engine._engine_mode.value if hasattr(engine, '_engine_mode') else "paper"

    # BUG-228c: runtime per-exchange min_notional registry (replaces hardcoded
    # execution.exchange_min_notional). Registry holds reference to engine._exchanges
    # so late-registered adapters are visible automatically.
    from src.infra.exchange.min_notional_registry import MinNotionalRegistry
    engine._min_notional_registry = MinNotionalRegistry(engine._exchanges)

    engine._live_mode = LiveMode(
        signal_generator=engine._signal_generator,
        executor=engine._executor,
        strategy_manager=engine._strategy_manager,
        symbols=symbols,
        exchanges=exchanges,
        multi_signal_producer=engine._multi_signal_producer,
        funding_rate_collector=funding_rate_collector,
        market_recorder=engine._market_recorder,
        telegram=engine._telegram,
        live_gate=engine._live_gate,
        risk_guardian=engine._risk_guardian,
        kill_switch=getattr(engine, "_kill_switch", None),
        circuit_breaker=engine._circuit_breaker,
        regime_detector=engine._regime_detector,
        event_bus=engine._event_bus,
        db_pool=engine._db_pool,
        data_quality_manager=engine._data_quality_manager,
        flash_guard=getattr(engine, "_flash_guard", None),
        portfolio_risk=getattr(engine, "_portfolio_risk", None),
        execution_mode=execution_mode,
        tca_analyzer=getattr(engine, "_tca_analyzer", None),
        slippage_feedback_collector=getattr(engine, "_slippage_fb_collector", None),
        position_manager=engine._position_manager,
        cost_feedback=getattr(engine, "_cost_feedback", None),  # WS-B: shared TCAAdaptiveFeedback
        min_notional_registry=engine._min_notional_registry,  # BUG-228c: runtime min_notional
    )
    from src.reconciliation import ExchangePnLSnapshot, PnLLedger, PnLReconciler  # Path-B Day-1
    engine._pnl_snapshot = ExchangePnLSnapshot(adapters=list(engine._exchanges.values()), db_pool=engine._db_pool)
    engine._pnl_ledger = PnLLedger(snapshot=engine._pnl_snapshot, engine_pnl_getter=lambda: getattr(engine._live_mode._stats, "total_pnl", 0.0))
    engine._pnl_reconciler = PnLReconciler(snapshot=engine._pnl_snapshot, engine_pnl_getter=lambda: getattr(engine._live_mode._stats, "total_pnl", 0.0), ledger=engine._pnl_ledger, telegram=engine._telegram)
    engine._live_mode._pnl_ledger = engine._pnl_ledger  # inject post-init (frozen live.py contract)

    try:
        await engine._live_mode.start()
        engine.context.execution_mode = execution_mode

        # Keep alive until cancelled
        while engine.state.running:
            await asyncio.sleep(5.0)
    except LiveGateFailed as _lgf:
        logger.critical(
            "live_mode_aborted — pre-existing positions detected. "
            "Run close_positions.py --execute first, then restart engine. err=%s", _lgf
        )
        if hasattr(engine, "state"):
            engine.state.running = False
        return  # stop cleanly — do NOT fall back to paper with live tasks running
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.error("_live_mode_loop FATAL error: %s", exc, exc_info=True)
    finally:
        if hasattr(engine, "_live_mode") and engine._live_mode is not None:
            await engine._live_mode.stop()


async def paper_mode_loop(engine: "Engine") -> None:
    """Start Paper Mode: real data + simulated execution + full metrics.

    Phase 8 (2026-04-27 사장님 명령): "라이브 기준으로 배관 맞춤"
    - 단일 배관 통합 — LiveMode + execution_mode="paper" 사용 (ShadowMode 대신)
    - 사장님 메모리: feedback_pipeline_must_be_unified.md
    - paper에서도 live와 동일 LiveMode 클래스 + dispatcher + 14 listeners + Day 6-15 모듈 자동 wired
    - 변경 전: ShadowMode 별도 클래스 (Day 6/12만 활성, Day 7/8/11/13/14/15 미활성)
    - 변경 후: LiveMode + execution_mode="paper" (모든 Day 모듈 활성)

    feature flag 보호: PAPER_USE_LIVEMODE (default false → 안정성 검증 후 true)
    """
    try:
        from src.collectors.funding_rate_collector import FundingRateCollector
        # Phase 8 단일 배관: ShadowMode 대신 LiveMode 사용 (default 활성 후 검증)
        _use_livemode = get_bool_flag("PAPER_USE_LIVEMODE")
        if not _use_livemode:
            from src.modes.shadow import ShadowMode  # legacy fallback
        from src.modes.live import LiveMode  # Phase 8 단일 배관

        symbols = engine._settings.trading.symbols if engine._settings else ["BTC/USDT"]
        exchanges = engine._active_exchanges or _get_fallback_exchanges()

        # Create MultiStrategySignalProducer for 6 additional strategies
        from src.core.multi_signal import MultiStrategySignalProducer
        multi_signal_producer = MultiStrategySignalProducer(
            event_bus=engine._event_bus,
            latency_tracker=getattr(engine, "_latency_tracker", None),
        )

        # Dynamic symbol + exchange discovery
        _fr_symbols = await FundingRateCollector.fetch_paired_symbols(
            http_client=getattr(engine, "_http_client", None),
        )
        funding_rate_collector = FundingRateCollector(
            symbols=_fr_symbols,
            exchanges=FundingRateCollector.get_poll_exchanges(),
            http_client=getattr(engine, "_http_client", None),
        )

        # US-171: create KillSwitch for KRW staleness soft-block
        from src.risk.kill_switch import KillSwitch as _KillSwitch
        _paper_kill_switch = _KillSwitch()

        # US-299: optional per-strategy filter
        _strategy_filter_raw = get_settings().operational.paper_strategy_filter.strip()
        _strategy_filter = (
            [s.strip() for s in _strategy_filter_raw.split(",") if s.strip()]
            if _strategy_filter_raw else None
        )

        if _use_livemode:
            # Phase 8 단일 배관: LiveMode + execution_mode="paper" 사용
            # paper에서도 live와 동일 dispatcher + Day 6-15 모듈 자동 wired
            from src.infra.exchange.min_notional_registry import MinNotionalRegistry
            engine._min_notional_registry = MinNotionalRegistry(engine._exchanges)
            
            # US-348: LiveMode internally wires PreTradeValidator, PaperExecutor, 
            # and BookWalkSlippage when execution_mode="paper".
            engine._paper_mode = LiveMode(
                signal_generator=engine._signal_generator,
                executor=engine._executor,  # AtomicExecutor + PaperExchangeAdapter
                strategy_manager=engine._strategy_manager,
                symbols=symbols,
                exchanges=exchanges,
                multi_signal_producer=multi_signal_producer,
                funding_rate_collector=funding_rate_collector,
                market_recorder=engine._market_recorder,
                telegram=engine._telegram,
                live_gate=None,  # Codex BLOCKING fix: paper에서 live_gate skip (approval gate 무용)
                risk_guardian=None,  # Codex BLOCKING #1: paper에서 risk_guardian=None (legacy 룰 보존, 100% reject 회귀 방지)
                kill_switch=_paper_kill_switch,
                circuit_breaker=engine._circuit_breaker,
                regime_detector=engine._regime_detector,
                event_bus=engine._event_bus,
                db_pool=engine._db_pool,
                data_quality_manager=engine._data_quality_manager,
                flash_guard=getattr(engine, "_flash_guard", None),
                portfolio_risk=getattr(engine, "_portfolio_risk", None),
                execution_mode="paper",  # PaperExecutor + BookWalkSlippage 자동 wiring
                tca_analyzer=getattr(engine, "_tca_analyzer", None),
                slippage_feedback_collector=getattr(engine, "_slippage_fb_collector", None),
                position_manager=engine._position_manager,
                cost_feedback=getattr(engine, "_cost_feedback", None),
                min_notional_registry=engine._min_notional_registry,
                strategy_filter=_strategy_filter,
            )
            # Codex BLOCKING #2 fix: MarketRecorderListener (factory.py:104) 가
            # `engine._live_mode._execution_mode` 참조 — paper unified 경로에서도
            # alias 설정해야 mode='paper' 정확 기록 (DB 검증 증거 오염 방지).
            engine._live_mode = engine._paper_mode
            logger.info(
                "paper_mode.livemode_unified pipeline enabled (Phase 8 — risk_guardian=None, "
                "live_gate=None, _live_mode=_paper_mode alias for recorder)"
            )
        else:
            # Legacy: ShadowMode (Phase 8 활성 검증 후 폐기 예정)
            # 2026-04-26 Phase 2B: paper에서 Day 6 (ExecutionJournal) + Day 12 (PreTradeValidator) 활성화
            from src.core.config_loader import get_config as _shadow_get_cfg
            _shadow_pre_trade_validator = None
            _shadow_execution_journal = None
            
            if get_bool_flag("EXECUTION_JOURNAL_ENABLED"):
                try:
                    import pathlib as _pl
                    from src.execution.journal import ExecutionJournal
                    _journal_path = _pl.Path(__file__).parent.parent / "logs" / "paper_execution_journal.db"
                    _journal_path.parent.mkdir(parents=True, exist_ok=True)
                    _shadow_execution_journal = ExecutionJournal(db_path=_journal_path)
                    await _shadow_execution_journal.start()
                    logger.info("paper_mode.shadow_legacy.execution_journal_enabled path=%s", _journal_path)
                except Exception as exc:
                    logger.warning("paper_mode.shadow_legacy.execution_journal_init_failed: %r", exc, exc_info=True)
            
            if get_bool_flag("EXECUTION_PRETRADE_VALIDATOR_ENABLED"):
                try:
                    from src.execution.pre_trade_validator import PreTradeValidator
                    from src.execution.dedup import DeduplicationGate
                    from src.risk.kill_switch import halt_local as _shadow_halt_local
                    _shadow_dedup = DeduplicationGate(window_s=10.0)
                    class _PaperMinNotionalStub:
                        async def get(engine, exchange_id, symbol): return 0.0
                    _shadow_min_notional = _PaperMinNotionalStub()
                    _total_capital = float(_shadow_get_cfg("portfolio.total_capital_usdt", default=140.0))
                    _max_session_loss = float(_shadow_get_cfg("risk.max_session_loss_usd", default=10.0))
                    _shadow_pre_trade_validator = PreTradeValidator(
                        strategy_filter=frozenset(_strategy_filter) if _strategy_filter else None,
                        strategy_disable_until={},
                        kill_switch=_paper_kill_switch,
                        circuit_breaker=None,
                        rate_buckets=None,
                        flash_guard=engine._flash_guard,
                        risk_guardian=None,
                        dedup_gate=_shadow_dedup,
                        symbol_last_trade={},
                        symbol_cooldown_s=float(_shadow_get_cfg("execution.symbol_cooldown_s", default=30.0)),
                        cached_margin={},
                        min_notional_registry=_shadow_min_notional,
                        get_config=lambda key, default=None: _shadow_get_cfg(key, default=default),
                        total_capital_usd=_total_capital,
                        max_session_loss_usd=_max_session_loss,
                        session_loss_supplier=lambda: -float(getattr(getattr(engine, "_paper_mode", None), "_stats", None) and engine._paper_mode._stats.total_pnl or 0.0),
                        build_collision_key=lambda req: f"{req.strategy_id}_{req.legs[0].symbol if req.legs else 'none'}",
                        is_reduceonly_request=lambda req: False,
                        halt_local=_shadow_halt_local,
                        telegram=engine._telegram,
                    )
                    logger.info("paper_mode.shadow_legacy.pre_trade_validator_enabled")
                except Exception as exc:
                    logger.warning("paper_mode.shadow_legacy.pre_trade_validator_init_failed: %r", exc, exc_info=True)

            engine._paper_mode = ShadowMode(
                signal_generator=engine._signal_generator,
                paper_executor=None,
                collector_manager=None,
                market_recorder=engine._market_recorder,
                telegram=engine._telegram,
                symbols=symbols,
                exchanges=exchanges,
                multi_signal_producer=multi_signal_producer,
                funding_rate_collector=funding_rate_collector,
                strategy_manager=engine._strategy_manager,
                kill_switch=_paper_kill_switch,
                regime_detector=engine._regime_detector,
                adaptive_threshold=engine._adaptive_threshold,
                db_pool=engine._db_pool,
                data_quality_manager=engine._data_quality_manager,
                strategy_filter=_strategy_filter,
                portfolio_risk=engine._portfolio_risk,
                pre_trade_validator=_shadow_pre_trade_validator,
                execution_journal=_shadow_execution_journal,
            )
            logger.info("paper_mode.shadowmode_legacy enabled (PAPER_USE_LIVEMODE=false)")

        # Common: Wire FlashGuard if available
        if engine._flash_guard is not None:
            engine._paper_mode._flash_guard = engine._flash_guard

        # Set all registered strategies to shadow mode and start them
        if engine._strategy_manager is not None:
            for sid in engine._strategy_manager.list_strategies():
                s = engine._strategy_manager.get_strategy(sid)
                if s:
                    s.paper_mode = True
            for sid in engine._strategy_manager.list_strategies():
                try:
                    await engine._strategy_manager.start_strategy(sid)
                except Exception as exc:
                    logger.warning("Shadow strategy %s start failed: %s", sid, exc)

        engine.context.paper_mode = engine._paper_mode  # set before start so API can see it
        try:
            await engine._paper_mode.start()
        except Exception as exc:
            logger.error("paper_mode.start_failed error=%s", exc, exc_info=True)
            raise
        engine.context.shadow_active = True
        engine.context.execution_mode = "paper"
        logger.info("Paper Mode started: %s for %s", exchanges, symbols)

        # ER5-04: Warm-start restore — load previous shadow stats from DB
        if engine._db_pool is not None:
            try:
                async with engine._db_pool.pool.acquire() as conn:
                    rows = await conn.fetch(
                        "SELECT key, value FROM engine_state"
                        " WHERE key IN ('paper_total_pnl', 'paper_trades_executed',"
                        " 'shadow_total_pnl', 'shadow_trades_executed')"
                    )
                    for row in rows:
                        if row["key"] in ("paper_total_pnl", "shadow_total_pnl"):
                            engine._paper_mode._stats.total_pnl = float(row["value"])
                            logger.info("paper_total_pnl_restored value=%s", row["value"])
                        elif row["key"] in ("paper_trades_executed", "shadow_trades_executed"):
                            engine._paper_mode._stats.trades_executed = int(row["value"])
                            logger.info("paper_trades_executed_restored value=%s", row["value"])
            except Exception as exc:
                logger.warning("shadow_stats_warm_start_failed error=%s", exc)

        # Start LiveGate auto-evaluation if DB is available
        if engine._db_pool is not None:
            try:
                from src.modes.live_gate import LiveGate
                from src.risk.kill_switch import KillSwitch

                # Wire with ALL exchange adapters for Tier 2/3 to function
                kill_switch = KillSwitch(
                    redis_client=getattr(engine, '_redis_client', None),
                    exchanges=list(engine._exchanges.values()),
                )
                engine._kill_switch = kill_switch  # store for shutdown and compliance
                # US-286: DQM health scores as exchange_health_fn
                _ehf = engine._data_quality_manager.get_all_health_scores if engine._data_quality_manager else None
                engine._live_gate = LiveGate(
                    pool=engine._db_pool.pool,
                    telegram=engine._telegram,
                    kill_switch=kill_switch,
                    circuit_breaker=engine._circuit_breaker,
                    exchange_health_fn=_ehf,
                    settings=engine._settings,
                )
                await engine._live_gate.start_auto_evaluation()
                logger.info("LiveGate auto-evaluation started (24h cycle)")
            except Exception as exc:
                logger.warning("LiveGate init failed (non-fatal): %s", exc)

        # Send Telegram notification
        if engine._telegram and engine._telegram._enabled:
            await engine._telegram.send_alert_kr("shadow_mode_start", {
                "exchanges": ", ".join(exchanges),
                "symbols": ", ".join(symbols),
                "live_gate": "활성" if engine._live_gate else "비활성",
            })
    except Exception as exc:
        logger.error("paper_mode_loop.failed error=%s", exc, exc_info=True)
        return

    # Keep alive until cancelled
    # NOTE: Cleanup is handled exclusively by Engine.stop() to avoid
    # double-cleanup race conditions. Do NOT add cleanup here.
    try:
        while engine.state.running:
            await asyncio.sleep(5.0)
    except asyncio.CancelledError:
        pass


async def strategy_validation_loop(engine: "Engine") -> None:
    """Per-strategy isolated Shadow validation (US-067).

    Creates ShadowMode and StrategyValidationOrchestrator, validates each strategy
    in isolation, then writes config/strategy_activation.json.
    Enabled when STRATEGY_VALIDATION=true (overrides SHADOW_PROGRESSIVE).
    """
    from src.collectors.funding_rate_collector import FundingRateCollector
    from src.core.multi_signal import MultiStrategySignalProducer
    from src.modes.shadow import ShadowMode
    from src.modes.strategy_validation import StrategyValidationOrchestrator

    symbols = engine._settings.trading.symbols if engine._settings else ["BTC/USDT"]
    exchanges = engine._active_exchanges or _get_fallback_exchanges()

    multi_signal_producer = MultiStrategySignalProducer(
        event_bus=engine._event_bus,
        latency_tracker=getattr(engine, "_latency_tracker", None),
    )

    _fr_symbols_sv = await FundingRateCollector.fetch_paired_symbols(
        http_client=getattr(engine, "_http_client", None),
    )
    funding_rate_collector = FundingRateCollector(
        symbols=_fr_symbols_sv,
        exchanges=FundingRateCollector.get_poll_exchanges(),
        http_client=getattr(engine, "_http_client", None),
    )

    # US-299: optional per-strategy filter from env var (comma-separated signal IDs)
    _sf_raw = get_settings().operational.paper_strategy_filter.strip()
    _sf = [s.strip() for s in _sf_raw.split(",") if s.strip()] if _sf_raw else None

    shadow = ShadowMode(
        signal_generator=engine._signal_generator,
        paper_executor=None,
        collector_manager=None,
        market_recorder=engine._market_recorder,
        telegram=engine._telegram,
        symbols=symbols,
        exchanges=exchanges,
        multi_signal_producer=multi_signal_producer,
        funding_rate_collector=funding_rate_collector,
        strategy_manager=engine._strategy_manager,
        regime_detector=engine._regime_detector,
        adaptive_threshold=engine._adaptive_threshold,
        db_pool=engine._db_pool,  # US-256
        data_quality_manager=engine._data_quality_manager,  # US-286
        strategy_filter=_sf,  # US-299
        portfolio_risk=engine._portfolio_risk,  # US-300
    )
    # SIT-3: Wire FlashGuard into ShadowMode
    if engine._flash_guard is not None:
        shadow._flash_guard = engine._flash_guard

    if engine._strategy_manager is not None:
        for sid in engine._strategy_manager.list_strategies():
            s = engine._strategy_manager.get_strategy(sid)
            if s:
                s.paper_mode = True
        for sid in engine._strategy_manager.list_strategies():
            try:
                await engine._strategy_manager.start_strategy(sid)
            except Exception as exc:
                logger.warning("Strategy validation: strategy %s start failed: %s", sid, exc)

    await shadow.start()
    logger.info("Strategy validation Shadow started: %s for %s", exchanges, symbols)

    try:
        orchestrator = StrategyValidationOrchestrator(
            paper_mode=shadow,
            telegram_sender=engine._telegram,
        )
        report = await orchestrator.run()
        logger.info(
            "Strategy validation complete: %d profitable, active=%s",
            len(report.profitable), report.profitable,
        )
    finally:
        await shadow.stop()


async def progressive_shadow_loop(engine: "Engine") -> None:
    """Progressive Shadow: 6-stage automatic extension (1H→2H→6H→12H→24H→72H).

    Creates ShadowMode and ProgressiveShadowOrchestrator, runs all 6 stages.
    Enabled when SHADOW_PROGRESSIVE=true (default: false → _paper_mode_loop).
    """
    from src.collectors.funding_rate_collector import FundingRateCollector
    from src.modes.shadow import ShadowMode
    from src.modes.progressive_shadow import ProgressiveShadowOrchestrator

    symbols = engine._settings.trading.symbols if engine._settings else ["BTC/USDT"]
    exchanges = engine._active_exchanges or _get_fallback_exchanges()

    # Create MultiStrategySignalProducer for 6 additional strategies
    from src.core.multi_signal import MultiStrategySignalProducer

    multi_signal_producer = MultiStrategySignalProducer(
        event_bus=engine._event_bus,
        latency_tracker=getattr(engine, "_latency_tracker", None),
    )

    # Dynamic symbol + exchange discovery — reads engine.json at runtime.
    _fr_symbols_ps = await FundingRateCollector.fetch_paired_symbols(
        http_client=getattr(engine, "_http_client", None),
    )
    funding_rate_collector = FundingRateCollector(
        symbols=_fr_symbols_ps,
        exchanges=FundingRateCollector.get_poll_exchanges(),
        http_client=getattr(engine, "_http_client", None),
    )

    # US-299: optional per-strategy filter from env var (comma-separated signal IDs)
    _sf2_raw = get_settings().operational.paper_strategy_filter.strip()
    _sf2 = [s.strip() for s in _sf2_raw.split(",") if s.strip()] if _sf2_raw else None

    engine._paper_mode = ShadowMode(
        signal_generator=engine._signal_generator,
        paper_executor=None,  # auto-creates with PowerLawSlippage(gamma=0.5)
        collector_manager=None,  # auto-creates CollectorManager
        market_recorder=engine._market_recorder,
        telegram=engine._telegram,
        symbols=symbols,
        exchanges=exchanges,
        multi_signal_producer=multi_signal_producer,
        funding_rate_collector=funding_rate_collector,
        strategy_manager=engine._strategy_manager,
        regime_detector=engine._regime_detector,
        adaptive_threshold=engine._adaptive_threshold,
        db_pool=engine._db_pool,  # US-256
        data_quality_manager=engine._data_quality_manager,  # US-286
        strategy_filter=_sf2,  # US-299
        portfolio_risk=engine._portfolio_risk,  # US-300
    )
    # SIT-3: Wire FlashGuard into ShadowMode
    if engine._flash_guard is not None:
        engine._paper_mode._flash_guard = engine._flash_guard

    # Set all registered strategies to shadow mode
    if engine._strategy_manager is not None:
        for sid in engine._strategy_manager.list_strategies():
            s = engine._strategy_manager.get_strategy(sid)
            if s:
                s.paper_mode = True
        for sid in engine._strategy_manager.list_strategies():
            try:
                await engine._strategy_manager.start_strategy(sid)
            except Exception as exc:
                logger.warning("Shadow strategy %s start failed: %s", sid, exc)

    # Build LiveGate for Stage 6
    if engine._db_pool is not None:
        try:
            from src.modes.live_gate import LiveGate
            from src.risk.kill_switch import KillSwitch

            # Wire with ALL exchange adapters for Tier 2/3 to function
            kill_switch = KillSwitch(
                redis_client=getattr(engine, '_redis_client', None),
                exchanges=list(engine._exchanges.values()),
            )
            engine._kill_switch = kill_switch  # store for shutdown and compliance
            # US-286: DQM health scores as exchange_health_fn
            _ehf2 = engine._data_quality_manager.get_all_health_scores if engine._data_quality_manager else None
            engine._live_gate = LiveGate(
                pool=engine._db_pool.pool,
                telegram=engine._telegram,
                kill_switch=kill_switch,
                circuit_breaker=engine._circuit_breaker,
                exchange_health_fn=_ehf2,
                settings=engine._settings,
            )
        except Exception as exc:
            logger.warning("LiveGate init failed (non-fatal): %s", exc)

    engine.context.paper_mode = engine._paper_mode
    engine.context.shadow_active = True
    engine.context.execution_mode = "paper"

    orchestrator = ProgressiveShadowOrchestrator(
        shadow_mode=engine._paper_mode,
        live_gate=engine._live_gate,
        telegram=engine._telegram,
        db_pool=engine._db_pool,
    )

    try:
        results = await orchestrator.run()
    except asyncio.CancelledError:
        return

    passed_count = sum(1 for r in results if r.passed)
    logger.info(
        "progressive_shadow_loop.finished",
        stages_passed=passed_count,
        total_stages=len(results),
    )


