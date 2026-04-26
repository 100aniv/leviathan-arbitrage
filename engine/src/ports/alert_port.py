"""AlertPort — Phase 7 alert/notification abstraction (2026-04-26).

Codex SUGGEST (codex-review-newly-added-ports-2026-04-26): runtime의 Telegram
직접 결합을 AlertPort로 해체. listeners + bootstrap + background_loops 모두
이 Port에 의존 — 향후 Discord/Slack swap 가능.

구현체:
- engine/src/infra/telegram_trade_bot.py.TelegramTradeBot
- engine/src/infra/telegram.py.TradeBot
- 향후 NoOpAlertAdapter (test)
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AlertPort(Protocol):
    """Hexagonal port for engine alert/notification.

    Korean fill notification + alert by category. Telegram production 시그니처 매칭.
    """

    async def send_alert_kr(self, alert_type: str, data: dict[str, Any]) -> bool:
        """Korean alert 전송 by category.

        예: send_alert_kr("position_tracking_fail", {"error_count": 6})
        Returns True on success, False on failure (caller-aware).
        """
        ...

    async def send_fill_kr(self, data: dict[str, Any]) -> bool:
        """Korean fill notification 전송.

        예: send_fill_kr({"strategy_id": "spot_futures", "pnl": 1.5, ...})
        """
        ...
