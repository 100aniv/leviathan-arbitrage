"""LEVIATHAN Infrastructure Telegram Bot.

US-291-b: 인프라 모니터링 전용 봇.
환경변수: INFRA_TELEGRAM_BOT_TOKEN, INFRA_TELEGRAM_CHAT_ID, INFRA_TELEGRAM_ENABLED
"""
from __future__ import annotations

import asyncio
import subprocess
from typing import Any

import structlog

from src.infra.telegram_bot_base import TelegramBotBase

logger = structlog.get_logger(__name__)


class InfraTelegramBot(TelegramBotBase):
    """인프라 모니터링 전용 Telegram 봇."""

    def __init__(self) -> None:
        super().__init__(
            token_env="INFRA_TELEGRAM_BOT_TOKEN",
            chat_id_env="INFRA_TELEGRAM_CHAT_ID",
            enabled_env="INFRA_TELEGRAM_ENABLED",
            bot_name="LEVIATHAN-INFRA",
        )
        self._startup_checker: Any = None
        self._monitor_daemon: Any = None

        self.register_command("/health", self._cmd_health)
        self.register_command("/docker", self._cmd_docker)
        self.register_command("/checklist", self._cmd_checklist)
        self.register_command("/help", self._cmd_help)

    def set_startup_checker(self, checker: Any) -> None:
        """StartupChecker 인스턴스 주입."""
        self._startup_checker = checker

    def set_monitor_daemon(self, daemon: Any) -> None:
        """MonitorDaemon 인스턴스 주입 (재사용)."""
        self._monitor_daemon = daemon

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    async def _cmd_health(self, text: str, chat_id: int, message: dict) -> str:
        """Redis/DB/Engine 상태 조회 (주입된 MonitorDaemon 재사용)."""
        try:
            daemon = self._monitor_daemon
            if daemon is None:
                from src.infra.monitor_daemon import MonitorDaemon
                daemon = MonitorDaemon()
            results = await daemon.check_all()
            lines = ["<b>인프라 헬스체크</b>"]
            for service, ok in results.items():
                icon = "🟢" if ok else "🔴"
                lines.append(f"{icon} {service}: {'OK' if ok else 'FAIL'}")
            return "\n".join(lines)
        except Exception as exc:
            logger.error("infra_bot_health_error", error=str(exc))
            return f"헬스체크 오류: {exc}"

    async def _cmd_docker(self, text: str, chat_id: int, message: dict) -> str:
        """Docker 컨테이너 상태 조회 (async subprocess)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "compose", "ps",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
            output = (stdout or stderr or b"(no output)").decode().strip()
            if len(output) > 3500:
                output = output[:3500] + "\n...(truncated)"
            return f"<b>Docker 상태</b>\n<pre>{output}</pre>"
        except asyncio.TimeoutError:
            return "Docker 조회 타임아웃 (15s)"
        except FileNotFoundError:
            return "docker 명령을 찾을 수 없습니다"
        except Exception as exc:
            logger.error("infra_bot_docker_error", error=str(exc))
            return f"Docker 조회 오류: {exc}"

    async def _cmd_checklist(self, text: str, chat_id: int, message: dict) -> str:
        """StartupChecker 결과 표시."""
        if self._startup_checker is None:
            return "체크리스트 미설정"
        try:
            if asyncio.iscoroutinefunction(getattr(self._startup_checker, "run_all", None)):
                results = await self._startup_checker.run_all()
            else:
                results = self._startup_checker.run_all()
            if isinstance(results, dict):
                lines = ["<b>StartupChecker</b>"]
                for item, ok in results.items():
                    icon = "✅" if ok else "❌"
                    lines.append(f"{icon} {item}")
                return "\n".join(lines)
            return f"<b>StartupChecker</b>\n{results}"
        except Exception as exc:
            logger.error("infra_bot_checklist_error", error=str(exc))
            return f"체크리스트 오류: {exc}"

    async def _cmd_help(self, text: str, chat_id: int, message: dict) -> str:
        """도움말."""
        return (
            "<b>LEVIATHAN-INFRA 봇 명령어</b>\n"
            "/health — Redis/DB/Engine 헬스체크\n"
            "/docker — Docker 컨테이너 상태\n"
            "/checklist — 시작 체크리스트\n"
            "/help — 이 도움말"
        )

    # ------------------------------------------------------------------
    # Proactive alert helpers
    # ------------------------------------------------------------------

    async def send_infra_alert(self, service: str, status: str, error: str) -> None:
        """인프라 장애 알림."""
        text = (
            f"🔴 <b>인프라 장애</b>\n"
            f"서비스: {service}\n"
            f"상태: {status}\n"
            f"오류: {error}"
        )
        await self.send_message(text)

    async def send_recovery_alert(self, service: str) -> None:
        """복구 알림."""
        text = f"🟢 <b>복구 완료</b>\n서비스: {service}"
        await self.send_message(text)
