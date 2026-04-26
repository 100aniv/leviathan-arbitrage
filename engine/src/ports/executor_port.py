"""ExecutorPort — Phase 5.1.2 (2026-04-26).

TradeRequest 실행 추상화. AtomicExecutor (live) / PaperExecutor (paper) 모두
이 Protocol을 구현. ExecutionResult 콜백을 통해 PnL/Position 등 14 listener에 전파.

산업 표준 비교:
- Nautilus ExecutionEngine + ExecClient
- LEAN ITransactionHandler (algorithm → broker pipeline)
- Hummingbot OrderExecutor (Strategy V2)

LEVIATHAN 책임:
- execute_trade_request: TradeRequest → ExecutionResult
- subscribe_result_listener: on_execution_result 14-listener 등록
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol, runtime_checkable


@runtime_checkable
class ExecutorPort(Protocol):
    """Hexagonal port for trade executors (paper/atomic/router-wrapped)."""

    async def execute_trade_request(self, trade_request: Any) -> Any:
        """TradeRequest 실행 → ExecutionResult 반환.

        - paper: PaperExecutor 시뮬 fill (PaperExchangeAdapter 경유)
        - live:  AtomicExecutor IOC + 다리 동시 실행 (NativeAdapter 경유)

        Failure modes:
        - OrderRejectedError → result.success=False, error_code 명시
        - Timeout → STRANDED 상태 (StrandedPositionTracker)
        - Both legs fail → rollback 자동 실행
        """
        ...

    def add_listener(
        self,
        listener: Callable[[Any, Any], Awaitable[None]] | Callable[[Any, Any], None],
    ) -> None:
        """ExecutionResult callback 등록 (Phase 5.2.4 14-listener 마이그레이션).

        listener signature: (trade_request, execution_result) -> None | Awaitable[None]
        """
        ...

    def remove_listener(
        self,
        listener: Callable[[Any, Any], Awaitable[None]] | Callable[[Any, Any], None],
    ) -> None:
        """등록 해제."""
        ...
