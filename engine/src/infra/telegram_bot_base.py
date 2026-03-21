"""LEVIATHAN Telegram Bot Base — inline keyboard + callback + photo support.

US-291-a: Base class for all 3 Telegram bots (Infra, Trade, Dev).
Provides:
  - Inline keyboard markup builder
  - Callback query handling (answerCallbackQuery)
  - Photo sending (sendPhoto)
  - Long-polling with message + callback_query support
  - Command routing + auth (chat_id whitelist)
  - Rate limiting (20 msgs/min)
"""
from __future__ import annotations

import asyncio
import io
import os
import time
from collections import deque
from typing import Any, Awaitable, Callable

import httpx
import structlog

logger = structlog.get_logger(__name__)

# Type aliases
CommandHandler = Callable[..., Awaitable[str | None]]
CallbackHandler = Callable[[dict], Awaitable[str | None]]


class InlineKeyboard:
    """Builder for Telegram InlineKeyboardMarkup."""

    def __init__(self) -> None:
        self._rows: list[list[dict[str, str]]] = []

    def row(self, *buttons: tuple[str, str]) -> "InlineKeyboard":
        """Add a row of buttons. Each button is (text, callback_data)."""
        self._rows.append(
            [{"text": text, "callback_data": data} for text, data in buttons]
        )
        return self

    def to_markup(self) -> dict:
        """Convert to Telegram InlineKeyboardMarkup dict."""
        return {"inline_keyboard": self._rows}


