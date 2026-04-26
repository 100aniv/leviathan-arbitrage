"""Risk + Execution initialization — Phase 4-4 main.py 모듈화 (2026-04-26).

Extracted from main.py (4 methods, ~845 LOC):
- init_risk            (RiskGuardian + CircuitBreaker + KillSwitch + per-strategy CB)
- init_execution       (AtomicExecutor + ExecutionJournal + OrderRouter + LiveMode wiring)
- build_risk_check_fn  (RiskGuardian check function for AtomicExecutor)
- on_execution_result  (ExecutionResult callback: PnL update + WAL + position tracking)

각 함수는 ``engine: "Engine"`` 첫 인자.
"""
from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.main import Engine

logger = logging.getLogger(__name__)


def _btc_ref_price() -> Decimal:
    """Lazy import — main.py module-level constant. Preserves identity for tests."""
    from src.main import _BTC_REFERENCE_PRICE
    return _BTC_REFERENCE_PRICE


async def init_risk(engine: "Engine") -> None:
    try:
        from src.risk.circuit_breaker import CircuitBreaker

        # Wire Telegram into CircuitBreaker state changes
        cb_state_callback = None
        if engine._telegram and engine._telegram._enabled:
            def cb_state_callback(state, reason):
                import asyncio
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(
                        engine._telegram.send_circuit_breaker_event(state.value, reason)
                    )
                except RuntimeError:
                    pass

        from src.core.config_loader import get_config as _gc_cb
        _cb_mdd = float(_gc_cb("risk.circuit_breaker_mdd_threshold", default=0.02))
        _cb_loss = int(_gc_cb("risk.circuit_breaker_consecutive_loss_limit", default=5))
        _cb_err = float(_gc_cb("risk.circuit_breaker_api_error_rate_threshold", default=0.20))
        _cb_cool = float(_gc_cb("risk.circuit_breaker_cooldown_seconds", default=300.0))
        _cb_half = int(_gc_cb("risk.circuit_breaker_half_open_test_count", default=3))

        engine._circuit_breaker = CircuitBreaker(
            mdd_threshold=_cb_mdd,
            consecutive_loss_limit=_cb_loss,
            api_error_rate_threshold=_cb_err,
            cooldown_seconds=_cb_cool,
            half_open_test_count=_cb_half,
            on_state_change=cb_state_callback,
        )
        logger.info("CircuitBreaker initialized mdd=%.3f loss_limit=%d cooldown=%.0f", _cb_mdd, _cb_loss, _cb_cool)
    except Exception as exc:
        logger.warning("CircuitBreaker init failed: %s", exc)

    try:
        from src.risk.guardian import RiskGuardian
        # BUG-A: engine.json is the single source for risk config
        from src.core.config import load_engine_config as _lec_risk
        _risk_cfg = _lec_risk().get("risk", {})
        _use_pct = _risk_cfg.get("use_percentage", False)
        if _use_pct and "max_position_pct" in _risk_cfg:
            _max_pos_pct = Decimal(str(_risk_cfg["max_position_pct"])) / Decimal("100")
        else:
            _max_pos_pct = Decimal("0.10")  # fallback: 10%
        # Load max_drawdown_pct from config (max_daily_loss_pct), fallback to 50% for alpha testing
        if _use_pct and "max_daily_loss_pct" in _risk_cfg:
            _max_dd_pct = Decimal(str(_risk_cfg["max_daily_loss_pct"])) / Decimal("100")
        else:
            _max_dd_pct = Decimal("0.50")  # fallback: 50% (permissive for alpha testing)
        # Amendment 7: wire max_net_exposure_per_asset from trading.json
        _max_net_exp = Decimal(str(_risk_cfg.get("max_net_exposure_per_asset", 0)))
        # BUG-100: max_single_trade_pct must match the largest per-strategy trade cap.
        # futures_futures_max_position_usd=12, capital=120 → 10%.
        # Default 5% (=$6) blocks all FF trades of $12 notional.
        # BUG-102: add 5% tolerance buffer — float division (size=max_pos/price) causes
        # notional to exceed limit by $0.04 (e.g. 12.04 > 12.00) → guardian rejects profitable trades.
        # Guardian is a safety net; 5% tolerance still blocks truly oversized trades.
        # Read engine.json directly (this method has no access to _init_strategies() locals).
        from src.core.config import load_engine_config as _load_ecfg
        from src.core.config_loader import get_config as _gc_risk
        _ecfg_r = _load_ecfg()
        _cap_cfg_r = _ecfg_r.get("capital", {})
        _tier_r = _cap_cfg_r.get("tier", "alpha")
        _cap_usd_r = Decimal(str(
            _cap_cfg_r.get("tiers", {}).get(_tier_r, {}).get("initial_usd", 70)
        ))
        _ff_max_r = Decimal(str(_gc_risk(
            "strategy_filters.futures_futures_max_position_usd", default=12
        )))
        _max_single_trade_pct = (
            (_ff_max_r / _cap_usd_r) * Decimal("1.05") if _cap_usd_r > 0 else Decimal("0.11")
        )
        # BUG-80: Wire ALL RiskGuardian params from config (was only 4 of 10)
        from src.core.config_loader import get_config as _rg_gc
        _rg_max_exposure = Decimal(str(
            _rg_gc("risk.max_net_exposure_pct", default=30)
        )) / Decimal("100")
        _rg_max_rollback = Decimal(str(
            _rg_gc("risk.max_rollback_threshold", default=0.02)
        ))
        _rg_max_concurrent = int(
            _rg_gc("strategy_filters.futures_max_concurrent_positions", default=4)
        )
        _rg_warmup = float(_rg_gc("risk.warmup_seconds", default=120.0))
        _rg_alloc_cfg = _load_ecfg().get("capital", {}).get("strategies", {})
        _rg_alloc_pct = {
            k: float(v.get("allocation_pct", 25))
            for k, v in _rg_alloc_cfg.items()
        }
        engine._risk_guardian = RiskGuardian(
            circuit_breaker=engine._circuit_breaker,
            max_position_pct=_max_pos_pct,
            max_drawdown_pct=_max_dd_pct,
            max_net_exposure_per_asset=_max_net_exp,
            max_single_trade_pct=_max_single_trade_pct,
            max_exposure_pct=_rg_max_exposure,
            max_rollback_threshold=_rg_max_rollback,
            max_concurrent_positions=_rg_max_concurrent,
            warmup_seconds=_rg_warmup,
            capital_allocation_pct=_rg_alloc_pct,
        )
        logger.info(
            "RiskGuardian initialized with 9 pre-trade checks, max_position_pct=%.1f%% "
            "max_single_trade_pct=%.1f%% max_net_exposure_per_asset=%s",
            float(_max_pos_pct) * 100,
            float(_max_single_trade_pct) * 100,
            _max_net_exp,
        )
    except Exception as exc:
        logger.warning("RiskGuardian init failed: %s", exc)

    # US-222/228: PerStrategyCB → Guardian integration
    try:
        from src.risk.per_strategy_cb import PerStrategyCB
        engine._per_strategy_cb = PerStrategyCB()
        if engine._risk_guardian is not None:
            engine._risk_guardian.per_strategy_cb = engine._per_strategy_cb
        logger.info("PerStrategyCB initialized (4-state: ACTIVE/THROTTLED/HALTED/SUSPENDED)")
    except Exception as exc:
        logger.warning("PerStrategyCB init failed (non-fatal): %s", exc)

    # US-118: CorrelationMonitor → Guardian integration
    try:
        from src.risk.correlation_monitor import CorrelationMonitor
        _corr_window = int(_rg_gc("risk.correlation_window", default=30))
        _corr_threshold = float(_rg_gc("risk.correlation_threshold", default=0.7))
        engine._correlation_monitor = CorrelationMonitor(window=_corr_window, threshold=_corr_threshold)
        if engine._risk_guardian is not None:
            engine._risk_guardian.correlation_monitor = engine._correlation_monitor
        logger.info("CorrelationMonitor initialized (window=%d, threshold=%.1f)", _corr_window, _corr_threshold)
    except Exception as exc:
        logger.warning("CorrelationMonitor init failed (non-fatal): %s", exc)

    # US-278: Wire PortfolioRiskManager into RiskGuardian
    if engine._portfolio_risk is not None and engine._risk_guardian is not None:
        engine._risk_guardian.portfolio_risk = engine._portfolio_risk

    # US-286: DataQualityManager → RiskGuardian Check #5
    try:
        from src.core.data_quality_manager import DataQualityManager
        from src.execution.paper_adapter import PaperExchangeAdapter
        engine._data_quality_manager = DataQualityManager()
        # Register known exchanges (Paper adapters → always_healthy=True)
        for eid, adapter in engine._exchanges.items():
            is_paper = isinstance(adapter, PaperExchangeAdapter)
            engine._data_quality_manager.register_exchange(eid, always_healthy=is_paper)
        if engine._risk_guardian is not None:
            engine._risk_guardian.data_quality_manager = engine._data_quality_manager
        logger.info("DataQualityManager initialized (%d exchanges)", len(engine._exchanges))
    except Exception as exc:
        logger.warning("DataQualityManager init failed (non-fatal): %s", exc)

    # SIT-3: FlashGuard — rapid price movement detection (5min window, 3% threshold)
    try:
        from src.risk.flash_guard import FlashGuard
        _fg_threshold = float(_rg_gc("risk.flash_guard_threshold_pct", default=3.0))
        _fg_window = int(_rg_gc("risk.flash_guard_window_s", default=300))
        _fg_cooldown = int(_rg_gc("risk.flash_guard_cooldown_s", default=60))
        engine._flash_guard = FlashGuard(
            threshold_pct=_fg_threshold,
            window_seconds=_fg_window,
            cooldown_seconds=_fg_cooldown,
        )
        if engine._risk_guardian is not None:
            engine._risk_guardian.flash_guard = engine._flash_guard
        logger.info("FlashGuard initialized (threshold=%.1f%%, window=%ds, cooldown=%ds)", _fg_threshold, _fg_window, _fg_cooldown)
    except Exception as exc:
        logger.warning("FlashGuard init failed (non-fatal): %s", exc)

    # US-175: ExposureTracker
    try:
        from src.risk.exposure_tracker import ExposureTracker
        if engine._redis_client is not None:
            engine._exposure_tracker = ExposureTracker(engine._redis_client)
            logger.info("ExposureTracker initialized (Redis-backed)")
        else:
            engine._exposure_tracker = ExposureTracker(None)
            logger.warning("ExposureTracker: Redis unavailable, using in-memory fallback")
    except Exception as exc:
        logger.warning("ExposureTracker init failed (non-fatal): %s", exc)

