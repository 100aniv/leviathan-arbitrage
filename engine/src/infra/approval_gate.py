"""Telegram approval gate for Live trading entry — US-364.

Sends a DevBot approval request and waits for operator confirmation
before allowing LiveMode to start.

Fail-closed: no response within timeout → False (live trading blocked).
"""
from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

# Module-level registry: stage → asyncio.Event (pending approvals)
_PENDING_EVENTS: dict[str, asyncio.Event] = {}
_APPROVAL_RESULT: dict[str, bool] = {}

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


async def request_live_approval(
    stage: str,
    details: str,
    timeout_secs: int = 600,
) -> bool:
    """Request Live trading approval via Telegram DevBot.

    Sends a message to DEV_TELEGRAM_BOT_TOKEN and waits up to timeout_secs
    for the operator to respond '/approve {stage}' or '/reject {stage}'.

    10-minute wait → retransmit once → return False (fail-closed).

    Returns:
        True  — operator approved
        False — rejected, timed out, or Telegram not configured
    """
    token = os.getenv("DEV_TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("DEV_TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        logger.warning(
            "approval_gate.no_telegram_config stage=%s — auto-approve (dev mode)", stage
        )
        return True  # Dev/CI: allow without Telegram

    event = asyncio.Event()
    _PENDING_EVENTS[stage] = event
    _APPROVAL_RESULT.pop(stage, None)

    half = timeout_secs / 2

    try:
        msg = (
            f"🔐 *LEVIATHAN Live 진입 승인 요청*\n"
            f"Stage: `{stage}`\n"
            f"{details}\n\n"
            f"✅ 승인: `/approve {stage}`\n"
            f"❌ 거부: `/reject {stage}`\n"
            f"⏱ 타임아웃: {timeout_secs}s (무응답 시 자동 거부)"
        )
        await _send_telegram(token, chat_id, msg)

        # First half-window
        try:
            await asyncio.wait_for(event.wait(), timeout=half)
        except asyncio.TimeoutError:
            # Retransmit once
            await _send_telegram(
                token, chat_id,
                f"⏰ *재전송* — `/approve {stage}` 응답 대기 중 (잔여 {int(half)}s)"
            )
            try:
                await asyncio.wait_for(event.wait(), timeout=half)
            except asyncio.TimeoutError:
                logger.warning("approval_gate.timeout stage=%s — blocking live entry", stage)
                return False

        result = _APPROVAL_RESULT.get(stage, False)
        logger.info("approval_gate.result stage=%s approved=%s", stage, result)
        return result

    finally:
        _PENDING_EVENTS.pop(stage, None)
        _APPROVAL_RESULT.pop(stage, None)


def approve(stage: str) -> bool:
    """Mark a pending approval stage as approved.

    Called by /api/paper/approve endpoint or Telegram DevBot handler.
    Returns False if no pending request for this stage.
    """
    event = _PENDING_EVENTS.get(stage)
    if event is None:
        logger.warning("approval_gate.approve_no_pending stage=%s", stage)
        return False
    _APPROVAL_RESULT[stage] = True
    event.set()
    return True


def reject(stage: str) -> bool:
    """Mark a pending approval stage as rejected."""
    event = _PENDING_EVENTS.get(stage)
    if event is None:
        return False
    _APPROVAL_RESULT[stage] = False
    event.set()
    return True


def list_pending() -> list[str]:
    """Return list of stages awaiting approval."""
    return list(_PENDING_EVENTS.keys())


async def _send_telegram(token: str, chat_id: str, text: str) -> None:
    """Send a message via Telegram Bot API (best-effort, never raises)."""
    try:
        import httpx  # noqa: PLC0415
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                _TELEGRAM_API.format(token=token),
                json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("approval_gate.telegram_send_failed: %s", exc)