class TelegramBotBase:
    """Base class for LEVIATHAN Telegram bots.

    Supports:
      - Text messages with HTML parse mode
      - Inline keyboard buttons
      - Callback query handling
      - Photo sending (PNG bytes)
      - Long-polling for updates (message + callback_query)
      - Command routing with prefix matching
      - Chat ID authentication
      - Rate limiting (20 msgs/min)
    """

    API_BASE = "https://api.telegram.org/bot{token}"
    MAX_MESSAGES_PER_MINUTE = 20

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
        enabled: bool | None = None,
        *,
        token_env: str = "TELEGRAM_BOT_TOKEN",
        chat_id_env: str = "TELEGRAM_CHAT_ID",
        enabled_env: str = "TELEGRAM_ENABLED",
        bot_name: str = "Bot",
    ) -> None:
        self._bot_token = bot_token or os.getenv(token_env, "")
        self._chat_id = chat_id or os.getenv(chat_id_env, "")

        if enabled is not None:
            self._enabled = enabled
        else:
            self._enabled = os.getenv(enabled_env, "false").lower() == "true"

        self._bot_name = bot_name
        self._offset: int = 0
        self._running = False
        self._consecutive_errors: int = 0
        self._http_client: httpx.AsyncClient | None = None
        self._send_times: deque[float] = deque()

        # Command handlers: "/cmd" -> async handler(text, chat_id, message)
        self._commands: dict[str, CommandHandler] = {}
        # Callback handlers: "prefix" -> async handler(callback_query)
        self._callbacks: dict[str, CallbackHandler] = {}

        # Auth: allowed chat IDs
        if self._chat_id:
            try:
                self._allowed_chat_ids: set[int] = {int(self._chat_id)}
            except ValueError:
                self._allowed_chat_ids = set()
        else:
            self._allowed_chat_ids = set()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def bot_name(self) -> str:
        return self._bot_name

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_command(self, command: str, handler: CommandHandler) -> None:
        """Register a command handler. Command should start with '/'."""
        self._commands[command.lower()] = handler

    def register_callback(self, prefix: str, handler: CallbackHandler) -> None:
        """Register a callback handler for callback_data starting with prefix."""
        self._callbacks[prefix] = handler

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    async def send_message(
        self,
        text: str,
        reply_markup: dict | None = None,
        chat_id: str | None = None,
        parse_mode: str = "HTML",
    ) -> dict | None:
        """Send a text message, optionally with inline keyboard.

        Returns the Telegram API response dict, or None on failure.
        """
        if not self._enabled or not self._bot_token:
            return None

        if not self._check_rate_limit():
            logger.warning("telegram_rate_limit", bot=self._bot_name)
            return None

        target_chat = chat_id or self._chat_id
        if not target_chat:
            return None

        url = f"{self.API_BASE.format(token=self._bot_token)}/sendMessage"
        payload: dict[str, Any] = {
            "chat_id": target_chat,
            "text": text,
            "parse_mode": parse_mode,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        result = await self._post(url, payload)
        if result is None and parse_mode == "HTML":
            # HTML parse failure fallback — retry without parse_mode
            payload.pop("parse_mode", None)
            result = await self._post(url, payload)
        return result

    async def edit_message(
        self,
        chat_id: str | int,
        message_id: int,
        text: str,
        reply_markup: dict | None = None,
        parse_mode: str = "HTML",
    ) -> dict | None:
        """Edit an existing message (for inline keyboard updates)."""
        if not self._enabled or not self._bot_token:
            return None

        url = f"{self.API_BASE.format(token=self._bot_token)}/editMessageText"
        payload: dict[str, Any] = {
            "chat_id": str(chat_id),
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        return await self._post(url, payload)

    async def send_photo(
        self,
        photo: bytes | io.BytesIO,
        caption: str = "",
        chat_id: str | None = None,
    ) -> dict | None:
        """Send a photo (PNG bytes) with optional caption."""
        if not self._enabled or not self._bot_token:
            return None

        if not self._check_rate_limit():
            logger.warning("telegram_rate_limit_photo", bot=self._bot_name)
            return None

        target_chat = chat_id or self._chat_id
        if not target_chat:
            return None

        url = f"{self.API_BASE.format(token=self._bot_token)}/sendPhoto"

        if isinstance(photo, bytes):
            photo_file = io.BytesIO(photo)
        else:
            photo_file = photo

        try:
            client = await self._get_client()
            resp = await client.post(
                url,
                data={"chat_id": target_chat, "caption": caption, "parse_mode": "HTML"},
                files={"photo": ("chart.png", photo_file, "image/png")},
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.error("telegram_send_photo_error", bot=self._bot_name, error=str(exc))
            return None

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: str = "",
        show_alert: bool = False,
    ) -> bool:
        """Acknowledge a callback query (button press)."""
        if not self._enabled or not self._bot_token:
            return False

        url = f"{self.API_BASE.format(token=self._bot_token)}/answerCallbackQuery"
        payload = {
            "callback_query_id": callback_query_id,
            "text": text,
            "show_alert": show_alert,
        }
        result = await self._post(url, payload)
        return result is not None

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    async def poll_loop(self) -> None:
        """Run long-poll loop for updates (messages + callback queries)."""
        self._running = True
        self._consecutive_errors = 0
        logger.info("telegram_bot_poll_started", bot=self._bot_name)

        while self._running:
            updates = await self._poll_updates()
            for update in updates:
                self._offset = update["update_id"] + 1
                try:
                    if "callback_query" in update:
                        await self._handle_callback(update["callback_query"])
                    elif "message" in update:
                        await self._handle_message(update["message"])
                except Exception:
                    logger.error("telegram_update_error", bot=self._bot_name, exc_info=True)

            if not updates:
                backoff = min(60, 2 ** min(self._consecutive_errors, 6)) if self._consecutive_errors > 0 else 1
                await asyncio.sleep(backoff)

    def stop(self) -> None:
        """Signal poll_loop to exit."""
        self._running = False

    async def close(self) -> None:
        """Close HTTP client."""
        self.stop()
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    # ------------------------------------------------------------------
    # Internal: update handling
    # ------------------------------------------------------------------

    async def _handle_message(self, message: dict) -> None:
        """Route incoming message to registered command handler."""
        chat_id = message.get("chat", {}).get("id")
        text = message.get("text", "")

        if not self._authorize(chat_id):
            return

        if not text.startswith("/"):
            return

        cmd = text.strip().split()[0].lower()
        handler = self._commands.get(cmd)
        if handler is None:
            # Try /help as default
            handler = self._commands.get("/help")
            if handler is None:
                return

        try:
            result = await handler(text, chat_id, message)
            if isinstance(result, str) and result:
                await self.send_message(result, chat_id=str(chat_id))
        except Exception:
            logger.error("telegram_command_error", bot=self._bot_name, cmd=cmd, exc_info=True)
            await self.send_message(
                f"오류가 발생했습니다: {cmd}\n잠시 후 다시 시도하세요.",
                chat_id=str(chat_id),
                parse_mode="",
            )

    async def _handle_callback(self, callback_query: dict) -> None:
        """Route callback query to registered callback handler."""
        chat_id = callback_query.get("message", {}).get("chat", {}).get("id")
        data = callback_query.get("data", "")

        if not self._authorize(chat_id):
            await self.answer_callback_query(callback_query["id"], "Unauthorized")
            return

        # Find matching callback handler by prefix
        for prefix, handler in self._callbacks.items():
            if data.startswith(prefix):
                try:
                    result = await handler(callback_query)
                    if isinstance(result, str) and result:
                        await self.answer_callback_query(callback_query["id"], result)
                    else:
                        await self.answer_callback_query(callback_query["id"])
                except Exception:
                    logger.error("telegram_callback_error", bot=self._bot_name, data=data, exc_info=True)
                    await self.answer_callback_query(callback_query["id"], "Error")
                return

        await self.answer_callback_query(callback_query["id"], "Unknown action")

    def _authorize(self, chat_id: int | None) -> bool:
        """Check if chat_id is authorized."""
        if not self._allowed_chat_ids:
            logger.warning("telegram_no_allowed_chats", bot=self._bot_name, chat_id=chat_id)
            return False
        if chat_id not in self._allowed_chat_ids:
            logger.warning("telegram_unauthorized", bot=self._bot_name, chat_id=chat_id)
            return False
        return True

    # ------------------------------------------------------------------
    # Internal: HTTP
    # ------------------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=35.0)
        return self._http_client

    async def _post(self, url: str, payload: dict) -> dict | None:
        """POST JSON to Telegram API. Returns response dict or None."""
        try:
            client = await self._get_client()
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                logger.warning("telegram_api_not_ok", bot=self._bot_name, response=data)
                return None
            return data.get("result", {})
        except httpx.HTTPStatusError as exc:
            logger.error("telegram_http_error", bot=self._bot_name, status=exc.response.status_code)
            return None
        except httpx.TimeoutException:
            logger.error("telegram_timeout", bot=self._bot_name)
            return None
        except Exception as exc:
            logger.error("telegram_error", bot=self._bot_name, error=str(exc))
            return None

    async def _poll_updates(self) -> list[dict]:
        """Long-poll Telegram getUpdates API."""
        if not self._enabled or not self._bot_token:
            await asyncio.sleep(5)
            return []

        url = f"{self.API_BASE.format(token=self._bot_token)}/getUpdates"
        params = {
            "offset": self._offset,
            "timeout": 30,
            "allowed_updates": ["message", "callback_query"],
        }
        try:
            client = await self._get_client()
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                return []
            self._consecutive_errors = 0
            return data.get("result", [])
        except Exception:
            self._consecutive_errors += 1
            logger.warning("telegram_poll_error", bot=self._bot_name, errors=self._consecutive_errors)
            return []

    def _check_rate_limit(self) -> bool:
        """Sliding-window rate limiter — max 20 messages per 60 seconds."""
        now = time.monotonic()
        window_start = now - 60.0
        while self._send_times and self._send_times[0] < window_start:
            self._send_times.popleft()
        if len(self._send_times) >= self.MAX_MESSAGES_PER_MINUTE:
            return False
        self._send_times.append(now)
        return True