# ------------------------------------------------------------------
# Step 7: Execution Engine
# ------------------------------------------------------------------


async def init_execution(engine: "Engine") -> None:
    from src.execution.executor import AtomicExecutor
    from src.execution.trade_consumer import TradeRequestConsumer

    # US-236: Initialize PositionManager (in-memory tracking)
    # WS-4 Step 3: wire DualWriter if db_pool + redis_client available (persistence)
    try:
        from src.risk.position_manager import PositionManager
        _dual_writer = None
        if getattr(engine, "_db_pool", None) and getattr(engine, "_redis_client", None):
            try:
                from src.infra.db.dual_write import DualWriter
                _dual_writer = DualWriter(
                    db_pool=engine._db_pool,
                    redis_client=engine._redis_client,
                )
                logger.info("DualWriter wired for PositionManager (persistence active)")
            except Exception as _dw_exc:
                logger.warning("DualWriter init failed (fallback to None): %s", _dw_exc)
        engine._position_manager = PositionManager(
            dual_writer=_dual_writer,
            redis_client=getattr(engine, "_redis_client", None),
        )
        _mode_desc = "with dual_writer" if _dual_writer else "in-memory only (dual_writer=None)"
        logger.info("PositionManager initialized (%s)", _mode_desc)
    except Exception as exc:
        logger.warning("PositionManager init failed (non-fatal): %s", exc)
    # WS-4 Step 1: async queue + drain task for ordered PositionManager writes
    # (replaces fire-and-forget asyncio.ensure_future - no ordering, no exception surfacing)
    engine._pm_queue: asyncio.Queue = asyncio.Queue(maxsize=1024)
    engine._pm_drain_task: asyncio.Task | None = None
    engine._pm_drain_errors = 0  # metric counter

    engine._executor = AtomicExecutor(
        exchanges=engine._exchanges,
    )

    # Build risk check function for TradeRequestConsumer
    risk_check = None
    if engine._risk_guardian is not None:
        risk_check = engine._build_risk_check_fn()

    engine._trade_consumer = TradeRequestConsumer(
        event_bus=engine._event_bus,
        executor=engine._executor,
        risk_check=risk_check,
        on_result=engine._on_execution_result,
    )
    logger.info("AtomicExecutor + TradeRequestConsumer initialized")

    # US-115: SlippageFeedbackLoop — tracks actual vs expected fills
    try:
        from src.risk.slippage import SlippageFeedbackLoop
        engine._slippage_feedback = SlippageFeedbackLoop(alpha=0.1, window=100)
        logger.info("SlippageFeedbackLoop initialized (alpha=0.1, window=100)")
    except Exception as exc:
        logger.warning("SlippageFeedbackLoop init failed (non-fatal): %s", exc)

    # US-114: DynamicSizer — wraps PositionSizer with confidence × regime × liquidity
    try:
        from src.execution.sizer import DynamicSizer, PositionSizer, SizerConfig
        capital = engine._settings.capital.initial_capital if engine._settings else Decimal("70")
        base_sizer = PositionSizer(SizerConfig(capital=capital, tier="alpha"))
        engine._dynamic_sizer = DynamicSizer(base_sizer=base_sizer)
        logger.info("DynamicSizer initialized (wrapping PositionSizer)")
    except Exception as exc:
        logger.warning("DynamicSizer init failed (non-fatal): %s", exc)

    # US-130: Wire DynamicSizer to SignalGenerator for regime-adaptive position sizing
    if engine._dynamic_sizer is not None and engine._signal_generator is not None:
        engine._signal_generator._dynamic_sizer = engine._dynamic_sizer
        logger.info("DynamicSizer wired to SignalGenerator")

    # US-116: TCAAnalyzer
    try:
        from src.analysis.tca import TCAAnalyzer
        engine._tca_analyzer = TCAAnalyzer(window_size=1000)
        logger.info("TCAAnalyzer initialized (window=1000)")
    except Exception as exc:
        logger.warning("TCAAnalyzer init failed (non-fatal): %s", exc)

    # US-120: InventoryRebalancer
    try:
        from src.core.inventory_rebalancer import InventoryRebalancer
        from src.core.balance_tracker import BalanceTracker
        engine._balance_tracker = BalanceTracker()
        _op = get_settings().operational
        engine._rebalancer = InventoryRebalancer(
            tracker=engine._balance_tracker,
            deviation_threshold=_op.rebalancer_deviation_threshold,
            check_interval_s=_op.rebalancer_check_interval_s,
            min_transfer_usd=_op.rebalancer_min_transfer_usd,
        )
        # Connect exchange balance feeds (US-QF: balance_feed NOT_CONNECTED 해소)
        if engine._exchanges:
            await engine._rebalancer.connect_exchange_feeds(engine._exchanges)
            logger.info(
                "InventoryRebalancer initialized (threshold=%.0f%%, interval=%.0fh, balance_feed=CONNECTED, exchanges=%d)",
                engine._rebalancer.deviation_threshold * 100,
                engine._rebalancer.check_interval_s / 3600,
                len(engine._exchanges),
            )
        else:
            logger.info(
                "InventoryRebalancer initialized (threshold=%.0f%%, interval=%.0fh, balance_feed=NOT_CONNECTED)",
                engine._rebalancer.deviation_threshold * 100,
                engine._rebalancer.check_interval_s / 3600,
            )
    except Exception as exc:
        logger.warning("InventoryRebalancer init failed (non-fatal): %s", exc)

    # US-250: PositionRecovery (WAL-based orphan detection on startup)
    try:
        from src.execution.position_recovery import PositionRecovery
        engine._position_recovery = PositionRecovery()
        logger.info("PositionRecovery initialized")
    except Exception as exc:
        logger.warning("PositionRecovery init failed (non-fatal): %s", exc)

    # ER2-22: RecoveryManager (WAL-based Redis state reconstruction on Redis restart)
    if engine._db_pool is not None and engine._redis_client is not None:
        try:
            from src.infra.db.recovery import RecoveryManager
            redis_raw = engine._redis_client.redis
            engine._recovery_manager = RecoveryManager(
                db_pool=engine._db_pool,
                redis_client=redis_raw,
                exchange_clients={ex_id: ex for ex_id, ex in engine._exchanges.items()},
            )
            logger.info("RecoveryManager initialized")
        except Exception as exc:
            logger.warning("RecoveryManager init failed (non-fatal): %s", exc)

    # US-250: PositionReconciler (60s periodic engine-vs-exchange check)
    try:
        from src.execution.reconciler import PositionReconciler

        async def _auto_close_orphan(exchange_id: str, pos) -> None:
            """Auto-close a position the engine has no record of."""
            adapter = engine._exchanges.get(exchange_id)
            if adapter is None:
                return
            try:
                from src.core.models import Order, OrderSide, OrderType
                import uuid as _uuid
                # close_side: if exchange has LONG (size>0) → SELL to close; SHORT → BUY
                close_side = OrderSide.SELL if pos.size > Decimal("0") else OrderSide.BUY
                close_order = Order(
                    order_id=str(_uuid.uuid4()),
                    symbol=pos.symbol,
                    exchange_id=exchange_id,
                    side=close_side,
                    order_type=OrderType.MARKET,
                    amount=abs(pos.size),
                    metadata={"reduceOnly": True, "leg_type": "reconciler_auto_close"},
                )
                await adapter.place_order(close_order)
                logger.critical(
                    "reconciler_auto_closed exchange=%s symbol=%s size=%s side=%s",
                    exchange_id, pos.symbol, pos.size, close_side,
                )
            except Exception as _exc:
                logger.error("reconciler_auto_close_failed exchange=%s symbol=%s error=%s",
                             exchange_id, pos.symbol, _exc)

        def _on_reconcile_discrepancy(result) -> None:
            # BUG-164: race guard — reconciler (60s cycle) can run between
            # order_placed and position_opened (fire-and-forget PM). Discrepancies
            # that appear ONCE are almost always race artifacts. Only escalate to
            # Telegram if the same orphan persists for 2+ cycles.
            orphans_now = {
                k for k, v in result.exchange_positions.items()
                if k not in result.engine_positions and abs(v.size) > Decimal("0.0001")
            }
            _prev = getattr(engine, "_prev_reconciler_orphans", set())
            persistent = orphans_now & _prev
            engine._prev_reconciler_orphans = orphans_now

            if persistent:
                # Persistent orphan → real issue, alert
                if engine._telegram:
                    summary = [s for s in result.discrepancies if any(k in s for k in persistent)][:3]
                    asyncio.ensure_future(engine._telegram.send_alert_kr(
                        "position_discrepancy",
                        {"count": len(persistent), "summary": str(summary)},
                    ))
                for key in persistent:
                    pos = result.exchange_positions.get(key)
                    if pos is not None:
                        logger.warning(
                            "reconciler_orphan_PERSISTENT key=%s size=%s "
                            "(auto_close disabled — manual cleanup required)",
                            key, pos.size,
                        )
            elif orphans_now:
                # Transient (race) — log at INFO, no Telegram (BUG-164)
                logger.info(
                    "reconciler_orphan_transient count=%d keys=%s "
                    "(will escalate next cycle if persistent)",
                    len(orphans_now), list(orphans_now)[:3],
                )

        engine._position_reconciler = PositionReconciler(
            exchanges=list(engine._exchanges.values()),
            on_discrepancy=_on_reconcile_discrepancy,
        )
        logger.info("PositionReconciler initialized (exchanges=%d)", len(engine._exchanges))
    except Exception as exc:
        logger.warning("PositionReconciler init failed (non-fatal): %s", exc)


