"""ExchangeIncomeFetcherPort — Phase 7 income polling abstraction (2026-04-26).

Codex SUGGEST (codex-final-review-2026-04-26): exchange-income polling을 Port로 분리.
runtime/modes/live는 직접 ExchangeIncomeFetcher 의존 대신 이 Port를 받아 사용.

구현체:
- engine/src/infra/exchange/exchange_income_fetcher.py.ExchangeIncomeFetcher
- 향후 NoOpIncomeFetcher (test, paper-only mode)
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ExchangeIncomeFetcherPort(Protocol):
    """Hexagonal port for exchange income polling (Binance income / Bitget bills).

    PnL 정합 검증 + reconciliation 입력. live 모드 진입 시 시작/종료 lifecycle 관리.
    """

    async def start(self) -> None:
        """Background poll task 시작."""
        ...

    async def stop(self) -> None:
        """Background poll task graceful 종료."""
        ...
