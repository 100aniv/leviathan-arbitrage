"""Telegram bidirectional command handler — long-polling based.

US-117: 5 commands (/status /kill /mode /balance /help) via Telegram Bot API.
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

import httpx
import structlog

from src.infra.telegram import TelegramAlerter

logger = structlog.get_logger(__name__)


class TelegramCommandHandler:
    """Long-poll Telegram updates and dispatch engine control commands."""

    COMMANDS = {"/status", "/kill", "/mode", "/balance", "/help"}
    HELP_TEXT = (
        "LEVIATHAN Bot Commands:\n"
        "/status — Engine status + today's PnL + active strategies\n"
        "/kill — Trigger KillSwitch immediately\n"
        "/mode — Current execution mode\n"
        "/balance — Exchange balance summary\n"
        "/help — Show this help message"
    )

    def __init__(
        self,
        alerter: TelegramAlerter,
        status_fn: Callable[[], Awaitable[str]] | None = None,
        kill_fn: Callable[[], Awaitable[str]] | None = None,
        mode_fn: Callable[[], Awaitable[str]] | None = None,
        balance_fn: Callable[[], Awaitable[str]] | None = None,
        allowed_chat_ids: set[int] | None = None,
    ) -> None:
        self._alerter = alerter
        self._offset: int = 0
        self._running = False
        self._consecutive_errors: int = 0
        # Reusable HTTP client for long-polling (US-168)
        self._http_client: httpx.AsyncClient | None = None
        # Auth: only allow commands from these chat IDs (fallback to TELEGRAM_CHAT_ID env)
        if allowed_chat_ids is not None:
            self._allowed_chat_ids = allowed_chat_ids
        else:
            import os
            chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
            self._allowed_chat_ids = {int(chat_id)} if chat_id.strip() else set()
        self._handlers: dict[str, Callable[[], Awaitable[str]]] = {
            "/status": status_fn or self._default_status,
            "/kill": kill_fn or self._default_kill,
            "/mode": mode_fn or self._default_mode,
            "/balance": balance_fn or self._default_balance,
            "/help": self._handle_help,
        }

    # ---------------------------------------------------------------------------
    # Default no-op handlers (overridden via constructor)
    # ---------------------------------------------------------------------------

    async def _default_status(self) -> str:
        return "Engine running (no status_fn configured)"

    async def _default_kill(self) -> str:
        return "KillSwitch not configured"

    async def _default_mode(self) -> str:
        return "Mode info not available"

    async def _default_balance(self) -> str:
        return "Balance info not available"

    async def _handle_help(self) -> str:
        return self.HELP_TEXT

    # ---------------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------------

    async def process_command(self, text: str) -> str:
        """Parse first token and dispatch to registered handler."""
        cmd = text.strip().split()[0].lower() if text.strip() else ""
        handler = self._handlers.get(cmd)
        if handler is None:
            return f"Unknown command: {cmd}\n\n{self.HELP_TEXT}"
        return await handler()

    async def poll_updates(self) -> list[dict]:
        """Long-poll Telegram getUpdates API (timeout=30s)."""
        if not self._alerter.bot_token or not self._alerter.enabled:
            return []
        url = f"https://api.telegram.org/bot{self._alerter.bot_token}/getUpdates"
        params: dict = {
            "offset": self._offset,
            "timeout": 30,
            "allowed_updates": ["message"],
        }
        try:
            if self._http_client is None:
                self._http_client = httpx.AsyncClient(timeout=35)
            resp = await self._http_client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                logger.warning("telegram_api_error", response=data)
                return []
            self._consecutive_errors = 0
            return data.get("result", [])
        except Exception:
            self._consecutive_errors = getattr(self, "_consecutive_errors", 0) + 1
            logger.warning("telegram_poll_failed", consecutive=self._consecutive_errors, exc_info=True)
            return []

    async def close(self) -> None:
        """Close the reusable HTTP client (US-168)."""
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def poll_loop(self) -> None:
        """Run long-poll loop until stop() is called."""
        self._running = True
        self._consecutive_errors = 0
        while self._running:
            updates = await self.poll_updates()
            for update in updates:
                self._offset = update["update_id"] + 1
                msg = update.get("message", {})
                chat_id = msg.get("chat", {}).get("id")
                text = msg.get("text", "")
                # Auth check: fail-closed — no allowed_chat_ids means block ALL
                if not self._allowed_chat_ids:
                    logger.warning("telegram_no_allowed_chats", chat_id=chat_id)
                    continue
                if chat_id not in self._allowed_chat_ids:
                    logger.warning("telegram_unauthorized_command", chat_id=chat_id, text=text)
                    continue
                if text.startswith("/"):
                    response = await self.process_command(text)
                    await self._alerter.send_alert(response, level="INFO")
            if not updates:
                # Exponential backoff on consecutive errors, capped at 60s
                backoff = min(60, 2 ** min(self._consecutive_errors, 6)) if self._consecutive_errors > 0 else 1
                await asyncio.sleep(backoff)

    def stop(self) -> None:
        """Signal poll_loop to exit after current iteration."""
        self._running = False
