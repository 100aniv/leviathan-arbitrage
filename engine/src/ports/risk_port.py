"""RiskPort — Phase 5.1.3 (2026-04-26).

Risk pre-trade check + 결과 기록 추상화.
RiskGuardian (11-check) / PreTradeValidator (typed gates) 통합 진입점.

산업 표준 비교:
- Nautilus RiskEngine (pre-trade check + position sizing)
- LEAN ISecurityRiskModel + IRiskManagementModel
- Hummingbot RiskAssessor

LEVIATHAN 책임:
- check_proposal: pre-trade gate (11 checks)
- record_loss / record_win: post-trade circuit breaker feedback
- is_halted: kill-switch 통합 query
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RiskPort(Protocol):
    """Hexagonal port for risk management."""

    async def check_proposal(
        self,
        trade_request: Any,
        strategy_id: str,
    ) -> Any:
        """pre-trade 11-check + reason_code 반환.

        Returns ValidationResult(approved: bool, reason_code, detail).
        """
        ...

    def record_loss(self, strategy_id: str, pnl_usd: float) -> None:
        """손실 기록 → CircuitBreaker consecutive_loss_limit 트리거."""
        ...

    def record_win(self, strategy_id: str, pnl_usd: float) -> None:
        """수익 기록 → CircuitBreaker reset half-open count."""
        ...

    @property
    def is_halted(self) -> bool:
        """kill switch + circuit breaker OPEN + flash guard 통합 halt 상태."""
        ...
