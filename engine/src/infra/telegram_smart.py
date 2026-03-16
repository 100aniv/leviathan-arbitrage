"""SmartTelegramAlerter — Redis-based dedup + INFO batching (US-213).

Wraps TelegramAlerter with:
  1. Redis SET dedup: identical message hash → skip for TTL seconds (default 300s).
  2. INFO batch: INFO-level alerts accumulated for batch_interval (default 1800s),
     then sent as a single combined message.
  3. In-memory fallback when Redis is unavailable.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any

import structlog

from src.infra.telegram import AlertSeverity, SeverityFilter, TelegramAlerter

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_DEDUP_TTL_SECONDS = 300       # 5 minutes
_BATCH_INTERVAL_SECONDS = 1800  # 30 minutes
_DEDUP_KEY_PREFIX = "leviathan:telegram:dedup:"


class SmartTelegramAlerter:
    """Telegram alerter with Redis dedup and INFO batching.

    Falls back to in-memory dict when Redis is not available.
    """

    def __init__(
        self,
        alerter: TelegramAlerter,
        redis_client: Any | None = None,
        dedup_ttl: int = _DEDUP_TTL_SECONDS,
        batch_interval: int = _BATCH_INTERVAL_SECONDS,
    ) -> None:
        self._alerter = alerter
        self._redis = redis_client  # RedisClient or None
        self._dedup_ttl = dedup_ttl
        self._batch_interval = batch_interval

        # In-memory fallback for dedup
        self._memory_dedup: dict[str, float] = {}

        # INFO batch buffer
        self._info_buffer: list[str] = []
        self._last_batch_flush: float = time.monotonic()

        # Severity filter (reuse from telegram.py)
        self._severity_filter = SeverityFilter()

        # Background flush task
        self._flush_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def send_smart(
        self,
        message: str,
        severity: AlertSeverity = AlertSeverity.INFO,
        level: str = "INFO",
    ) -> bool:
        """Send an alert with dedup and optional batching.

        - Dedup: skip if same message hash was sent within dedup_ttl.
        - Batch: INFO-level messages are buffered and flushed periodically.
        - EMERGENCY/CRITICAL/WARNING: sent immediately (if not deduped).

        Returns True if sent/buffered, False if deduped/failed.
        """
        msg_hash = self._hash(message)

        # 1) Dedup check
        if await self._is_duplicate(msg_hash):
            logger.debug("smart_telegram_dedup_skip", hash=msg_hash[:12])
            return False

        # 2) Record dedup
        await self._record_dedup(msg_hash)

        # 3) INFO → batch buffer
        if severity == AlertSeverity.INFO:
            self._info_buffer.append(message)
            logger.debug(
                "smart_telegram_info_buffered",
                buffer_size=len(self._info_buffer),
            )
            return True

        # 4) Non-INFO → send immediately
        return await self._alerter.send_alert(message, level=level)

    async def flush_batch(self) -> bool:
        """Flush buffered INFO messages as a single combined message.

        Returns True if a batch was sent, False if buffer was empty or send failed.
        """
        if not self._info_buffer:
            self._last_batch_flush = time.monotonic()
            return False

        # Combine all buffered messages
        count = len(self._info_buffer)
        combined = (
            f"📋 <b>알림 모음 ({count}건)</b>\n"
            + "\n─────────────────\n".join(self._info_buffer)
        )
        self._info_buffer.clear()
        self._last_batch_flush = time.monotonic()

        result = await self._alerter.send_alert(combined, level="INFO")
        logger.info("smart_telegram_batch_flushed", count=count, sent=result)
        return result

    async def start_flush_loop(self) -> None:
        """Start background task that flushes INFO batch periodically."""
        if self._flush_task is not None:
            return
        self._flush_task = asyncio.create_task(self._flush_loop())

    async def stop(self) -> None:
        """Stop flush loop and send remaining buffered messages."""
        if self._flush_task is not None:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            self._flush_task = None
        # Final flush
        await self.flush_batch()

    @property
    def buffer_size(self) -> int:
        """Number of INFO messages currently buffered."""
        return len(self._info_buffer)

    # ------------------------------------------------------------------
    # Dedup helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _hash(message: str) -> str:
        """SHA-256 hex digest of message text."""
        return hashlib.sha256(message.encode("utf-8")).hexdigest()

    async def _is_duplicate(self, msg_hash: str) -> bool:
        """Check if msg_hash exists in Redis SET or memory fallback."""
        # Try Redis first
        if self._redis is not None:
            try:
                redis_conn = self._redis.redis if hasattr(self._redis, "redis") else self._redis
                result = await redis_conn.get(_DEDUP_KEY_PREFIX + msg_hash)
                return result is not None
            except Exception:
                logger.debug("smart_telegram_redis_dedup_fallback")
                # Fall through to memory

        # Memory fallback
        now = time.monotonic()
        self._evict_expired(now)
        return msg_hash in self._memory_dedup

    async def _record_dedup(self, msg_hash: str) -> None:
        """Store msg_hash in Redis with TTL, or in memory fallback."""
        if self._redis is not None:
            try:
                redis_conn = self._redis.redis if hasattr(self._redis, "redis") else self._redis
                await redis_conn.setex(
                    _DEDUP_KEY_PREFIX + msg_hash,
                    self._dedup_ttl,
                    "1",
                )
                return
            except Exception:
                logger.debug("smart_telegram_redis_record_fallback")

        # Memory fallback
        self._memory_dedup[msg_hash] = time.monotonic()

    def _evict_expired(self, now: float) -> None:
        """Remove expired entries from in-memory dedup cache."""
        expired = [
            k for k, ts in self._memory_dedup.items()
            if (now - ts) >= self._dedup_ttl
        ]
        for k in expired:
            del self._memory_dedup[k]

    # ------------------------------------------------------------------
    # Background flush
    # ------------------------------------------------------------------

    async def _flush_loop(self) -> None:
        """Periodically flush INFO batch buffer."""
        while True:
            await asyncio.sleep(self._batch_interval)
            try:
                await self.flush_batch()
            except Exception:
                logger.warning("smart_telegram_flush_error", exc_info=True)
