"""LedgerPort — Phase 5.1.6 (2026-04-26).

PnL ledger 추상화. PnLLedger (single-source-of-truth realized PnL) 통합 진입점.

산업 표준 비교:
- Nautilus PortfolioStore + AccountStore
- LEAN ITransactionHandler.GetCashBook
- Hummingbot PerformanceMetrics

LEVIATHAN 책임:
- record_pnl: realized PnL 기록 (strategy + exchange)
- get_total: 누적 total PnL
- get_per_strategy: 전략별 분해
- snapshot: 현재 상태 immutable snapshot
"""
from __future__ import annotations

from decimal import Decimal
from typing import Protocol, runtime_checkable


@runtime_checkable
class LedgerPort(Protocol):
    """Hexagonal port for PnL ledger."""

    def record_pnl(
        self,
        strategy_id: str,
        exchange_id: str,
        pnl_usd: Decimal,
        commission_usd: Decimal = Decimal("0"),
    ) -> None:
        """realized PnL 기록. Phase 2B+ paper journal과 함께 single-source-of-truth.

        net = pnl_usd - commission_usd.
        per-strategy + exchange 분리 누적.
        """
        ...

    def get_total(self) -> Decimal:
        """누적 total realized PnL (USD)."""
        ...

    def get_per_strategy(self) -> dict[str, Decimal]:
        """전략별 누적 PnL (strategy_id → USD)."""
        ...

    def get_per_exchange(self) -> dict[str, Decimal]:
        """거래소별 누적 PnL (exchange_id → USD)."""
        ...
