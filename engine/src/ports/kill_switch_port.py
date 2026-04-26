"""KillSwitchPort — Phase 5.1.7 (2026-04-26, last port).

Kill switch (3-tier halt) 추상화. KillSwitch (engine/src/risk/kill_switch.py) 통합.

산업 표준 비교:
- Nautilus EmergencyStop
- LEAN AlgorithmManager.SetStatus(QUITTING)
- Hummingbot KillSwitch

LEVIATHAN 책임:
- halt(reason): 3-tier 트리거 (block + cancel + close)
- clear(): 운영자 수동 reset
- is_active: 현재 halt 상태 query
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class KillSwitchPort(Protocol):
    """Hexagonal port for kill switch (3-tier graceful halt)."""

    async def halt(self, reason: str) -> None:
        """3-tier halt 트리거.
        Tier 1 (<1ms): halt 플래그 설정 → 신규 주문 차단
        Tier 2 (<500ms): 미체결 주문 취소 (asyncio.gather 2s timeout)
        Tier 3 (<2s): 오픈 포지션 시장가 청산 (asyncio.gather 3s timeout)
        """
        ...

    def clear(self) -> None:
        """운영자 수동 reset (Telegram /resume 명령 등). Prometheus metric 동기 reset.
        BUG fix 45a5ba4: clear()도 KILL_SWITCH_ACTIVE.set(0) 필수.
        """
        ...

    @property
    def is_active(self) -> bool:
        """현재 halt 상태 (true ⇒ 모든 신규 주문 거부)."""
        ...
