"""NoOpAlertAdapter — AlertPort 무동작 구현 (2026-04-27).

목적:
- Test fixture (Telegram 의존성 없이 listener 테스트 가능)
- Bootstrap fallback (Telegram 미설정 환경, paper 모드 default)
"""
from __future__ import annotations

from typing import Any


class NoOpAlertAdapter:
    """AlertPort no-op impl — 모든 send 호출이 silent True."""

    async def send_alert_kr(self, alert_type: str, data: dict[str, Any]) -> bool:
        return True

    async def send_fill_kr(self, data: dict[str, Any]) -> bool:
        return True
