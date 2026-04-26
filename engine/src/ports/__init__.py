"""LEVIATHAN Ports — Hexagonal Architecture (2026-04-26).

Industry pattern (Nautilus ExecClient / LEAN IBrokerage / Hummingbot ConnectorBase):
runtime의 god-object 결합 해체를 위한 Port (Protocol) 인터페이스 계약.

구현체는 별도 adapter — Port를 통해서만 통신 (PEP 544 typing.Protocol).

Phase 5.1 Ports (7개):
1. ExchangeAdapterPort  — 거래소 (place_order/cancel/balance/supports_symbol)
2. ExecutorPort         — TradeRequest 실행 + listener 등록
3. RiskPort             — pre-trade check + circuit breaker feedback
4. DataFeedPort         — orderbook/trade WS 구독
5. JournalPort          — append-only event log (ExecutionJournal)
6. LedgerPort           — PnL ledger (record_pnl/get_total/per-strategy)
7. KillSwitchPort       — 3-tier halt + clear

Phase 7 추가 Ports (2026-04-26, Gemini Priority 2):
8. EventBusPort         — pub/sub message bus (Nautilus MessageBus 미러)
9. MetricsPort          — telemetry (LEAN/OpenTelemetry 미러)

Phase 5.2 god-object 해체 시 runtime/* 모듈이 Engine 대신 각 Port 의존.
"""
from __future__ import annotations

from src.ports.data_feed_port import DataFeedPort
from src.ports.event_bus_port import EventBusPort
from src.ports.exchange_adapter_port import ExchangeAdapterPort
from src.ports.executor_port import ExecutorPort
from src.ports.journal_port import JournalPort
from src.ports.kill_switch_port import KillSwitchPort
from src.ports.ledger_port import LedgerPort
from src.ports.metrics_port import MetricsPort
from src.ports.risk_port import RiskPort

__all__ = [
    "DataFeedPort",
    "EventBusPort",
    "ExchangeAdapterPort",
    "ExecutorPort",
    "JournalPort",
    "KillSwitchPort",
    "LedgerPort",
    "MetricsPort",
    "RiskPort",
]
