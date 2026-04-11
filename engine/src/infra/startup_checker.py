"""LEVIATHAN Startup Checker.
US-291-e: 엔진 시작 시 8개 항목 점검 + 인프라봇 체크리스트 전송.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import socket
import sys
import structlog
from src.core.config_loader import get_config as _gc

logger = structlog.get_logger(__name__)

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


class StartupChecker:
    """엔진 시작 시 8개 항목 점검."""

    REQUIRED_ENV_VARS = [
        "DATABASE_URL",
        "REDIS_URL",
    ]

    def __init__(self) -> None:
        self._results: dict[str, bool] = {}
        self._details: dict[str, str] = {}

    async def check_all(self) -> dict[str, bool]:
        """모든 항목 점검 후 결과 반환."""
        checks = [
            ("Redis", self._check_redis),
            ("TimescaleDB", self._check_timescaledb),
            ("API 포트", self._check_api_port),
            ("WebSocket", self._check_websocket),
            ("Prometheus", self._check_prometheus),
            (".env 변수", self._check_env_vars),
            ("디스크 공간", self._check_disk_space),
            ("Python 버전", self._check_python_version),
        ]
        for name, check_fn in checks:
            try:
                ok = await check_fn()
                self._results[name] = ok
            except Exception as exc:
                self._results[name] = False
                self._details[name] = str(exc)
                logger.error("startup_check_error", check=name, error=str(exc))
        return self._results

    def format_checklist(self) -> str:
        """체크리스트 HTML 포맷."""
        lines = ["🔍 <b>시작 체크리스트</b>\n"]
        all_pass = True
        for name, ok in self._results.items():
            icon = "✅" if ok else "❌"
            detail = self._details.get(name, "")
            line = f"  {icon} {name}"
            if detail:
                line += f" — {detail}"
            lines.append(line)
            if not ok:
                all_pass = False

        status = "🟢 전체 PASS" if all_pass else "🔴 일부 실패"
        lines.append(f"\n<b>결과: {status}</b>")
        return "\n".join(lines)

    @property
    def all_passed(self) -> bool:
        return all(self._results.values()) if self._results else False

    # --- Individual checks ---

    async def _check_redis(self) -> bool:
        if not _REDIS_AVAILABLE:
            self._details["Redis"] = "redis 패키지 미설치"
            return False
        url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        password = os.getenv("REDIS_PASSWORD")
        client = None
        try:
            client = aioredis.from_url(url, password=password, socket_connect_timeout=5)
            await client.ping()
            self._details["Redis"] = "연결 성공"
            return True
        except Exception as exc:
            self._details["Redis"] = f"연결 실패: {exc}"
            return False
        finally:
            if client:
                await client.aclose()

    async def _check_timescaledb(self) -> bool:
        if not _ASYNCPG_AVAILABLE:
            self._details["TimescaleDB"] = "asyncpg 미설치"
            return False
        dsn = os.getenv("DATABASE_URL", "")
        dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
        if not dsn:
            self._details["TimescaleDB"] = "DATABASE_URL 미설정"
            return False
        conn = None
        try:
            conn = await asyncio.wait_for(asyncpg.connect(dsn), timeout=10)
            await conn.fetchval("SELECT 1")
            self._details["TimescaleDB"] = "연결 성공"
            return True
        except Exception as exc:
            self._details["TimescaleDB"] = f"연결 실패: {exc}"
            return False
        finally:
            if conn:
                await conn.close()

    async def _check_api_port(self) -> bool:
        port = int(_gc("api.port", default=8000))
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.settimeout(1)
            result = sock.connect_ex(("127.0.0.1", port))
            if result == 0:
                self._details["API 포트"] = f"포트 {port} 이미 사용 중"
                return False
            self._details["API 포트"] = f"포트 {port} 사용 가능"
            return True
        except Exception:
            self._details["API 포트"] = f"포트 {port} 확인 가능"
            return True
        finally:
            sock.close()

    async def _check_websocket(self) -> bool:
        try:
            import websockets  # noqa: F401
            self._details["WebSocket"] = "websockets 패키지 OK"
            return True
        except ImportError:
            self._details["WebSocket"] = "websockets 패키지 미설치"
            return False

    async def _check_prometheus(self) -> bool:
        try:
            import prometheus_client  # noqa: F401
            self._details["Prometheus"] = "prometheus_client OK"
            return True
        except ImportError:
            self._details["Prometheus"] = "prometheus_client 미설치"
            return False

    async def _check_env_vars(self) -> bool:
        missing = [v for v in self.REQUIRED_ENV_VARS if not os.getenv(v)]
        if missing:
            self._details[".env 변수"] = f"누락: {', '.join(missing)}"
            return False
        self._details[".env 변수"] = "필수 변수 확인됨"
        return True

    async def _check_disk_space(self) -> bool:
        usage = shutil.disk_usage("/")
        free_gb = usage.free / (1024 ** 3)
        if free_gb < 1.0:
            self._details["디스크 공간"] = f"{free_gb:.1f}GB 남음 (최소 1GB 필요)"
            return False
        self._details["디스크 공간"] = f"{free_gb:.1f}GB 여유"
        return True

    async def _check_python_version(self) -> bool:
        ver = sys.version_info
        if ver >= (3, 12):
            self._details["Python 버전"] = f"{ver.major}.{ver.minor}.{ver.micro}"
            return True
        self._details["Python 버전"] = f"{ver.major}.{ver.minor} (3.12+ 필요)"
        return False
