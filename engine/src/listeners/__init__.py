"""LEVIATHAN Execution Result Listeners — Phase 5.2.4 (2026-04-26).

on_execution_result 360 LOC god-function 분리. 14 single-responsibility listeners.

각 Listener는 ``src.ports.listener_port.ExecutionResultListener`` Protocol 구현.

마이그레이션 순서 (LOW → HIGH risk):
1. LogListener (trivial)
2. MarketRecorderListener
3. ExposureListener
4. TelegramListener
5. SlippageListener / TCAListener / CorrelationListener
6. PositionSizeLeakListener / CrossHedgeListener / PnLPeakListener (idempotency 추가 필요)
7. PositionManagerListener / TradeHistoryListener / CircuitBreakerListener / RollbackListener
"""
from __future__ import annotations

from src.listeners.correlation_listener import CorrelationListener
from src.listeners.exposure_listener import ExposureListener
from src.listeners.log_listener import LogListener
from src.listeners.market_recorder_listener import MarketRecorderListener
from src.listeners.slippage_listener import SlippageListener
from src.listeners.tca_listener import TCAListener

__all__ = [
    "CorrelationListener",
    "ExposureListener",
    "LogListener",
    "MarketRecorderListener",
    "SlippageListener",
    "TCAListener",
]