def build_risk_check_fn(engine: "Engine"):
    """Create a risk check callable for the trade consumer (US-129: all 8 fields populated)."""
    from src.risk.guardian import PortfolioState, TradeProposal

    capital = engine._settings.capital.initial_capital if engine._settings else Decimal("70")

    def risk_check(trade_request) -> tuple[bool, str]:
        capital_total = capital * max(len(engine._exchanges), 1)

        # US-129: used_capital from tracked position sizes
        used_capital = sum(engine._position_sizes.values()) if engine._position_sizes else Decimal("0")

        # US-129: drawdown from peak equity tracking (CRITICAL FIX: init to capital_total)
        if engine._peak_equity is None:
            engine._peak_equity = capital_total
        current_equity = capital_total + engine._total_pnl
        current_drawdown_pct = max(
            Decimal("0"),
            (engine._peak_equity - current_equity) / engine._peak_equity,
        ) if engine._peak_equity > Decimal("0") else Decimal("0")

        # US-129: exchange health scores — default 1.0 (healthy)
        exchange_health = {
            eid: engine._exchange_health.get(eid, Decimal("1.0"))
            for eid in engine._exchanges.keys()
        }

        # Settlement exits and reduceOnly closes bypass risk checks (early return — skip PortfolioState)
        _is_close_req = any(
            isinstance(leg.metadata, dict) and (
                leg.metadata.get("reduceOnly") is True or
                str(leg.metadata.get("leg_type", "")).startswith("settlement_close")
            )
            for leg in trade_request.legs
        )
        if _is_close_req:
            return True, ""

        # Effective position map for Check #10 (max concurrent positions):
        # _position_sizes nets BUY/SELL for delta-neutral hedged positions to ~0,
        # so cross_exchange_positions fills the gap.  Sentinel value is Decimal("0")
        # (not "1") so Check #1 sees zero directional exposure — correct for hedges.
        _effective_positions = dict(engine._position_sizes)
        for _sym in engine._cross_exchange_positions:
            if _sym not in _effective_positions:
                _effective_positions[_sym] = Decimal("0")  # sentinel: key present, no directional exposure

        # Gross exposure = net directional + capital tied in cross-exchange hedges
        _total_exposure = used_capital + engine._cross_gross_exposure

        # US-175/Amendment 7: populate net_exposures from ExposureTracker snapshot.
        # snapshot() is synchronous and always reflects latest fills in this process.
        _net_exposures = (
            engine._exposure_tracker.snapshot()
            if engine._exposure_tracker is not None
            else {}
        )

        portfolio = PortfolioState(
            total_capital=capital_total,
            used_capital=used_capital,
            current_drawdown_pct=current_drawdown_pct,
            total_exposure=_total_exposure,
            position_sizes=_effective_positions,
            exchange_health_scores=exchange_health,
            volatility_1min={},   # populated when live vol data available
            volatility_24h={},    # populated when live vol data available
            net_exposures=_net_exposures,
        )

        # Check each leg
        for leg in trade_request.legs:
            price = leg.price or _btc_ref_price()
            proposal = TradeProposal(
                strategy_id=trade_request.strategy_id,
                exchange_id=leg.exchange_id,
                symbol=leg.symbol,
                side=leg.side.value.upper(),
                size=leg.size,
                price=price,
                position_value=price * leg.size,
            )
            result = engine._risk_guardian.check(proposal, portfolio)
            if not result.approved:
                return False, result.reason
        return True, ""

    return risk_check


