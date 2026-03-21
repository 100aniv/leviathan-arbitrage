"""LEVIATHAN Development Telegram Bot.

US-291-d: 개발/워크플로우 전용 봇.
환경변수: DEV_TELEGRAM_BOT_TOKEN (fallback: WORKFLOW_TELEGRAM_BOT_TOKEN),
          DEV_TELEGRAM_CHAT_ID   (fallback: WORKFLOW_TELEGRAM_CHAT_ID),
          DEV_TELEGRAM_ENABLED   (fallback: WORKFLOW_TELEGRAM_ENABLED, default "false")
"""
from __future__ import annotations

import os
import pathlib

import structlog

from src.infra.telegram_bot_base import TelegramBotBase

logger = structlog.get_logger(__name__)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SSOT_PATH = _REPO_ROOT / "SSOT.md"


class DevTelegramBot(TelegramBotBase):
    """개발/워크플로우 전용 Telegram 봇."""

    def __init__(self) -> None:
        token = os.getenv("DEV_TELEGRAM_BOT_TOKEN") or os.getenv("WORKFLOW_TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("DEV_TELEGRAM_CHAT_ID") or os.getenv("WORKFLOW_TELEGRAM_CHAT_ID", "")
        enabled_str = os.getenv("DEV_TELEGRAM_ENABLED") or os.getenv("WORKFLOW_TELEGRAM_ENABLED", "false")

        super().__init__(
            bot_token=token,
            chat_id=chat_id,
            enabled=enabled_str.lower() == "true",
            bot_name="LEVIATHAN-DEV",
        )

        self.register_command("/phase", self._cmd_phase)
        self.register_command("/tests", self._cmd_tests)
        self.register_command("/errors", self._cmd_errors)
        self.register_command("/help", self._cmd_help)

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    async def _cmd_phase(self, text: str, chat_id: int, message: dict) -> str:
        """현재 Phase 상태 (SSOT.md §2)."""
        try:
            if _SSOT_PATH.exists():
                content = _SSOT_PATH.read_text(encoding="utf-8")
                # §2 섹션 추출 (간단 파싱)
                lines = content.splitlines()
                in_section = False
                excerpt: list[str] = []
                for line in lines:
                    if line.startswith("## §2") or line.startswith("## 2"):
                        in_section = True
                    elif in_section and line.startswith("## ") and not (
                        line.startswith("## §2") or line.startswith("## 2")
                    ):
                        break
                    if in_section:
                        excerpt.append(line)
                    if len(excerpt) > 20:
                        break
                if excerpt:
                    body = "\n".join(excerpt[:20])
                    if len(body) > 3000:
                        body = body[:3000] + "\n...(truncated)"
                    return f"<b>Phase 상태 (SSOT.md §2)</b>\n<pre>{body}</pre>"
            return "SSOT.md 파일을 찾을 수 없습니다"
        except Exception as exc:
            logger.error("dev_bot_phase_error", error=str(exc))
            return f"Phase 조회 오류: {exc}"

    async def _cmd_tests(self, text: str, chat_id: int, message: dict) -> str:
        """최근 테스트 결과 요약."""
        # pytest --tb=no -q 결과 파일 또는 project-memory 활용
        prd_path = _REPO_ROOT / ".omc" / "prd.json"
        try:
            if prd_path.exists():
                import json
                data = json.loads(prd_path.read_text(encoding="utf-8"))
                us_list = data if isinstance(data, list) else data.get("user_stories", [])
                total = len(us_list)
                passed = sum(1 for us in us_list if us.get("passes") is True)
                failed = total - passed
                return (
                    f"<b>PRD 테스트 요약</b>\n"
                    f"총 US: {total}\n"
                    f"✅ passes:true: {passed}\n"
                    f"❌ passes:false: {failed}"
                )
        except Exception as exc:
            logger.error("dev_bot_tests_error", error=str(exc))
        return "테스트 결과 파일을 찾을 수 없습니다 (prd.json)"

    async def _cmd_errors(self, text: str, chat_id: int, message: dict) -> str:
        """최근 에러 로그 요약 (structlog ERROR 레벨)."""
        # structlog는 런타임 메모리 기반이므로 간단 메시지 반환
        return (
            "<b>에러 로그</b>\n"
            "실시간 에러는 structlog 출력 확인:\n"
            "<code>docker compose logs engine --tail=50 | grep ERROR</code>"
        )

    async def _cmd_help(self, text: str, chat_id: int, message: dict) -> str:
        """도움말."""
        return (
            "<b>LEVIATHAN-DEV 봇 명령어</b>\n"
            "/phase — 현재 Phase 상태 (SSOT.md §2)\n"
            "/tests — 최근 테스트 결과 요약\n"
            "/errors — 최근 에러 로그 안내\n"
            "/help — 이 도움말"
        )

    # ------------------------------------------------------------------
    # Proactive notification helpers
    # ------------------------------------------------------------------

    async def send_phase_notification(self, phase: str, status: str, details: str = "") -> None:
        """Phase 완료/실패 알림."""
        icon = "✅" if status.lower() in ("complete", "pass", "완료") else "❌"
        text = f"{icon} <b>Phase {phase}</b> — {status}"
        if details:
            text += f"\n{details}"
        await self.send_message(text)

    async def send_build_result(
        self, test_count: int, passed: int, failed: int, duration: float
    ) -> None:
        """빌드/테스트 결과 알림."""
        icon = "✅" if failed == 0 else "❌"
        text = (
            f"{icon} <b>테스트 결과</b>\n"
            f"총: {test_count} | 통과: {passed} | 실패: {failed}\n"
            f"소요: {duration:.1f}s"
        )
        await self.send_message(text)

    async def send_error_report(
        self, component: str, error: str, traceback_str: str = ""
    ) -> None:
        """에러 리포트."""
        text = f"🔴 <b>에러</b> [{component}]\n{error}"
        if traceback_str:
            tb = traceback_str[-800:] if len(traceback_str) > 800 else traceback_str
            text += f"\n<pre>{tb}</pre>"
        await self.send_message(text)

    # ------------------------------------------------------------------
    # WorkflowTelegramAlerter 어댑터 메서드
    # ------------------------------------------------------------------

    async def send_phase_complete(self, phase: str, result: dict) -> None:
        """WorkflowTelegramAlerter.send_phase_complete() 대체."""
        status = "완료" if result.get("success", False) else "실패"
        details = result.get("summary", "")
        await self.send_phase_notification(phase, status, details)

    async def send_l5_escalation(self, issue: str) -> None:
        """L5 에스컬레이션 알림 — 사장님 승인 요청."""
        text = (
            "🚨 <b>L5 에스컬레이션</b>\n"
            f"{issue}\n"
            "사장님 승인이 필요합니다."
        )
        await self.send_message(text)
