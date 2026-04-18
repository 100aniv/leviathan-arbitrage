"""Async Redis client with connection pool, health check, and auto-reconnect.

Uses redis.asyncio with hiredis parser for performance.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)


@dataclass
class RedisConfig:
    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: Optional[str] = None
    max_connections: int = 100
    socket_timeout: float = 5.0
    socket_connect_timeout: float = 5.0
    socket_keepalive: bool = True
    health_check_interval: int = 30
    retry_on_timeout: bool = True


class RedisClient:
    """
    Async Redis client wrapping redis.asyncio with connection pool management.

    Usage:
        client = RedisClient(RedisConfig())
        await client.connect()
        ...
        await client.disconnect()

    For tests, inject a fakeredis instance via client._redis = FakeRedis().
    """

    def __init__(self, config: RedisConfig) -> None:
        self._config = config
        self._redis: Optional[aioredis.Redis] = None
        self._pool: Optional[aioredis.ConnectionPool] = None
        self._reconnect_lock = asyncio.Lock()

    async def connect(self) -> None:
        """Create connection pool and verify connectivity via PING."""
        redis_password = self._config.password or os.environ.get("REDIS_PASSWORD") or None
        self._pool = aioredis.ConnectionPool.from_url(
            f"redis://{self._config.host}:{self._config.port}/{self._config.db}",
            password=redis_password,
            max_connections=self._config.max_connections,
            socket_timeout=self._config.socket_timeout,
            socket_connect_timeout=self._config.socket_connect_timeout,
            socket_keepalive=self._config.socket_keepalive,
            health_check_interval=self._config.health_check_interval,
            retry_on_timeout=self._config.retry_on_timeout,
        )
        self._redis = aioredis.Redis(connection_pool=self._pool)
        await self._redis.ping()
        logger.info("Redis connected: %s:%d db=%d", self._config.host, self._config.port, self._config.db)

    async def _ensure_connected(self) -> bool:
        """Return True if connected. Attempt reconnect if not. Returns False if failed."""
        if self._redis is not None:
            return True
        async with self._reconnect_lock:
            if self._redis is not None:  # double-check after lock
                return True
            try:
                logger.warning("Redis disconnected — attempting auto-reconnect...")
                await self.connect()
                logger.info("Redis auto-reconnect successful")
                return True
            except Exception as exc:
                logger.error("Redis auto-reconnect failed: %s", exc)
                return False

    async def disconnect(self) -> None:
        """Close all connections gracefully."""
        if self._redis:
            await self._redis.aclose()
            self._redis = None
        if self._pool:
            await self._pool.aclose()
            self._pool = None
        logger.info("Redis disconnected")

    async def health_check(self) -> dict[str, Any]:
        """
        Run PING + INFO memory. Returns dict with 'status': 'ok' or 'error'.
        """
        if self._redis is None:
            return {"status": "error", "error": "not connected"}
        try:
            ping = await self._redis.ping()
        except Exception as exc:
            logger.error("Redis ping failed: %s", exc)
            return {"status": "error", "error": str(exc)}

        memory_used = 0
        memory_max = 0
        try:
            info = await self._redis.info("memory")
            memory_used = info.get("used_memory", 0)
            memory_max = info.get("maxmemory", 0)
        except Exception:
            pass  # INFO not supported in test environments (fakeredis)

        return {
            "status": "ok",
            "ping": ping,
            "memory_used_bytes": memory_used,
            "memory_max_bytes": memory_max,
        }

    @property
    def redis(self) -> aioredis.Redis:
        """Direct access to underlying redis.asyncio client."""
        if self._redis is None:
            raise RuntimeError("RedisClient not connected. Call connect() first.")
        return self._redis

    # ── String operations ──────────────────────────────────────────────────────

    async def set(self, key: str, value: Any, ex: Optional[int] = None) -> None:
        if not await self._ensure_connected():
            return
        try:
            await self._redis.set(key, value, ex=ex)
        except Exception as exc:
            logger.warning("Redis set failed key=%s: %s", key, exc)
            self._redis = None

    async def get(self, key: str) -> Optional[bytes]:
        if not await self._ensure_connected():
            return None
        try:
            return await self._redis.get(key)
        except Exception as exc:
            logger.warning("Redis get failed key=%s: %s", key, exc)
            self._redis = None
            return None

    async def delete(self, *keys: str) -> int:
        if not await self._ensure_connected():
            return 0
        try:
            return await self._redis.delete(*keys)
        except Exception as exc:
            logger.warning("Redis delete failed: %s", exc)
            self._redis = None
            return 0

    # ── Hash operations ────────────────────────────────────────────────────────

    async def hset(self, name: str, mapping: dict) -> int:
        if not await self._ensure_connected():
            return 0
        try:
            return await self._redis.hset(name, mapping=mapping)
        except Exception as exc:
            logger.warning("Redis hset failed name=%s: %s", name, exc)
            self._redis = None
            return 0

    async def hget(self, name: str, key: str) -> Optional[bytes]:
        if not await self._ensure_connected():
            return None
        try:
            return await self._redis.hget(name, key)
        except Exception as exc:
            logger.warning("Redis hget failed name=%s key=%s: %s", name, key, exc)
            self._redis = None
            return None

    async def hgetall(self, name: str) -> dict:
        if not await self._ensure_connected():
            return {}
        try:
            return await self._redis.hgetall(name)
        except Exception as exc:
            logger.warning("Redis hgetall failed name=%s: %s", name, exc)
            self._redis = None
            return {}

    async def hdel(self, name: str, *keys: str) -> int:
        if not await self._ensure_connected():
            return 0
        try:
            return await self._redis.hdel(name, *keys)
        except Exception as exc:
            logger.warning("Redis hdel failed name=%s: %s", name, exc)
            self._redis = None
            return 0

    # ── Sorted set operations ──────────────────────────────────────────────────

    async def zadd(self, name: str, mapping: dict) -> int:
        if not await self._ensure_connected():
            return 0
        try:
            return await self._redis.zadd(name, mapping)
        except Exception as exc:
            logger.warning("Redis zadd failed name=%s: %s", name, exc)
            self._redis = None
            return 0

    async def zrem(self, name: str, *values: str) -> int:
        if not await self._ensure_connected():
            return 0
        try:
            return await self._redis.zrem(name, *values)
        except Exception as exc:
            logger.warning("Redis zrem failed name=%s: %s", name, exc)
            self._redis = None
            return 0

    async def zrangebyscore(
        self, name: str, min: Any, max: Any, withscores: bool = False
    ) -> list:
        if not await self._ensure_connected():
            return []
        try:
            return await self._redis.zrangebyscore(name, min, max, withscores=withscores)
        except Exception as exc:
            logger.warning("Redis zrangebyscore failed name=%s: %s", name, exc)
            self._redis = None
            return []

    async def zremrangebyscore(self, name: str, min: Any, max: Any) -> int:
        if not await self._ensure_connected():
            return 0
        try:
            return await self._redis.zremrangebyscore(name, min, max)
        except Exception as exc:
            logger.warning("Redis zremrangebyscore failed name=%s: %s", name, exc)
            self._redis = None
            return 0

    # ── Stream operations ──────────────────────────────────────────────────────

    async def xadd(
        self, name: str, fields: dict, id: str = "*",
        maxlen: int | None = None, approximate: bool = True,
    ) -> bytes:
        if not await self._ensure_connected():
            logger.warning("xadd skipped (Redis unavailable) stream=%s", name)
            return b""
        try:
            return await self._redis.xadd(
                name, fields, id=id, maxlen=maxlen, approximate=approximate,
            )
        except Exception as exc:
            logger.warning("Redis xadd failed stream=%s: %s", name, exc)
            self._redis = None
            return b""

    async def xread(
        self,
        streams: dict,
        count: Optional[int] = None,
        block: Optional[int] = None,
    ) -> list:
        if not await self._ensure_connected():
            return []
        try:
            return await self._redis.xread(streams, count=count, block=block)
        except Exception as exc:
            logger.warning("Redis xread failed: %s", exc)
            self._redis = None
            return []

    async def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        streams: dict,
        count: Optional[int] = None,
        block: Optional[int] = None,
        noack: bool = False,
    ) -> list:
        if not await self._ensure_connected():
            return []
        try:
            return await self._redis.xreadgroup(
                groupname, consumername, streams,
                count=count, block=block, noack=noack,
            )
        except Exception as exc:
            logger.warning("Redis xreadgroup failed group=%s: %s", groupname, exc)
            self._redis = None
            return []

    async def xgroup_create(
        self, name: str, groupname: str, id: str = "$", mkstream: bool = True
    ) -> None:
        if not await self._ensure_connected():
            return
        try:
            await self._redis.xgroup_create(name, groupname, id=id, mkstream=mkstream)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                logger.warning("Redis xgroup_create failed stream=%s group=%s: %s", name, groupname, exc)
                self._redis = None

    async def xack(self, name: str, groupname: str, *ids) -> int:
        if not await self._ensure_connected():
            return 0
        try:
            return await self._redis.xack(name, groupname, *ids)
        except Exception as exc:
            logger.warning("Redis xack failed stream=%s: %s", name, exc)
            self._redis = None
            return 0

    async def xpending(self, name: str, groupname: str) -> dict:
        if not await self._ensure_connected():
            return {}
        try:
            return await self._redis.xpending(name, groupname)
        except Exception as exc:
            logger.warning("Redis xpending failed stream=%s: %s", name, exc)
            self._redis = None
            return {}

    async def xclaim(
        self,
        name: str,
        groupname: str,
        consumername: str,
        min_idle_time: int,
        message_ids: list,
    ) -> list:
        if not await self._ensure_connected():
            return []
        try:
            return await self._redis.xclaim(
                name, groupname, consumername, min_idle_time, message_ids
            )
        except Exception as exc:
            logger.warning("Redis xclaim failed stream=%s: %s", name, exc)
            self._redis = None
            return []
