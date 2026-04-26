"""LEVIATHAN Ports — Phase 5.1 Hexagonal Architecture (2026-04-26).

Industry pattern (Nautilus ExecClient / LEAN IBrokerage / Hummingbot ConnectorBase):
runtime의 god-object 결합 해체를 위한 Port (Protocol) 인터페이스 계약.

구현체는 별도 adapter (`src/infra/exchange/`, `src/execution/`, `src/risk/`).
Port를 통해서만 통신 — duck typing + 정적 타입 검증 (PEP 544 typing.Protocol).

Ports 정의 순서 (Phase 5.1):
1. ExchangeAdapterPort  ← 1st (LOW risk pilot, 33 god-object accesses)
2. ExecutorPort
3. RiskPort
4. DataFeedPort
5. JournalPort
6. LedgerPort
7. KillSwitchPort
"""
from __future__ import annotations

from src.ports.exchange_adapter_port import ExchangeAdapterPort

__all__ = ["ExchangeAdapterPort"]
