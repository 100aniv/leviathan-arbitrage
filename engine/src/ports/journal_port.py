"""JournalPort — Phase 5.1.5 (2026-04-26).

ExecutionJournal 추상화. SQLite-WAL + hash-chain 보장 (append-only audit log).

산업 표준 비교:
- Nautilus EventStore (event sourcing)
- LEAN IResultHandler (algorithm output journal)
- Hummingbot OrderTracker history

LEVIATHAN 책임:
- start: SQLite WAL 초기화 + last_hash 로드
- append: 이벤트 + hash-chain 추가
- replay: 이전 세션 이벤트 재생 (recovery)
- flush: 버퍼 강제 flush
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Protocol, runtime_checkable


@runtime_checkable
class JournalPort(Protocol):
    """Hexagonal port for execution journal."""

    async def start(self) -> None:
        """WAL 모드 초기화 + last_hash 로드 (idempotent)."""
        ...

    async def append(
        self,
        order_id: str,
        state: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """이벤트 append. state ∈ {SENT, ACKED, FILLED, CANCELLED, REJECTED, ROLLED_BACK, STRANDED}.

        payload: 선택적 메타데이터 (gas, fee, slippage, latency_ms 등).
        hash-chain: prev_hash + sha256(record) → cur_hash.
        """
        ...

    async def replay(self, since_ts: float | None = None) -> AsyncIterator[Any]:
        """이전 이벤트 재생. since_ts: epoch float (None = 처음부터).

        recovery 시나리오: crash 재시작 + position 재구성.
        """
        ...

    async def flush(self) -> None:
        """버퍼 강제 flush (graceful shutdown 전)."""
        ...