def on_execution_result(engine: "Engine", trade_request, execution_result) -> None:
    """Callback after each trade execution.

    Phase 6 Step 3: When engine._listener_dispatcher is set, delegate to
    ExecutionResultDispatcher (14 listeners) and skip the legacy 360 LOC body.
    Falls back to legacy on dispatcher errors (resilience).
    """
    dispatcher = getattr(engine, "_listener_dispatcher", None)
    if dispatcher is not None:
        try:
            asyncio.ensure_future(dispatcher.dispatch(trade_request, execution_result))
            return
        except Exception as exc:
            logger.error(
                "dispatcher_failed_falling_back_to_legacy strategy=%s err=%s",
                trade_request.strategy_id,
                exc,
            )
            # Intentional fall-through to legacy path

    logger.info(
        "Execution result: strategy=%s status=%s",
        trade_request.strategy_id,
        execution_result.status.value,
    )
    # US-129: Update position tracking and peak equity for RiskGuardian PortfolioState
    if getattr(execution_result.status, "value", str(execution_result.status)) == "success":
        try:
            legs_info = [
                (getattr(leg, "trade", None), getattr(leg, "order", None))
                for leg in getattr(execution_result, "legs", [])
            ]
            for trade, order in legs_info:
                if trade is not None and order is not None:
                    symbol = order.symbol
                    pos_value = trade.price * trade.amount
                    side = getattr(order.side, "value", str(order.side)).upper()
                    if side == "BUY":
                        engine._position_sizes[symbol] = (
                            engine._position_sizes.get(symbol, Decimal("0")) + pos_value
                        )
                    else:
                        current = engine._position_sizes.get(symbol, Decimal("0"))
                        updated = max(Decimal("0"), current - pos_value)
                        if updated == Decimal("0"):
                            engine._position_sizes.pop(symbol, None)
                        else:
                            engine._position_sizes[symbol] = updated
            # WS-3.1+3.2: Wire PositionManager open/close from trade fills
            # _on_execution_result is sync → fire-and-forget via ensure_future
            if engine._position_manager is not None:
                _is_close_exec = any(
                    isinstance(getattr(o, "metadata", None), dict) and (
                        o.metadata.get("reduceOnly") is True or
                        str(o.metadata.get("leg_type", "")).startswith(("settlement_close", "timeout_close"))
                    )
                    for _, o in legs_info if o
                )
                for trade, order in legs_info:
                    if trade is not None and order is not None:
                        _side_str = getattr(order.side, "value", str(order.side)).upper()
                        # WS-4 Step 1: enqueue ordered ops via drain task (예외 surfaced, order 보장)
                        if _is_close_exec:
                            _op_kwargs = ("close_position", {
                                "strategy_id": trade_request.strategy_id,
                                "exchange_id": order.exchange_id,
                                "symbol": order.symbol,
                                "close_price": trade.price,
                            })
                        else:
                            _op_kwargs = ("open_position", {
                                "strategy_id": trade_request.strategy_id,
                                "exchange_id": order.exchange_id,
                                "symbol": order.symbol,
                                "side": "LONG" if _side_str == "BUY" else "SHORT",
                                "quantity": trade.amount,
                                "entry_price": trade.price,
                            })
                        # WS-4 Step 2: 동기 인메모리 인덱스 먼저 업데이트
                        # reconciler가 같은 tick에 최신 상태 볼 수 있도록
                        try:
                            engine._position_manager.update_index_sync(_op_kwargs[0], **_op_kwargs[1])
                        except Exception as _sync_err:
                            logger.debug("update_index_sync_failed: %s", _sync_err)
                        # 그 후 async 큐에 dispatch (WAL/Redis 쓰기)
                        try:
                            engine._pm_queue.put_nowait(_op_kwargs)
                        except asyncio.QueueFull:
                            # Safety net: fall back to fire-and-forget if queue saturated
                            logger.warning("pm_queue_full — falling back to ensure_future op=%s sym=%s",
                                           _op_kwargs[0], _op_kwargs[1].get("symbol"))
                            _op_name, _op_args = _op_kwargs
                            asyncio.ensure_future(getattr(engine._position_manager, _op_name)(**_op_args))

            # Track cross-exchange hedged positions (funding_rate, spot_futures)
            # _position_sizes nets BUY/SELL to ~0 for hedged positions, so we track separately
            buy_exchanges = {order.exchange_id for _, order in legs_info if order and getattr(order.side, "value", str(order.side)).upper() == "BUY"}
            sell_exchanges = {order.exchange_id for _, order in legs_info if order and getattr(order.side, "value", str(order.side)).upper() == "SELL"}
            symbols_in_exec = {order.symbol for _, order in legs_info if order}
            _is_cross = bool(buy_exchanges and sell_exchanges and buy_exchanges != sell_exchanges)
            _is_close = any(
                isinstance(getattr(order, "metadata", None), dict) and (
                    order.metadata.get("reduceOnly") is True or
                    str(order.metadata.get("leg_type", "")).startswith("settlement_close")
                )
                for _, order in legs_info if order
            )
            for sym in symbols_in_exec:
                if _is_cross and not _is_close:
                    engine._cross_exchange_positions.add(sym)
                elif _is_close or not _is_cross:
                    engine._cross_exchange_positions.discard(sym)

            # Track gross capital in delta-neutral hedges for Check #3 (total exposure).
            # Both legs of a cross-exchange trade consume margin even though net = 0.
            if _is_cross:
                _leg_gross = sum(
                    trade.price * trade.amount
                    for trade, order in legs_info
                    if trade is not None and order is not None
                )
                if _is_close:
                    engine._cross_gross_exposure = max(
                        Decimal("0"), engine._cross_gross_exposure - _leg_gross
                    )
                else:
                    engine._cross_gross_exposure += _leg_gross
            # Update peak equity
            capital = engine._settings.capital.initial_capital if engine._settings else Decimal("70")
            capital_total = capital * max(len(engine._exchanges), 1)
            # Compute actual PnL from fills (HIGH FIX: don't use expected_profit)
            pnl_raw = getattr(execution_result, "pnl", None)
            if pnl_raw is None:
                # Estimate from fill prices: sell proceeds - buy costs
                pnl_estimate = Decimal("0")
                for leg in getattr(execution_result, "legs", []):
                    t = getattr(leg, "trade", None)
                    o = getattr(leg, "order", None)
                    if t and o:
                        val = t.price * t.amount
                        s = getattr(o.side, "value", str(o.side)).upper()
                        pnl_estimate += val if s == "SELL" else -val
                pnl_raw = pnl_estimate
            engine._total_pnl += Decimal(str(pnl_raw))
            current_equity = capital_total + engine._total_pnl
            if engine._peak_equity is not None and current_equity > engine._peak_equity:
                engine._peak_equity = current_equity
        except Exception as exc:
            logger.error("position_tracking_failed strategy=%s error=%s", trade_request.strategy_id, exc)
            engine._position_tracking_errors = getattr(engine, "_position_tracking_errors", 0) + 1
            if engine._position_tracking_errors > 5 and engine._telegram:
                asyncio.ensure_future(engine._telegram.send_alert_kr(
                    "position_tracking_fail",
                    {"error_count": engine._position_tracking_errors},
                ))
    # Record execution to TimescaleDB via market_recorder (DB recording gap fix)
    if (getattr(execution_result.status, "value", str(execution_result.status)) == "success"
            and engine._market_recorder is not None and trade_request.legs):
        try:
            from src.core.models import OrderSide as _OS
            _buy_legs = [l for l in trade_request.legs if l.side == _OS.BUY]
            _sell_legs = [l for l in trade_request.legs if l.side == _OS.SELL]
            if _buy_legs and _sell_legs:
                _bp = _buy_legs[0].price or Decimal("0")
                _sp = _sell_legs[0].price or Decimal("0")
                # Prefer actual fill prices from execution_result
                for _lr in getattr(execution_result, "legs", []):
                    _t = getattr(_lr, "trade", None)
                    _o = getattr(_lr, "order", None)
                    if _t and _o:
                        _s = getattr(_o.side, "value", str(_o.side)).upper()
                        if _s == "BUY":
                            _bp = Decimal(str(_t.price))
                        else:
                            _sp = Decimal(str(_t.price))
                _mode = "live" if hasattr(engine, "_execution_mode") else "live"
                if hasattr(engine, "_live_mode") and engine._live_mode is not None:
                    _mode = getattr(engine._live_mode, "_execution_mode", "live")
                engine._market_recorder.record_execution(
                    strategy_id=trade_request.strategy_id,
                    buy_exchange=str(_buy_legs[0].exchange_id),
                    sell_exchange=str(_sell_legs[0].exchange_id),
                    symbol=trade_request.legs[0].symbol,
                    buy_price=_bp,
                    sell_price=_sp,
                    size=trade_request.legs[0].size,
                    net_pnl=Decimal(str(getattr(execution_result, "pnl", 0) or 0)),
                    status="filled",
                    mode=_mode,
                )
        except Exception as _rec_exc:
            logger.debug("db_record_execution_failed strategy=%s err=%s", trade_request.strategy_id, _rec_exc)

    # US-175: Update ExposureTracker on successful fills
    if (getattr(execution_result.status, "value", str(execution_result.status)) == "success"
            and engine._exposure_tracker is not None):
        try:
            for leg in getattr(execution_result, "legs", []):
                order = getattr(leg, "order", None)
                trade = getattr(leg, "trade", None)
                if order is not None and trade is not None and "/" in getattr(order, "symbol", ""):
                    base_asset = order.symbol.split("/")[0]
                    side = getattr(order.side, "value", str(order.side)).upper()
                    delta = trade.amount if side == "BUY" else -trade.amount
                    _ex_id = (order.exchange_id if hasattr(order, "exchange_id")
                              else getattr(leg, "exchange_id", "unknown"))
                    _task = asyncio.create_task(
                        engine._exposure_tracker.update_exposure(_ex_id, base_asset, Decimal(str(delta)))
                    )
                    # Log but don't propagate task exceptions (non-critical tracking)
                    def _on_exp_done(t: asyncio.Task, _ex=_ex_id, _ba=base_asset) -> None:
                        if not t.cancelled() and t.exception() is not None:
                            logger.warning(
                                "exposure_tracker.update_failed ex=%s asset=%s err=%s",
                                _ex, _ba, t.exception(),
                            )
                    _task.add_done_callback(_on_exp_done)
        except Exception as _exp_exc:
            logger.debug("exposure_tracking.loop_error %s", _exp_exc)  # Non-critical

    # US-115: Feed slippage data to feedback loop
    if engine._slippage_feedback is not None and hasattr(execution_result, 'legs'):
        try:
            for leg in execution_result.legs:
                if hasattr(leg, 'expected_price') and hasattr(leg, 'fill_price'):
                    engine._slippage_feedback.record_fill(
                        expected_price=leg.expected_price,
                        actual_price=leg.fill_price,
                        side=leg.order.side.value.upper() if leg.order and hasattr(leg.order, 'side') else "BUY",
                    )
        except Exception:
            pass  # Non-critical: feedback tracking failure
    # US-118: Feed trade PnL to correlation monitor
    if engine._correlation_monitor is not None:
        try:
            pnl = float(execution_result.pnl) if hasattr(execution_result, "pnl") else float(trade_request.expected_profit_usdt)
            engine._correlation_monitor.record_trade_pnl(trade_request.strategy_id, pnl)
        except Exception:
            pass  # Non-critical: correlation tracking failure
    # US-116: Feed TCA data
    if engine._tca_analyzer is not None:
        try:
            legs = getattr(execution_result, 'legs', [])
            for idx, leg in enumerate(legs):
                trade = getattr(leg, 'trade', None)
                if trade is not None:
                    # Latency: use execution_result duration if available, else 0
                    latency_ms = float(
                        getattr(execution_result, 'execution_duration_ms', 0)
                        or getattr(execution_result, 'duration_ms', 0)
                        or 0
                    )
                    # Expected price: use trade_request leg price (always populated)
                    expected = 0.0
                    if idx < len(trade_request.legs):
                        expected = float(trade_request.legs[idx].price or 0)
                    if expected <= 0:
                        expected = float(getattr(getattr(leg, 'order', None), 'price', 0) or 0)
                    if expected <= 0:
                        logger.debug("TCA: skipping leg %d — no expected price", idx)
                        continue
                    # US-329: pass signal_ts for timing decomposition
                    try:
                        _signal_ts = trade_request.timestamp.timestamp()
                    except (AttributeError, TypeError):
                        _signal_ts = 0.0
                    engine._tca_analyzer.record_execution(
                        expected_price=expected,
                        fill_price=float(trade.price),
                        latency_ms=latency_ms,
                        filled_ratio=float(getattr(leg, 'filled_ratio', 1.0)),
                        strategy_id=trade_request.strategy_id,
                        signal_ts=_signal_ts,
                        fill_ts=time.time(),
                    )
        except Exception:
            pass  # Non-critical: TCA tracking failure
    # Record trade in context for dashboard API
    from datetime import datetime, timezone
    from uuid import uuid4
    try:
        engine.context.trade_history.append({
            "id": str(uuid4()),
            "strategy_id": trade_request.strategy_id,
            "symbol": trade_request.legs[0].symbol if trade_request.legs else "UNKNOWN",
            "buy_exchange": next((l.exchange_id for l in trade_request.legs if l.side.value == "buy"), ""),
            "sell_exchange": next((l.exchange_id for l in trade_request.legs if l.side.value == "sell"), ""),
            "side": "arbitrage",
            "size": float(trade_request.legs[0].size) if trade_request.legs else 0,
            "entry_price": float(trade_request.legs[0].price or 0) if trade_request.legs else 0,
            "exit_price": float(trade_request.legs[-1].price or 0) if trade_request.legs else 0,
            "pnl": float(execution_result.pnl) if hasattr(execution_result, "pnl") else float(trade_request.expected_profit_usdt),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": execution_result.status.value,
        })
    except Exception as exc:
        logger.debug("Failed to record trade to context: %s", exc)

    # US-DW1: CircuitBreaker feedback — record win/loss after each execution
    if engine._circuit_breaker is not None:
        try:
            status_val = getattr(execution_result.status, "value", str(execution_result.status))
            if status_val == "success":
                # Compute drawdown for loss detection
                pnl_val = getattr(execution_result, "pnl", None)
                if pnl_val is not None and float(pnl_val) < 0:
                    # Loss: compute current drawdown pct
                    capital = engine._settings.capital.initial_capital if engine._settings else Decimal("70")
                    capital_total = capital * max(len(engine._exchanges), 1)
                    dd_pct = float(abs(engine._total_pnl) / capital_total) if capital_total > 0 and engine._total_pnl < 0 else 0.0
                    asyncio.ensure_future(engine._circuit_breaker.record_loss(drawdown_pct=dd_pct))
                else:
                    asyncio.ensure_future(engine._circuit_breaker.record_win())
            elif status_val in ("rolled_back", "rollback_failed", "timeout"):
                # Real execution attempt that failed — count as loss
                asyncio.ensure_future(engine._circuit_breaker.record_loss())
            # else: "rejected" = infrastructure reject (no adapter, halted, health)
            # — do NOT count as consecutive_loss; it was never a trade attempt
        except Exception:
            pass  # Non-critical: CB feedback failure

    # BUG-J: ROLLED_BACK 완료 시 strategy._open_positions 해제 → 4H 심볼 차단 방지
    # BUG-31: REJECTED도 해제 (주문 미발생 → position 없음)
    # ROLLBACK_FAILED는 stranded position 존재 → 해제 안 함
    if getattr(execution_result.status, "value", str(execution_result.status)) in ("rolled_back", "rejected"):
        try:
            strategy = engine._strategy_manager.get_strategy(trade_request.strategy_id)
            if strategy is not None:
                symbol = trade_request.legs[0].symbol if trade_request.legs else None
                if symbol:
                    # BUG-95 CRITICAL: distinguish entry vs exit rollback semantics
                    _is_exit_tc = any(
                        isinstance(getattr(leg, "metadata", None), dict) and (
                            leg.metadata.get("reduceOnly") is True or
                            str(leg.metadata.get("leg_type", "")).startswith(("settlement_close", "timeout_close", "spread_exit"))
                        )
                        for leg in trade_request.legs
                    )
                    if _is_exit_tc:
                        strategy.handle_exit_rollback(symbol)
                    else:
                        strategy.handle_entry_rollback(symbol)
        except Exception:
            pass  # Non-critical: position clear failure

        # WS-3.3: Fix _position_sizes rollback leak — reverse any exposure added
        # by optimistic on_signal before execution was attempted.
        try:
            for leg in trade_request.legs:
                if leg.symbol and leg.symbol in engine._position_sizes:
                    _val = (leg.price or Decimal("0")) * (leg.size or Decimal("0"))
                    if _val > 0:
                        current = engine._position_sizes.get(leg.symbol, Decimal("0"))
                        updated = max(Decimal("0"), current - _val)
                        if updated == Decimal("0"):
                            engine._position_sizes.pop(leg.symbol, None)
                        else:
                            engine._position_sizes[leg.symbol] = updated
        except Exception:
            pass  # Non-critical

    # US-DW8: Send Korean fill notification via Telegram
    if engine._trade_bot is not None and getattr(execution_result.status, "value", str(execution_result.status)) == "success":
        try:
            fill_data = {
                "strategy_id": trade_request.strategy_id,
                "symbol": trade_request.legs[0].symbol if trade_request.legs else "UNKNOWN",
                "buy_exchange": next((l.exchange_id for l in trade_request.legs if l.side.value == "buy"), ""),
                "sell_exchange": next((l.exchange_id for l in trade_request.legs if l.side.value == "sell"), ""),
                "size": float(trade_request.legs[0].size) if trade_request.legs else 0,
                "pnl": float(execution_result.pnl) if hasattr(execution_result, "pnl") else float(trade_request.expected_profit_usdt),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            asyncio.ensure_future(engine._trade_bot.send_fill_kr(fill_data))
        except Exception:
            pass  # Non-critical: Telegram fill notification failure


