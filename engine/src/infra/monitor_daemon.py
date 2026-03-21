"""LEVIATHAN Monitor Daemon.

5분 주기 인프라 헬스체크 데몬.
Redis, TimescaleDB, Engine API를 주기적으로 확인하고
연속 실패 시 Telegram 알림을 발송합니다.
"""
from __future__ import annotations

import asyncio
import os
import structlog

from src.infra.telegram import TelegramAlerter

try:
    import redis.asyncio as aioredis
    _REDIS_AVAILABLE = True
except ImportError:
    _REDIS_AVAILABLE = False

try:
    import asyncpg
    _ASYNCPG_AVAILABLE = True
except ImportError:
    _ASYNCPG_AVAILABLE = False

try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

logger = structlog.get_logger(__name__)


class MonitorDaemon:
    """5분 주기 인프라 헬스체크 데몬."""

    def __init__(
        self,
        interval_sec: int = 300,
        failure_threshold: int = 3,
        infra_bot: object | None = None,
    ) -> None:
        self.interval = interval_sec
        self.threshold = failure_threshold
        self._infra_bot = infra_bot
        self.alerter = TelegramAlerter() if infra_bot is None else None
        self.failure_counts: dict[str, int] = {}

    @classmethod
    def create_from_bot(cls, infra_bot: object, interval_sec: int = 300, failure_threshold: int = 3) -> "MonitorDaemon":
        """InfraTelegramBot 인스턴스로 MonitorDaemon 생성."""
        return cls(interval_sec=interval_sec, failure_threshold=failure_threshold, infra_bot=infra_bot)

    async def run(self) -> None:
        """메인 루프 — interval마다 check_all() 호출."""
        logger.info("monitor_daemon_started", interval_sec=self.interval)
        while True:
            try:
                results = await self.check_all()
                logger.info("monitor_check_complete", results=results)
            except Exception as exc:
                logger.error("monitor_check_error", error=str(exc), exc_info=True)
            await asyncio.sleep(self.interval)

    async def check_all(self) -> dict[str, bool]:
        """모든 서비스 체크 후 결과 반환."""
        results: dict[str, bool] = {}
        checks = {
            "redis": self.check_redis,
            "timescaledb": self.check_timescaledb,
            "engine": self.check_engine,
        }
        for service, check_fn in checks.items():
            try:
                ok = await check_fn()
            except Exception as exc:
                logger.error("monitor_check_exception", service=service, error=str(exc))
                ok = False

            results[service] = ok
            if ok:
                await self._handle_recovery(service)
            else:
                await self._handle_failure(service, "check returned False")

        return results

    async def check_redis(self) -> bool:
        """redis PING — redis.asyncio 사용."""
        if not _REDIS_AVAILABLE:
            logger.warning("redis_asyncio_not_available")
            return False

        url = os.getenv("REDIS_URL", "redis://redis:6379/0")
        redis_password = os.getenv("REDIS_PASSWORD")
        client = None
        try:
            client = aioredis.from_url(url, password=redis_password, socket_connect_timeout=5)
            await client.ping()
            return True
        except Exception as exc:
            logger.warning("redis_check_failed", error=str(exc))
            return False
        finally:
            if client is not None:
                await client.aclose()

    async def check_timescaledb(self) -> bool:
        """timescaledb SELECT 1 — asyncpg 사용."""
        if not _ASYNCPG_AVAILABLE:
            logger.warning("asyncpg_not_available")
            return False

        dsn = os.getenv("DATABASE_URL", "")
        # asyncpg는 postgresql+asyncpg:// 접두사를 지원하지 않으므로 변환
        dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
        if not dsn:
            logger.warning("database_url_not_set")
            return False

        conn = None
        try:
            conn = await asyncio.wait_for(asyncpg.connect(dsn), timeout=10)
            await conn.fetchval("SELECT 1")
            return True
        except Exception as exc:
            logger.warning("timescaledb_check_failed", error=str(exc))
            return False
        finally:
            if conn is not None:
                await conn.close()

    async def check_engine(self) -> bool:
        """engine:8000/health HTTP GET — httpx 사용."""
        if not _HTTPX_AVAILABLE:
            logger.warning("httpx_not_available")
            return False

        base_url = os.getenv("ENGINE_URL", "http://engine:8000")
        url = f"{base_url.rstrip('/')}/health"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url)
                response.raise_for_status()
                return True
        except Exception as exc:
            logger.warning("engine_check_failed", error=str(exc))
            return False

    async def _handle_failure(self, service: str, error: str) -> None:
        """연속 실패 카운트 후 threshold 초과 시 Telegram 알림."""
        self.failure_counts[service] = self.failure_counts.get(service, 0) + 1
        count = self.failure_counts[service]
        logger.warning("service_failure", service=service, count=count, error=error)
        if count == self.threshold:
            msg = f"🔴 {service} DOWN: {error}\n연속 {count}회 실패"
            if self._infra_bot is not None:
                await self._infra_bot.send_message(msg)
            elif self.alerter is not None:
                await self.alerter.send_alert(msg, level="CRITICAL")

    async def _handle_recovery(self, service: str) -> None:
        """이전 실패 후 복구 시 알림."""
        if self.failure_counts.get(service, 0) >= self.threshold:
            msg = f"🟢 {service} RECOVERED"
            if self._infra_bot is not None:
                await self._infra_bot.send_message(msg)
            elif self.alerter is not None:
                await self.alerter.send_alert(msg, level="INFO")
        self.failure_counts[service] = 0


if __name__ == "__main__":
    interval = int(os.getenv("MONITOR_INTERVAL_SEC", "300"))
    daemon = MonitorDaemon(interval_sec=interval)
    asyncio.run(daemon.run())
