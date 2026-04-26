"""Background lifecycle loops + health checks — Phase 4-6 main.py 모듈화 (2026-04-26).

Extracted from main.py:
A. Lifecycle (rebalancer, cancel_open_orders, close_all_positions, record_alert,
   populate_context, start_background_tasks, strategy_manager_loop, trade_consumer_loop)
B. Health/Heartbeat (health_check_loop, run_health_check, startup_position_scan,
   startup_compliance_audit, strategy_exit_poll_loop, reconcile_loop,
   peak_equity_persist_loop, heartbeat_loop, pm_drain_loop, redis_halt_watch_loop,
   btc_price_update_loop, dashboard_feed_loop)

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

from src.core.config import EngineMode, Settings, get_settings

logger = logging.getLogger(__name__)


async def rebalancer_loop(engine: "Engine") -> None:
    """US-120: Periodic inventory rebalancing check + Telegram alert."""
    while engine.state.running:
        try:
            await asyncio.sleep(engine._rebalancer.check_interval_s)

            if engine._rebalancer.has_critical_imbalance() and engine._telegram:
                try:
                    await engine._telegram.send_alert_kr(
                        "inventory_critical", {},
                    )
                except Exception:
                    pass

            suggestions = engine._rebalancer.check_and_suggest()
            if suggestions and engine._telegram:
                try:
                    await engine._telegram.send_alert_kr("inventory_rebalance", {
                        "suggestions": [
                            {"from": s.from_exchange, "to": s.to_exchange,
                             "amount_usd": s.amount_usd, "reason": s.reason}
                            for s in suggestions
                        ],
                    })
                except Exception:
                    pass

        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning("rebalancer_loop error: %s", exc)
            await asyncio.sleep(60)


async def cancel_open_orders(engine: "Engine") -> None:
    """US-155: Cancel all open/pending orders before shutdown (live mode only)."""
    logger.info("Cancelling open orders before shutdown...")
    total_cancelled = 0
    for eid, adapter in engine._exchanges.items():
        if not hasattr(adapter, "get_open_orders"):
            logger.debug("Exchange %s does not support get_open_orders — skipping", eid)
            continue
        try:
            pending = await adapter.get_open_orders()
        except Exception as exc:
            logger.warning("Failed to fetch open orders for %s: %s", eid, exc)
            continue
        for order in pending:
            try:
                symbol = getattr(order, "symbol", None)
                await adapter.cancel_order(order.order_id, symbol=symbol)
                logger.info("Cancelled order %s on %s (symbol=%s)", order.order_id, eid, symbol)
                total_cancelled += 1
            except Exception as exc:
                logger.error("Failed to cancel order %s on %s: %s", order.order_id, eid, exc)
                if engine._telegram:
                    try:
                        await engine._telegram.send_alert_kr(
                            "order_cancel_fail",
                            {"exchange": eid, "order_id": str(order.order_id), "error": str(exc)},
                        )
                    except Exception:
                        pass
    logger.info("Open order cancellation complete: %d orders cancelled", total_cancelled)


async def close_all_positions_on_shutdown(engine: "Engine") -> None:
    """Close all open futures positions before shutdown (live mode only).

    Called after _cancel_open_orders(). Non-fatal: logs errors and continues.
    """
    logger.info("Closing open positions before shutdown...")
    from src.core.models import Order, OrderSide, OrderType
    from decimal import Decimal

    total_closed = 0
    for eid, adapter in engine._exchanges.items():
        if not eid.endswith("_futures"):
            continue
        try:
            positions = await asyncio.wait_for(adapter.get_positions(), timeout=10.0)
        except Exception as exc:
            logger.warning("shutdown_get_positions_failed exchange=%s error=%s", eid, exc)
            continue

        for pos in positions:
            if pos.size == 0:
                continue
            close_side = OrderSide.SELL if pos.size > 0 else OrderSide.BUY
            close_order = Order(
                exchange_id=eid,
                symbol=pos.symbol,
                side=close_side,
                order_type=OrderType.MARKET,
                amount=abs(pos.size),
                metadata={"reduceOnly": True},
            )
            try:
                await asyncio.wait_for(adapter.place_order(close_order), timeout=10.0)
                logger.info(
                    "shutdown_position_closed exchange=%s symbol=%s side=%s size=%s",
                    eid, pos.symbol, close_side, abs(pos.size)
                )
                total_closed += 1
            except Exception as exc:
                logger.error(
                    "shutdown_position_close_failed exchange=%s symbol=%s error=%s",
                    eid, pos.symbol, exc
                )
    logger.info("Position close on shutdown complete: %d positions closed", total_closed)


def record_alert(engine: "Engine", alert_type: str, severity: str, message: str, metadata: dict | None = None) -> None:
    """Record a system alert for the dashboard API."""
    from datetime import datetime, timezone
    from uuid import uuid4
    engine.context.alert_history.append({
        "id": str(uuid4()),
        "type": alert_type,
        "severity": severity,
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "metadata": metadata or {},
    })

# ------------------------------------------------------------------
# Step 8: Populate EngineContext for API
# ------------------------------------------------------------------


async def populate_context(engine: "Engine") -> None:
    engine.context.strategy_manager = engine._strategy_manager
    engine.context.risk_guardian = engine._risk_guardian
    engine.context.position_manager = engine._position_manager
    engine.context.trade_consumer = engine._trade_consumer
    engine.context.engine = engine
    # US-284-b/a: Attribution + CapitalAllocator
    engine.context.attribution = engine._attribution
    engine.context.capital_allocator = engine._capital_allocator
    # US-277/278: PortfolioRiskManager
    engine.context.portfolio_risk = engine._portfolio_risk
    # Wave 3 modules
    engine.context.correlation_monitor = engine._correlation_monitor
    engine.context.slippage_feedback = engine._slippage_feedback
    engine.context.dynamic_sizer = engine._dynamic_sizer
    engine.context.tca_analyzer = engine._tca_analyzer
    engine.context.rebalancer = engine._rebalancer

    # Populate strategies dict for backward compatibility
    if engine._strategy_manager:
        for sid in engine._strategy_manager.list_strategies():
            s = engine._strategy_manager.get_strategy(sid)
            engine.context.strategies[sid] = {
                "id": sid,
                "type": getattr(s, "STRATEGY_TYPE", "unknown"),
                "enabled": s.is_active if s else False,
            }

# ------------------------------------------------------------------
# Step 9: Background Tasks
# ------------------------------------------------------------------


async def start_background_tasks(engine: "Engine") -> None:
    from src.main import DataMode  # lazy: avoid circular import
    from src.core.config import EngineMode, resolve_engine_mode, load_engine_config

    # Phase H-2: Resolve unified EngineMode (backtest/paper/shadow/live)
    # Priority: ENGINE_MODE env > engine.json > legacy EXECUTION_MODE+DATA_MODE
    _engine_cfg = load_engine_config()
    _op = get_settings().operational
    engine._engine_mode = resolve_engine_mode(
        execution_mode=_op.execution_mode or None,
        data_mode=_op.data_mode or None,
        engine_mode=get_settings().engine_mode.value if get_settings().engine_mode else _engine_cfg.get("mode"),
    )

    # Legacy compatibility: set _data_mode for code that still reads it
    _mode_to_data = {
        EngineMode.BACKTEST: DataMode.SYNTHETIC,
        EngineMode.PAPER: DataMode.SHADOW,
        EngineMode.LIVE: DataMode.REAL_AUTHENTICATED,
    }
    engine._data_mode = _mode_to_data.get(engine._engine_mode, DataMode.SYNTHETIC)

    logger.info("engine_mode=%s (legacy data_mode=%s)", engine._engine_mode, engine._data_mode)

    # Common background tasks (all modes)
    tasks = [
        asyncio.create_task(engine._trade_consumer_loop(), name="trade_consumer"),
        asyncio.create_task(engine._health_check_loop(), name="health_check"),
        asyncio.create_task(engine._reconcile_loop(), name="reconcile"),
        asyncio.create_task(engine._heartbeat_loop(), name="ws_heartbeat"),
        asyncio.create_task(engine._dashboard_feed_loop(), name="dashboard_feed"),
        asyncio.create_task(engine._btc_price_update_loop(), name="btc_price_update"),
        asyncio.create_task(engine._redis_halt_watch_loop(), name="redis_halt_watch"),
        # BUG-81: Poll strategies for pending exit requests (FF settlement, SF timeout)
        asyncio.create_task(engine._strategy_exit_poll_loop(), name="strategy_exit_poll"),
    ]
    # WS-4 Step 1: PositionManager drain task — exception surfacing, ordered writes
    engine._pm_drain_task = asyncio.create_task(engine._pm_drain_loop(), name="pm_drain")
    tasks.append(engine._pm_drain_task)

    # --- Single-axis mode routing (Phase H-2) ---
    if engine._engine_mode == EngineMode.BACKTEST:
        # Backtest: TimescaleDB orderbook replay via BacktestMode + WalkForwardAnalyzer
        tasks.append(
            asyncio.create_task(engine._backtest_mode_task(), name="backtest_mode")
        )
        logger.info("EngineMode: BACKTEST — TimescaleDB replay + WalkForwardAnalyzer")

    elif engine._engine_mode == EngineMode.PAPER:
        # Paper: live WS data + SimExecutor (= old shadow mode)
        # Direct in-process routing (no Redis consumer loop)
        strategy_validation = get_settings().operational.strategy_validation
        shadow_progressive = get_settings().operational.paper_progressive
        if strategy_validation:
            tasks.append(
                asyncio.create_task(engine._strategy_validation_loop(), name="strategy_validation")
            )
            logger.info("EngineMode: PAPER (STRATEGY_VALIDATION)")
        elif shadow_progressive:
            tasks.append(
                asyncio.create_task(engine._progressive_shadow_loop(), name="progressive_shadow")
            )
            logger.info("EngineMode: PAPER (PROGRESSIVE)")
        else:
            tasks.append(
                asyncio.create_task(engine._paper_mode_loop(), name="paper_mode")
            )
            logger.info("EngineMode: PAPER — live WS data + SimExecutor")

    elif engine._engine_mode == EngineMode.LIVE:
        # Live: live WS data + AtomicExecutor full capital
        # BUG-73: Do NOT start _strategy_manager_loop in LIVE mode.
        # live.py routes signals via route_signal() directly (in-process, no Redis).
        # Starting StrategyManager's Redis consume loop causes a race where the same
        # FF signal is processed by BOTH live.py (Path A, full accounting) AND
        # StrategyManager → TradeConsumer (Path B, no PnL/Telegram/DB accounting).
        tasks.append(
            asyncio.create_task(engine._live_mode_loop(), name="live_mode")
        )
        logger.info("EngineMode: LIVE — live WS data + AtomicExecutor (direct in-process routing)")

    else:
        logger.warning("Unknown engine_mode=%s — falling back to BACKTEST", engine._engine_mode)
        tasks.append(
            asyncio.create_task(engine._orderbook_feed_loop(), name="orderbook_feed")
        )

    # Phase S21: TelegramCommandHandler removed (Dev봇에 통합됨)
    # TradeBot poll loop (InfraBot/DevBot → bot-gateway)
    if engine._trade_bot and engine._trade_bot.enabled:
        tasks.append(asyncio.create_task(engine._trade_bot.poll_loop(), name="trade_bot"))
        logger.info("trade_bot poll_loop started")

    # TradeTelegramBot daily report scheduler
    if engine._trade_bot and engine._trade_bot.enabled:
        try:
            tasks.append(asyncio.create_task(engine._trade_bot.schedule_daily_report(), name="daily_report"))
            logger.info("Daily report scheduler started (09:00 KST)")
        except Exception as exc:
            logger.warning("Daily report scheduler failed (non-fatal): %s", exc)

    # US-120: Inventory rebalancer background loop
    if engine._rebalancer is not None:
        tasks.append(asyncio.create_task(
            engine._rebalancer_loop(), name="rebalancer"
        ))

    # Phase S21: SmartTelegramAlerter removed (Trade봇에 통합)

    # US-173: RegimeDetector background task (60s periodic)
    if engine._regime_detector is not None:
        tasks.append(asyncio.create_task(
            engine._regime_detect_loop(), name="regime_detect"
        ))

    # US-174: AdaptiveThreshold adjustment task (1h periodic)
    if engine._adaptive_threshold is not None:
        tasks.append(asyncio.create_task(
            engine._adaptive_threshold_loop(), name="adaptive_threshold"
        ))

    # US-256: peak_equity DB persistence loop (5min periodic)
    tasks.append(asyncio.create_task(
        engine._peak_equity_persist_loop(), name="peak_equity_persist"
    ))

    # US-251: HMM model retraining loop (24h cycle)
    tasks.append(asyncio.create_task(
        engine._hmm_training_loop(), name="hmm_training"
    ))

    # US-252: XGBoost + ONNX training loop (24h cycle)
    tasks.append(asyncio.create_task(
        engine._xgb_training_loop(), name="xgb_training"
    ))

    # US-280: LiveGate continuous monitor (all modes)
    if engine._live_gate is not None:
        _op = get_settings().operational
        if _op.live_gate_continuous_enabled:
            _lg_interval = _op.live_gate_monitor_interval_s
            tasks.append(asyncio.create_task(
                engine._live_gate.start_continuous_monitor(
                    interval_s=_lg_interval,
                    risk_guardian=engine._risk_guardian,
                ),
                name="live_gate_monitor",
            ))
            logger.info("LiveGate continuous monitor started (interval=%ds)", _lg_interval)

    engine.state.background_tasks.extend(tasks)
    logger.info("Started %d background tasks", len(tasks))


async def strategy_manager_loop(engine: "Engine") -> None:
    """Start StrategyManager signal consumption."""
    try:
        await engine._strategy_manager.start()
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.error("StrategyManager loop error: %s", exc)


async def trade_consumer_loop(engine: "Engine") -> None:
    """Start TradeRequestConsumer."""
    try:
        await engine._trade_consumer.start()
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.error("TradeConsumer loop error: %s", exc)


async def health_check_loop(engine: "Engine") -> None:
    while engine.state.running:
        try:
            await engine._run_health_check()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("Health check error: %s", exc)
        await asyncio.sleep(engine.HEALTH_CHECK_INTERVAL)


async def run_health_check(engine: "Engine") -> None:
    # Log exchange health scores and update _exchange_health (CRITICAL FIX: was never populated)
    for eid, adapter in engine._exchanges.items():
        score = adapter.health_score
        engine._exchange_health[eid] = Decimal(str(score))
        # US-286: Sync health data to DataQualityManager
        if engine._data_quality_manager is not None:
            engine._data_quality_manager.record_heartbeat(eid)
        # BUG-48: refresh native adapter's own HealthChecker heartbeat every 10s.
        # REST-only execution adapters (no WS stream) would otherwise go stale after
        # 150s and drop to health_score=0.6 → livelock (all trades rejected).
        if hasattr(adapter, '_health') and hasattr(adapter._health, 'record_heartbeat'):
            adapter._health.record_heartbeat()
        if score < 0.50:
            logger.critical("Exchange %s health_score=%.2f — approaching rejection threshold", eid, score)
        elif score < 0.70:
            logger.warning("Exchange %s health_score=%.2f", eid, score)
        elif score < 0.90:
            logger.debug("Exchange %s health_score=%.2f", eid, score)

    # US-286: Periodic DQM cleanup + stats logging
    if engine._data_quality_manager is not None:
        cleaned = engine._data_quality_manager.cleanup_expired()
        stats = engine._data_quality_manager.get_stats()
        if stats["check_count"] > 0:
            logger.debug(
                "dqm_stats",
                checks=stats["check_count"],
                rejects=stats["reject_count"],
                blacklisted=stats["active_blacklist"],
                cleaned=cleaned,
            )

    # Log trade consumer metrics
    if engine._trade_consumer:
        metrics = engine._trade_consumer
        logger.debug(
            "Health OK — trades: processed=%d success=%d rejected=%d",
            metrics.processed_count,
            metrics.execution_success_count,
            metrics.risk_rejected_count,
        )
    else:
        logger.debug("Health check OK")


async def startup_position_scan(engine: "Engine") -> None:
    """US-250: Scan for orphaned positions (WAL) on engine startup."""
    # ER2-22: WAL replay — reconstruct Redis state from PostgreSQL on startup
    if engine._recovery_manager is not None:
        try:
            recovered = await engine._recovery_manager.recover()
            if recovered:
                logger.info("RecoveryManager WAL replay completed successfully")
            else:
                logger.warning("RecoveryManager WAL replay: reconciliation failed, HALT flag set")
        except Exception as exc:
            logger.warning("RecoveryManager.recover() startup error: %s", exc)

    if engine._position_recovery is None:
        return
    # Use public .redis property (raises RuntimeError if not connected)
    if engine._redis_client is None:
        logger.debug("startup_position_scan skipped: no Redis client available")
        return
    try:
        redis_conn = engine._redis_client.redis  # public property → aioredis.Redis
    except RuntimeError:
        logger.debug("startup_position_scan skipped: Redis not connected")
        return
    try:
        from src.execution.position_recovery import PositionRecovery
        recovery = PositionRecovery(redis=redis_conn)
        result = await recovery.scan()
        if result.positions_found > 0:
            logger.warning(
                "startup_orphan_positions found=%d closed=%d resumed=%d skipped=%d",
                result.positions_found, result.closed, result.resumed, result.skipped,
            )
            if engine._telegram:
                await engine._telegram.send_alert_kr("orphan_positions", {
                    "found": result.positions_found,
                    "closed": result.closed,
                    "resumed": result.resumed,
                })
        else:
            logger.info("startup_position_scan: no orphaned positions found")
        logger.info("[position_recovery] scan completed")
    except Exception as exc:
        logger.warning("startup_position_scan_error error=%s", exc)


async def startup_compliance_audit(engine: "Engine") -> None:
    """US-250-a: Run ComplianceChecker on engine startup (non-blocking)."""
    try:
        from src.infra.compliance import ComplianceChecker, ComplianceStatus
        checker = ComplianceChecker(
            db_pool=engine._db_pool,
            kill_switch=None,
            circuit_breaker=engine._circuit_breaker,
            telegram=engine._telegram,
        )
        report = await checker.run_audit()
        if report.fail_count > 0:
            logger.error(
                "compliance_startup_audit: FAIL=%d PARTIAL=%d PASS=%d score=%.1f%%",
                report.fail_count, report.partial_count, report.pass_count, report.score_pct,
            )
            fail_names = [i.name for i in report.items if i.status == ComplianceStatus.FAIL]
            logger.error("compliance_failures: %s", fail_names)
        else:
            logger.info(
                "compliance_startup_audit: PASS=%d PARTIAL=%d score=%.1f%%",
                report.pass_count, report.partial_count, report.score_pct,
            )
    except ImportError:
        logger.debug("compliance_checker_not_available")
    except Exception as exc:
        logger.warning("compliance_startup_audit_error error=%s", exc)


async def strategy_exit_poll_loop(engine: "Engine") -> None:
    """BUG-81: Poll strategies for pending exit TradeRequests every 60s.

    FundingRateStrategy queues settlement-close TradeRequests in
    _pending_exit_requests. FuturesFuturesStrategy queues holding-timeout
    exits via pop_exit_requests(). Without this loop those requests are
    never routed and positions remain open forever.

    NOTE: This loop is SKIPPED when LiveMode is active. LiveMode._dedup_cleanup_loop
    already polls strategies every 60s and routes exits through _execute_trade_request
    (full pipeline: kill switch, circuit breaker, Telegram alerts, PnL tracking).
    Running both would cause a dual-drain race where exit requests are stolen between
    consumers. This loop handles non-Live modes (e.g., backtest, paper w/o LiveMode).
    """
    try:
        while engine.state.running:
            await asyncio.sleep(60)
            # Skip when LiveMode is active — it has its own _dedup_cleanup_loop consumer
            if getattr(engine, "_live_mode", None) is not None:
                continue
            if not engine._strategy_manager:
                continue
            try:
                # BUG-05: use public API instead of _strategies private dict
                for sid in engine._strategy_manager.list_strategies():
                    strategy = engine._strategy_manager.get_strategy(sid)
                    if strategy is None:
                        continue
                    if hasattr(strategy, "pop_exit_requests"):
                        for exit_req in strategy.pop_exit_requests():
                            logger.info(
                                "strategy_exit_poll strategy=%s legs=%d reason=%s",
                                sid,
                                len(exit_req.legs),
                                exit_req.metadata.get("reason", "unknown"),
                            )
                            if engine._event_bus:
                                await engine._event_bus.publish(
                                    "leviathan:trade_requests",
                                    exit_req.model_dump(mode="json"),
                                )
            except Exception as exc:
                logger.warning("strategy_exit_poll_loop error=%s", exc)
    except asyncio.CancelledError:  # BUG-02: handle cancellation cleanly
        pass


async def reconcile_loop(engine: "Engine") -> None:
    interval = get_settings().operational.reconciliation_interval_s
    while engine.state.running:
        try:
            await asyncio.sleep(interval)

            # BUG-155: balance reconcile needs paper_mode (snapshot source).
            # In live mode, skip balance snapshot but still run PositionReconciler below.
            current: dict[str, str] = {}
            if engine._paper_mode is not None and engine._redis_client is not None:
                try:
                    current = engine._paper_mode._balance_tracker.summary()
                except Exception:
                    current = {}

            # BUG-155: balance snapshot only when both current + redis available
            _can_snapshot = bool(current) and engine._redis_client is not None
            # Read last saved snapshot from Redis
            raw = None
            if _can_snapshot:
                raw = await engine._redis_client.hgetall("leviathan:recovery:balances")
            if raw:
                recovery = {
                    (k.decode() if isinstance(k, bytes) else k):
                    (v.decode() if isinstance(v, bytes) else v)
                    for k, v in raw.items()
                }
                mismatches = []
                for ex_id, cur_str in current.items():
                    if ex_id in recovery:
                        try:
                            cur_val = float(cur_str)
                            rec_val = float(recovery[ex_id])
                            if rec_val > 0 and abs(cur_val - rec_val) / rec_val > 0.01:
                                mismatches.append(
                                    f"{ex_id}: memory={cur_val:.4f} redis={rec_val:.4f}"
                                )
                        except (ValueError, ZeroDivisionError):
                            pass
                if mismatches:
                    msg = "잔고 불일치: " + ", ".join(mismatches)
                    logger.warning(msg)
                    if engine._telegram:
                        try:
                            await engine._telegram.send_alert_kr(
                                "balance_mismatch", {"detail": msg},
                            )
                        except Exception:
                            pass

            # Save current state as the new recovery snapshot (only if valid)
            if _can_snapshot:
                await engine._redis_client.hset("leviathan:recovery:balances", current)
                logger.debug("Position reconciliation tick — snapshot saved (%d exchanges)", len(current))

            # US-250: PositionReconciler — compare engine vs exchange positions
            # NOTE: _position_manager must be populated for this to be meaningful.
            # In live mode, _paper_mode=None causes early continue above, so this
            # block is unreachable in live mode until _position_manager is wired.
            # TODO: wire _position_manager.update_position() from live trade fills.
            if engine._position_reconciler is not None:
                try:
                    from src.core.models import Position
                    engine_positions: dict[str, Position] = {}
                    # BUG-159: skip reconcile if position_manager not wired
                    # (live mode currently doesn't populate it consistently →
                    # every exchange position becomes false 'no record' CRITICAL)
                    if engine._position_manager is None:
                        continue
                    _all_pos = list(engine._position_manager.get_all_positions())
                    if not _all_pos:
                        # No engine positions tracked → skip reconcile cycle
                        continue
                    if engine._position_manager is not None:
                        # BUG-223: aggregate cross-strategy positions on same (exchange,symbol)
                        # so engine total matches exchange-reported net. See reconciler.aggregate_engine_positions.
                        from src.execution.reconciler import aggregate_engine_positions
                        engine_positions = aggregate_engine_positions(_all_pos)
                    result = await engine._position_reconciler.reconcile(engine_positions)
                    if result.has_discrepancy:
                        logger.warning(
                            "position_reconciler_discrepancy count=%d",
                            len(result.discrepancies),
                        )
                except Exception as exc:
                    logger.warning("position_reconciler_error: %s", exc)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("Reconcile error: %s", exc)


async def peak_equity_persist_loop(engine: "Engine") -> None:
    """US-256: Persist peak_equity to TimescaleDB (primary) + JSON (backup) every 5 minutes."""
    import json
    import pathlib
    state_path = pathlib.Path(__file__).parent.parent / ".omc" / "state" / "peak_equity.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)

    _CREATE_TABLE = """
        CREATE TABLE IF NOT EXISTS engine_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
    """
    _UPSERT = """
        INSERT INTO engine_state (key, value, updated_at)
        VALUES ('peak_equity', $1, NOW())
        ON CONFLICT (key) DO UPDATE SET value = $1, updated_at = NOW()
    """

    # Restore on startup: DB first, then JSON fallback
    if engine._peak_equity is None:
        if engine._db_pool is not None:
            try:
                async with engine._db_pool.pool.acquire() as conn:
                    await conn.execute(_CREATE_TABLE)
                    row = await conn.fetchrow(
                        "SELECT value FROM engine_state WHERE key = 'peak_equity'"
                    )
                    if row is not None:
                        engine._peak_equity = Decimal(row["value"])
                        logger.info("peak_equity_restored_from_db value=%s", row["value"])
            except Exception as exc:
                logger.warning("peak_equity_db_restore_failed error=%s", exc)
        if engine._peak_equity is None:
            try:
                if state_path.exists():
                    data = json.loads(state_path.read_text())
                    stored = data.get("peak_equity")
                    if stored:
                        engine._peak_equity = Decimal(str(stored))
                        logger.info("peak_equity_restored_from_file value=%s", stored)
            except Exception as exc:
                logger.warning("peak_equity_file_restore_failed error=%s", exc)

    while engine.state.running:
        await asyncio.sleep(300)  # 5 minutes
        try:
            if engine._peak_equity is not None:
                val_str = str(engine._peak_equity)
                # Primary: TimescaleDB
                if engine._db_pool is not None:
                    try:
                        async with engine._db_pool.pool.acquire() as conn:
                            await conn.execute(_CREATE_TABLE)
                            await conn.execute(_UPSERT, val_str)
                        logger.debug("peak_equity_persisted_to_db value=%s", val_str)
                    except Exception as exc:
                        logger.warning("peak_equity_db_persist_failed error=%s", exc)
                # Backup: JSON file (dual write)
                try:
                    state_path.write_text(json.dumps({"peak_equity": val_str}))
                    logger.debug("peak_equity_persisted_to_file value=%s", val_str)
                except Exception as exc:
                    logger.debug("peak_equity_file_persist_error error=%s", exc)
            # ER5-04: Persist shadow stats alongside peak_equity
            if engine._paper_mode is not None and engine._db_pool is not None:
                try:
                    stats = engine._paper_mode._stats
                    async with engine._db_pool.pool.acquire() as conn:
                        await conn.execute(_CREATE_TABLE)
                        _UPSERT_SHADOW = """
                            INSERT INTO engine_state (key, value, updated_at)
                            VALUES ($1, $2, NOW())
                            ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = NOW()
                        """
                        await conn.execute(_UPSERT_SHADOW, "paper_total_pnl", str(stats.total_pnl))
                        await conn.execute(_UPSERT_SHADOW, "paper_trades_executed", str(stats.trades_executed))
                    logger.debug("paper_stats_persisted trades=%s pnl=%s", stats.trades_executed, stats.total_pnl)
                except Exception as exc:
                    logger.debug("shadow_stats_persist_error error=%s", exc)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.debug("peak_equity_persist_error error=%s", exc)


async def heartbeat_loop(engine: "Engine") -> None:
    while engine.state.running:
        try:
            await asyncio.sleep(engine.HEARTBEAT_INTERVAL)
            if engine.context.ws_manager:
                await engine.context.ws_manager.send_heartbeat()
            # Dead Man's Switch: InfraBot watchdog monitors this key (TTL=30s, written every 5s)
            if engine._redis_client is not None:
                try:
                    await engine._redis_client.set("leviathan:heartbeat", "1", ex=30)
                except Exception:
                    pass  # Redis 실패는 비치명적
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning("Heartbeat error: %s", exc)


async def pm_drain_loop(engine: "Engine") -> None:
    """WS-4 Step 1: PositionManager 작업 큐 드레인 loop.

    asyncio.ensure_future 를 큐 기반으로 대체:
    - 순서 보장 (open 후 close 순서 유지)
    - 예외 surface (로그 + 메트릭, swallow 금지)
    - 엔진 lifecycle 에 바인딩 (start/stop)
    """
    if engine._position_manager is None:
        return
    while engine.state.running:
        try:
            op, kwargs = await engine._pm_queue.get()
            try:
                await getattr(engine._position_manager, op)(**kwargs)
            except Exception as exc:
                engine._pm_drain_errors += 1
                logger.error(
                    "pm_drain_error op=%s sym=%s err=%s (errors_total=%d)",
                    op, kwargs.get("symbol"), exc, engine._pm_drain_errors,
                )
            finally:
                engine._pm_queue.task_done()
        except asyncio.CancelledError:
            # Engine shutdown
            return
        except Exception as exc:
            logger.error("pm_drain_loop_unexpected err=%s — continuing", exc)
            await asyncio.sleep(0.1)  # backoff to prevent tight loop


async def redis_halt_watch_loop(engine: "Engine") -> None:
    """Redis leviathan:halt 키 폴링 — InfraBot 원격 halt 명령 수신.

    InfraBot이 엔진 하트비트 소실 감지 시 leviathan:halt=1 설정.
    이 루프가 감지하면 in-process KillSwitch 활성화.
    """
    from src.risk.kill_switch import is_halted, halt_local
    while engine.state.running:
        try:
            await asyncio.sleep(5)
            if engine._redis_client is None:
                continue
            try:
                val = await engine._redis_client.get("leviathan:halt")
                if val and not is_halted():
                    logger.critical(
                        "redis_external_halt_received — "
                        "InfraBot 또는 외부 프로세스가 halt 명령 전송"
                    )
                    halt_local()
                    if engine._kill_switch is not None:
                        asyncio.create_task(
                            engine._kill_switch.trigger(),
                            name="external_halt_kill_switch",
                        )
                    engine.state.running = False
                    engine.context.running = False
                    engine._shutdown_event.set()
            except Exception:
                pass
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning("redis_halt_watch_error: %s", exc)


async def btc_price_update_loop(engine: "Engine") -> None:
    """Periodically refresh the BTC reference price from live PriceHub data.

    Overrides the static BTC_REFERENCE_PRICE env default ($50,000) with
    the actual live mid-price so position sizing stays accurate.
    Updates every 60 seconds; skips if PriceHub has no BTC/USDT data yet.
    """
    global _BTC_REFERENCE_PRICE
    while engine.state.running:
        try:
            await asyncio.sleep(60)
            if engine._price_hub is not None:
                mid = engine._price_hub.get_mid_price("BTC/USDT")
                if mid is not None and mid > Decimal("1000"):
                    _BTC_REFERENCE_PRICE = mid
                    logger.debug("btc_price_updated price=%s", mid)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning("btc_price_update_error: %s", exc)


async def dashboard_feed_loop(engine: "Engine") -> None:
    """Broadcast engine state to all WebSocket clients every second."""
    FEED_INTERVAL = 1.0
    while engine.state.running:
        try:
            await asyncio.sleep(FEED_INTERVAL)
            ws = engine.context.ws_manager
            if not ws or ws.connection_count == 0:
                continue

            # Strategy status
            strategies = []
            for sid, info in engine.context.strategies.items():
                strategies.append({
                    "id": sid,
                    "enabled": info.get("enabled", False),
                    "type": info.get("type", "unknown"),
                })

            # PnL
            realized = float(engine.context.realized_pnl)
            unrealized = float(engine.context.unrealized_pnl)

            # Positions
            positions = []
            if engine.context.position_manager:
                try:
                    for p in engine.context.position_manager.get_all_positions():
                        positions.append({
                            "strategy_id": p.strategy_id,
                            "exchange_id": p.exchange_id,
                            "symbol": p.symbol,
                            "side": p.side,
                            "pnl": float(p.unrealized_pnl),
                        })
                except Exception:
                    pass

            shadow_stats = None
            if engine._paper_mode and hasattr(engine._paper_mode, 'get_snapshot'):
                try:
                    shadow_stats = engine._paper_mode.get_snapshot()
                except Exception:
                    pass

            # US-210: Compute extended fields
            total_equity = realized + unrealized
            active_strategy_count = sum(
                1 for info in engine.context.strategies.values()
                if info.get("enabled", False)
            )
            # Win rate from shadow stats or 0
            feed_win_rate = 0.0
            if shadow_stats:
                feed_win_rate = float(shadow_stats.get("win_rate", 0.0))

            # WS mode: prefer engine_mode (authoritative) over context.execution_mode
            # Never downgrade live→paper just because paper_mode object exists
            _ws_mode = (
                engine._engine_mode.value
                if hasattr(engine, "_engine_mode")
                else engine.context.execution_mode
            )
            if _ws_mode != "live":
                _pm_obj = getattr(engine.context, "paper_mode", None) or getattr(engine.context, "shadow_mode", None)
                if _pm_obj is not None and hasattr(_pm_obj, "_stats"):
                    _ws_mode = "paper"

            await ws.broadcast({
                "type": "state_update",
                "data": {
                    "running": engine.context.running,
                    "kill_switch": engine.context.kill_switch_active,
                    "mode": _ws_mode,
                    "strategy_count": len(strategies),
                    "strategies": strategies,
                    "pnl": {
                        "realized": realized,
                        "unrealized": unrealized,
                        "total": realized + unrealized,
                    },
                    "positions": positions,
                    "position_count": len(positions),
                    "shadow_stats": shadow_stats,
                    # US-210: Extended fields
                    "total_equity": total_equity,
                    "win_rate": feed_win_rate,
                    "active_strategy_count": active_strategy_count,
                },
                "ts": time.time(),
            })
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning("Dashboard feed error: %s", exc)


