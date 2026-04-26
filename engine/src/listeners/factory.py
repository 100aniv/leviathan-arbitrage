"""ListenerFactory — Phase 5.2.6 (2026-04-26).

Engine 인스턴스 → 14 ExecutionResultListener + Dispatcher 빌드.

Phase 5.2.6 도입 단계: factory 작성만, 기존 risk_execution.on_execution_result는 보존
(paper canary 가동 중 위험 큼). Phase 6+에서 risk_execution → dispatcher 위임.

산업 표준 (Nautilus EventBus / Hummingbot OrderTracker observer):
- 의존성 주입은 application boot 시점에 1회
- listener 등록은 mode 결정 후 (paper/live별 다른 listener set 가능)
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from src.listeners.circuit_breaker_listener import CircuitBreakerListener
from src.listeners.correlation_listener import CorrelationListener
from src.listeners.cross_hedge_listener import CrossHedgeListener
from src.listeners.dispatcher import ExecutionResultDispatcher
from src.listeners.exposure_listener import ExposureListener
from src.listeners.log_listener import LogListener
from src.listeners.market_recorder_listener import MarketRecorderListener
from src.listeners.pnl_peak_listener import PnLPeakListener
from src.listeners.position_manager_listener import PositionManagerListener
from src.listeners.position_size_leak_listener import PositionSizeLeakListener
from src.listeners.rollback_listener import RollbackListener
from src.listeners.slippage_listener import SlippageListener
from src.listeners.tca_listener import TCAListener
from src.listeners.telegram_listener import TelegramListener
from src.listeners.trade_history_listener import TradeHistoryListener

logger = logging.getLogger(__name__)


def build_dispatcher_from_engine(engine: Any) -> ExecutionResultDispatcher:
    """Phase 5.2.6 dispatcher factory.

    Engine god-object → 14 listeners + dispatcher. 등록 순서는 risk_execution.py:519-877
    원본 실행 순서 보존:
    1. LogListener
    2. PositionSizeLeakListener (HIGH risk, NOT idempotent)
    3. PositionManagerListener
    4. CrossHedgeListener (HIGH risk)
    5. PnLPeakListener (HIGH risk)
    6. MarketRecorderListener
    7. ExposureListener
    8. SlippageListener
    9. CorrelationListener
    10. TCAListener
    11. TradeHistoryListener
    12. CircuitBreakerListener
    13. RollbackListener
    14. TelegramListener
    """
    dispatcher = ExecutionResultDispatcher()

    # Capital total supplier: 자동 갱신 (paper canary symbol 변경 가능)
    def _capital_total() -> Decimal:
        capital = (engine._settings.capital.initial_capital
                   if engine._settings else Decimal("70"))
        return capital * max(len(engine._exchanges), 1)

    def _total_pnl_supplier() -> Decimal:
        # Phase 5.2.2 EngineState 우선, fallback to legacy attr
        state = getattr(engine, "_state", None)
        if state is not None:
            return state.total_pnl
        return getattr(engine, "_total_pnl", Decimal("0"))

    # 1. Log
    dispatcher.register(LogListener())

    # 2. Position size leak (BUY/SELL net)
    dispatcher.register(PositionSizeLeakListener(engine._position_sizes))

    # 3. PositionManager (sync index + async queue)
    dispatcher.register(PositionManagerListener(
        engine._position_manager,
        getattr(engine, "_pm_queue", None),
    ))

    # 4. Cross-hedge tracking (delta-neutral)
    cross_gross_holder = [engine._cross_gross_exposure]
    dispatcher.register(CrossHedgeListener(
        engine._cross_exchange_positions,
        cross_gross_holder,
    ))
    # Note: cross_gross_holder[0] mutation 후 engine._cross_gross_exposure sync 필요.
    # Phase 6+: EngineState 통일.

    # 5. PnL + peak equity
    pnl_state = getattr(engine, "_state", None)
    if pnl_state is not None:
        dispatcher.register(PnLPeakListener(
            state=pnl_state,
            capital_total_supplier=_capital_total,
        ))

    # 6. MarketRecorder TimescaleDB
    dispatcher.register(MarketRecorderListener(
        engine._market_recorder,
        live_mode=getattr(engine, "_live_mode", None),
    ))

    # 7. Exposure tracker async update
    dispatcher.register(ExposureListener(engine._exposure_tracker))

    # 8. Slippage feedback
    dispatcher.register(SlippageListener(engine._slippage_feedback))

    # 9. Correlation monitor
    dispatcher.register(CorrelationListener(engine._correlation_monitor))

    # 10. TCA analyzer
    dispatcher.register(TCAListener(engine._tca_analyzer))

    # 11. Trade history (dashboard)
    dispatcher.register(TradeHistoryListener(engine.context))

    # 12. Circuit breaker feedback
    dispatcher.register(CircuitBreakerListener(
        engine._circuit_breaker,
        capital_total_supplier=_capital_total,
        total_pnl_supplier=_total_pnl_supplier,
    ))

    # 13. Rollback (strategy release + position_sizes leak fix)
    dispatcher.register(RollbackListener(
        engine._strategy_manager,
        engine._position_sizes,
    ))

    # 14. Telegram fill notification
    dispatcher.register(TelegramListener(engine._trade_bot))

    logger.info(
        "ListenerFactory.built %d listeners: %s",
        dispatcher.listener_count, dispatcher.listener_names,
    )
    return dispatcher
