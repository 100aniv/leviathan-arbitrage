"""LEVIATHAN Development Telegram Bot.

US-291-d: 개발/워크플로우 전용 봇.
Phase S20-B: 원격 개발 제어 확장 (4개 → 14개 명령어 + 자동 알림).
Phase S20-C: /engine 명령 InfraBot으로 이동 (역할 재정의).

환경변수: DEV_TELEGRAM_BOT_TOKEN (fallback: WORKFLOW_TELEGRAM_BOT_TOKEN),
          DEV_TELEGRAM_CHAT_ID   (fallback: WORKFLOW_TELEGRAM_CHAT_ID),
          DEV_TELEGRAM_ENABLED   (fallback: WORKFLOW_TELEGRAM_ENABLED, default "false")
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import re
import time

import structlog

from src.infra.telegram_bot_base import InlineKeyboard, TelegramBotBase

logger = structlog.get_logger(__name__)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_ENGINE_ROOT = _REPO_ROOT / "engine"
_SSOT_PATH = _REPO_ROOT / "SSOT.md"
_OMC_STATE = _REPO_ROOT / ".omc" / "state"

# Whitelisted commands for /cmd — security restriction
_CMD_WHITELIST: list[list[str]] = [
    ["python", "-m", "pytest", "tests/", "-x", "--tb=short"],
    ["git", "status", "-s"],
    ["git", "log", "--oneline", "-10"],
    ["git", "diff", "--stat"],
    ["docker", "compose", "ps"],
    ["python", "-m", "src.main"],
]

# Prefixes allowed for /cmd (partial match)
_CMD_PREFIX_WHITELIST: list[str] = [
    "git status",
    "git log",
    "git diff",
    "docker compose ps",
    "docker compose logs",
]


class DevTelegramBot(TelegramBotBase):
    """개발/워크플로우 전용 Telegram 봇 — 원격 제어 지원."""

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

        self._pending_deploys: dict[int, float] = {}
        self._pending_approvals: dict[int, str] = {}
        self._shadow_process: asyncio.subprocess.Process | None = None

        # Register all commands
        self.register_command("/phase", self._cmd_phase)
        self.register_command("/tests", self._cmd_tests)
        self.register_command("/errors", self._cmd_errors)
        self.register_command("/session", self._cmd_session)
        self.register_command("/cmd", self._cmd_exec)
        self.register_command("/test", self._cmd_test)
        self.register_command("/shadow", self._cmd_shadow)
        self.register_command("/git", self._cmd_git)
        self.register_command("/deploy", self._cmd_deploy)
        self.register_command("/logs", self._cmd_logs)
        self.register_command("/approve", self._cmd_approve)
        self.register_command("/reject", self._cmd_reject)
        self.register_command("/progress", self._cmd_progress)
        self.register_command("/env", self._cmd_env)
        self.register_command("/engine", self._cmd_engine_stub)
        self.register_command("/go", self._cmd_go)
        self.register_command("/help", self._cmd_help)

        # Register callbacks
        self.register_callback("deploy_", self._cb_deploy)
        self.register_callback("approve_", self._cb_approve)

    # ------------------------------------------------------------------
    # Original commands (enhanced)
    # ------------------------------------------------------------------

    async def _cmd_phase(self, text: str, chat_id: int, message: dict) -> str:
        """현재 Phase 상태 (SSOT.md 핵심 추출)."""
        try:
            if _SSOT_PATH.exists():
                content = _SSOT_PATH.read_text(encoding="utf-8")
                lines = content.splitlines()
                phase_info: dict[str, str] = {}
                for line in lines:
                    stripped = line.strip()
                    if stripped.startswith("**Phase**:"):
                        phase_info["phase"] = stripped.replace("**Phase**:", "").strip()
                    elif stripped.startswith("**Tests**:"):
                        phase_info["tests"] = stripped.replace("**Tests**:", "").strip()
                    elif stripped.startswith("**TF Status**:"):
                        phase_info["tf"] = stripped.replace("**TF Status**:", "").strip()
                    elif stripped.startswith("**Next**:"):
                        phase_info["next"] = stripped.replace("**Next**:", "").strip()

                if phase_info:
                    return (
                        "📋 Phase 현황\n"
                        "━━━━━━━━━━━━━━━\n"
                        f"📌 현재: {phase_info.get('phase', 'N/A')}\n"
                        f"🧪 테스트: {phase_info.get('tests', 'N/A')}\n"
                        f"🏁 TF: {phase_info.get('tf', 'N/A')}\n"
                        f"➡️ 다음: {phase_info.get('next', 'N/A')}"
                    )
            return "📋 SSOT.md 파일을 찾을 수 없습니다"
        except Exception as exc:
            logger.error("dev_bot_phase_error", error=str(exc))
            return f"📋 Phase 조회 오류: {exc}"

    async def _cmd_tests(self, text: str, chat_id: int, message: dict) -> str:
        """PRD 진행률 요약."""
        prd_path = _REPO_ROOT / ".omc" / "prd.json"
        try:
            if prd_path.exists():
                data = json.loads(prd_path.read_text(encoding="utf-8"))
                us_list = data if isinstance(data, list) else data.get("user_stories", [])
                total = len(us_list)
                passed = sum(1 for us in us_list if us.get("passes") is True)
                failed = total - passed
                pct = (passed / total * 100) if total > 0 else 0
                bar_len = 20
                filled = int(bar_len * passed / total) if total > 0 else 0
                bar = "█" * filled + "░" * (bar_len - filled)
                return (
                    "🧪 PRD 진행률\n"
                    "━━━━━━━━━━━━━━━\n"
                    f"{bar} {pct:.1f}%\n\n"
                    f"✅ 완료: {passed}/{total}\n"
                    f"❌ 미완: {failed}개"
                )
        except Exception as exc:
            logger.error("dev_bot_tests_error", error=str(exc))
        return "🧪 prd.json을 찾을 수 없습니다"

    async def _cmd_errors(self, text: str, chat_id: int, message: dict) -> str:
        """최근 에러 로그 조회."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "tail", "-50", str(_ENGINE_ROOT / "engine.log"),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            raw = (stdout or b"").decode(errors="replace")
            error_lines = [l for l in raw.splitlines() if "ERROR" in l or "CRITICAL" in l]
            if error_lines:
                recent = error_lines[-10:]
                return (
                    "🔍 최근 에러\n"
                    "━━━━━━━━━━━━━━━\n"
                    + "\n".join(f"  {l[:120]}" for l in recent)
                )
            return "🔍 최근 에러 없음 ✅"
        except Exception:
            return (
                "🔍 에러 로그 조회\n"
                "━━━━━━━━━━━━━━━\n"
                "터미널에서 확인:\n"
                "docker compose logs engine --tail=50 | grep ERROR"
            )

    # ------------------------------------------------------------------
    # New commands: Remote control
    # ------------------------------------------------------------------

    async def _cmd_session(self, text: str, chat_id: int, message: dict) -> str:
        """현재 개발 세션 상태 (.omc/state/)."""
        try:
            lines = ["📡 세션 상태\n━━━━━━━━━━━━━━━"]

            # Check for active state files
            if _OMC_STATE.exists():
                state_files = sorted(_OMC_STATE.glob("*-state.json"))
                if state_files:
                    for sf in state_files[:5]:
                        try:
                            data = json.loads(sf.read_text(encoding="utf-8"))
                            mode = data.get("mode", sf.stem)
                            status = data.get("status", "unknown")
                            lines.append(f"  📦 {mode}: {status}")
                        except Exception:
                            lines.append(f"  📦 {sf.stem}: (읽기 실패)")
                else:
                    lines.append("  활성 세션 없음")
            else:
                lines.append("  .omc/state/ 없음")

            # Shadow process status
            if self._shadow_process and self._shadow_process.returncode is None:
                lines.append("\n🔄 Shadow 실행 중 (PID: {})".format(self._shadow_process.pid))

            return "\n".join(lines)
        except Exception as exc:
            return f"📡 세션 조회 오류: {exc}"

    async def _cmd_exec(self, text: str, chat_id: int, message: dict) -> str:
        """원격 명령 실행 (화이트리스트 제한)."""
        parts = text.strip().split(maxsplit=1)
        if len(parts) < 2:
            allowed = "\n".join(f"  {p}" for p in _CMD_PREFIX_WHITELIST)
            return f"🔧 사용법: /cmd <명령>\n\n허용 명령어:\n{allowed}"

        cmd_str = parts[1].strip()

        # Security: check whitelist
        # Security H-1: Use exact whitelist matching + subprocess_exec (no shell)
        import shlex
        _CMD_EXACT_MAP: dict[str, list[str]] = {
            "git status": ["git", "status", "-s"],
            "git log": ["git", "log", "--oneline", "-10"],
            "git diff": ["git", "diff", "--stat"],
            "docker compose ps": ["docker", "compose", "ps"],
            "docker compose logs": ["docker", "compose", "logs", "--tail=50"],
        }
        matched_cmd = None
        for key, argv in _CMD_EXACT_MAP.items():
            if cmd_str.strip() == key or cmd_str.strip().startswith(key + " "):
                matched_cmd = argv
                break

        # Fallback: exact match against _CMD_WHITELIST
        if matched_cmd is None:
            parts = shlex.split(cmd_str)
            if parts in _CMD_WHITELIST:
                matched_cmd = parts

        if matched_cmd is None:
            return "🚫 허용되지 않은 명령어입니다.\n/cmd 로 허용 목록을 확인하세요."

        try:
            proc = await asyncio.create_subprocess_exec(
                *matched_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(_REPO_ROOT),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            output = (stdout or b"").decode(errors="replace").strip()
            err = (stderr or b"").decode(errors="replace").strip()

            result_lines = [f"🔧 {cmd_str}\n━━━━━━━━━━━━━━━"]
            if output:
                # Truncate to 3000 chars for Telegram limit
                result_lines.append(output[:3000])
            if err:
                result_lines.append(f"\n⚠️ stderr:\n{err[:500]}")
            result_lines.append(f"\n📌 exit code: {proc.returncode}")
            return "\n".join(result_lines)
        except asyncio.TimeoutError:
            return f"⏰ 명령 실행 타임아웃 (30s): {cmd_str}"
        except Exception as exc:
            return f"🔧 명령 실행 오류: {exc}"

    async def _cmd_test(self, text: str, chat_id: int, message: dict) -> str:
        """pytest 원격 실행."""
        await self.send_message("🧪 pytest 실행 중... (최대 5분)", chat_id=str(chat_id))
        try:
            proc = await asyncio.create_subprocess_exec(
                "python", "-m", "pytest", "tests/", "-x", "--tb=short", "-q",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(_ENGINE_ROOT),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
            output = (stdout or b"").decode(errors="replace").strip()

            # Extract summary line (e.g., "5080 passed, 12 skipped in 45.2s")
            summary = ""
            for line in reversed(output.splitlines()):
                if "passed" in line or "failed" in line or "error" in line:
                    summary = line.strip()
                    break

            icon = "✅" if proc.returncode == 0 else "❌"
            result = f"{icon} pytest 결과\n━━━━━━━━━━━━━━━\n"
            if summary:
                result += f"{summary}\n"
            result += f"Exit code: {proc.returncode}"

            # Show last 20 lines if failed
            if proc.returncode != 0:
                tail = "\n".join(output.splitlines()[-20:])
                result += f"\n\n{tail[:2000]}"

            return result
        except asyncio.TimeoutError:
            return "⏰ pytest 타임아웃 (5분)"
        except Exception as exc:
            return f"🧪 pytest 오류: {exc}"

    async def _cmd_shadow(self, text: str, chat_id: int, message: dict) -> str:
        """Shadow 10min 시작 (백그라운드)."""
        if self._shadow_process and self._shadow_process.returncode is None:
            return "🔄 Shadow가 이미 실행 중입니다 (PID: {})".format(self._shadow_process.pid)

        try:
            self._shadow_process = await asyncio.create_subprocess_exec(
                "timeout", "600", "python", "-m", "src.main",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(_ENGINE_ROOT),
                env={**os.environ, "ENGINE_ENV": "dev"},
            )
            # Monitor in background
            asyncio.create_task(self._monitor_shadow(chat_id))
            return f"🔄 Shadow 시작됨 (PID: {self._shadow_process.pid})\n10분 후 자동 종료 + 결과 알림"
        except Exception as exc:
            return f"🔄 Shadow 시작 오류: {exc}"

    async def _monitor_shadow(self, chat_id: int) -> None:
        """Shadow 프로세스 종료 대기 후 결과 전송."""
        if not self._shadow_process:
            return
        try:
            stdout, stderr = await self._shadow_process.communicate()
            output = (stdout or b"").decode(errors="replace")
            rc = self._shadow_process.returncode

            icon = "✅" if rc == 0 else "❌"
            # Extract key metrics from output
            metrics = []
            for line in output.splitlines():
                lower = line.lower()
                if any(k in lower for k in ["pnl", "trade", "win_rate", "drawdown", "shadow"]):
                    metrics.append(line.strip()[:120])

            result = f"{icon} Shadow 완료 (exit: {rc})\n━━━━━━━━━━━━━━━"
            if metrics:
                result += "\n" + "\n".join(metrics[-10:])
            else:
                result += "\n상세 로그는 /logs 로 확인"

            await self.send_message(result, chat_id=str(chat_id))
        except Exception as exc:
            await self.send_message(f"❌ Shadow 모니터링 오류: {exc}", chat_id=str(chat_id))
        finally:
            self._shadow_process = None

    async def _cmd_git(self, text: str, chat_id: int, message: dict) -> str:
        """git status + log 조회."""
        try:
            # Run status and log in parallel
            status_proc = await asyncio.create_subprocess_exec(
                "git", "status", "-s",
                stdout=asyncio.subprocess.PIPE,
                cwd=str(_REPO_ROOT),
            )
            log_proc = await asyncio.create_subprocess_exec(
                "git", "log", "--oneline", "-5",
                stdout=asyncio.subprocess.PIPE,
                cwd=str(_REPO_ROOT),
            )

            status_out, _ = await asyncio.wait_for(status_proc.communicate(), timeout=10)
            log_out, _ = await asyncio.wait_for(log_proc.communicate(), timeout=10)

            status = (status_out or b"").decode().strip() or "(clean)"
            log = (log_out or b"").decode().strip()

            # Count changed files
            changed = len([l for l in status.splitlines() if l.strip()])
            status_display = status[:1500] if status != "(clean)" else status

            return (
                f"📂 Git 상태 ({changed} files changed)\n"
                "━━━━━━━━━━━━━━━\n"
                f"{status_display}\n\n"
                "📝 최근 커밋:\n"
                f"{log}"
            )
        except Exception as exc:
            return f"📂 Git 조회 오류: {exc}"

    async def _cmd_deploy(self, text: str, chat_id: int, message: dict) -> str:
        """git push (2단계 인라인 키보드 확인)."""
        kb = InlineKeyboard()
        kb.row(("✅ Push 실행", "deploy_confirm"), ("❌ 취소", "deploy_cancel"))
        await self.send_message(
            "🚀 git push 확인\n\n정말로 원격 저장소에 push하시겠습니까?",
            reply_markup=kb.to_markup(),
            chat_id=str(chat_id),
        )
        self._pending_deploys[chat_id] = time.time()
        return None  # type: ignore[return-value]

    async def _cb_deploy(self, callback_query: dict) -> str | None:
        """Deploy 인라인 키보드 콜백."""
        data = callback_query["data"]
        msg = callback_query["message"]
        chat_id: int = msg["chat"]["id"]
        message_id: int = msg["message_id"]

        if data == "deploy_confirm":
            pending_ts = self._pending_deploys.get(chat_id)
            if pending_ts is not None and (time.time() - pending_ts) < 60:
                del self._pending_deploys[chat_id]
                await self.edit_message(chat_id, message_id, "🚀 git push 실행 중...")
                try:
                    proc = await asyncio.create_subprocess_exec(
                        "git", "push",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        cwd=str(_REPO_ROOT),
                    )
                    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
                    output = (stderr or stdout or b"").decode().strip()

                    if proc.returncode == 0:
                        # Get latest commit hash
                        hash_proc = await asyncio.create_subprocess_exec(
                            "git", "rev-parse", "--short", "HEAD",
                            stdout=asyncio.subprocess.PIPE,
                            cwd=str(_REPO_ROOT),
                        )
                        hash_out, _ = await hash_proc.communicate()
                        commit_hash = (hash_out or b"").decode().strip()

                        await self.edit_message(
                            chat_id, message_id,
                            f"✅ Push 완료: {commit_hash}\n{output[:500]}",
                        )
                        return "Push 완료"
                    else:
                        await self.edit_message(
                            chat_id, message_id,
                            f"❌ Push 실패 (exit: {proc.returncode})\n{output[:500]}",
                        )
                        return "Push 실패"
                except asyncio.TimeoutError:
                    await self.edit_message(chat_id, message_id, "⏰ Push 타임아웃")
                    return "타임아웃"
                except Exception as exc:
                    await self.edit_message(chat_id, message_id, f"❌ Push 오류: {exc}")
                    return "오류"
            else:
                self._pending_deploys.pop(chat_id, None)
                await self.edit_message(chat_id, message_id, "⏰ 시간 초과 — 다시 /deploy")
                return "시간 초과"
        elif data == "deploy_cancel":
            self._pending_deploys.pop(chat_id, None)
            await self.edit_message(chat_id, message_id, "✅ 취소됨")
            return "취소됨"
        return None

    async def _cmd_logs(self, text: str, chat_id: int, message: dict) -> str:
        """최근 엔진 로그 (tail -30, 에러 하이라이트)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "compose", "logs", "engine", "--tail=30", "--no-color",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(_REPO_ROOT),
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            raw = (stdout or b"").decode(errors="replace").strip()

            if not raw:
                return "📜 로그 없음 (Docker engine 미실행?)"

            # Highlight errors
            lines = []
            for line in raw.splitlines()[-30:]:
                if "ERROR" in line or "CRITICAL" in line:
                    lines.append(f"🔴 {line[:150]}")
                elif "WARNING" in line:
                    lines.append(f"🟡 {line[:150]}")
                else:
                    lines.append(f"  {line[:150]}")

            return "📜 최근 로그\n━━━━━━━━━━━━━━━\n" + "\n".join(lines[-20:])
        except FileNotFoundError:
            return "📜 docker 명령을 찾을 수 없습니다"
        except Exception as exc:
            return f"📜 로그 조회 오류: {exc}"

    async def _cmd_approve(self, text: str, chat_id: int, message: dict) -> str:
        """Go/No-Go 승인."""
        parts = text.strip().split(maxsplit=1)
        phase = parts[1] if len(parts) > 1 else "current"
        self._pending_approvals[chat_id] = phase
        return f"✅ Phase {phase} 승인 완료\n기록됨: {time.strftime('%Y-%m-%d %H:%M:%S')}"

    async def _cmd_reject(self, text: str, chat_id: int, message: dict) -> str:
        """Go/No-Go 거부."""
        parts = text.strip().split(maxsplit=1)
        reason = parts[1] if len(parts) > 1 else "사유 미입력"
        return f"❌ 거부됨\n사유: {reason}\n기록됨: {time.strftime('%Y-%m-%d %H:%M:%S')}"

    async def _cmd_progress(self, text: str, chat_id: int, message: dict) -> str:
        """PRD 진행률 프로그레스 바 + Phase별 breakdown."""
        prd_path = _REPO_ROOT / ".omc" / "prd.json"
        try:
            if not prd_path.exists():
                return "📊 prd.json을 찾을 수 없습니다"

            data = json.loads(prd_path.read_text(encoding="utf-8"))
            us_list = data if isinstance(data, list) else data.get("user_stories", [])
            total = len(us_list)
            passed = sum(1 for us in us_list if us.get("passes") is True)
            pct = (passed / total * 100) if total > 0 else 0

            # Progress bar
            bar_len = 20
            filled = int(bar_len * passed / total) if total > 0 else 0
            bar = "█" * filled + "░" * (bar_len - filled)

            lines = [
                "📊 전체 진행률\n━━━━━━━━━━━━━━━",
                f"{bar} {pct:.1f}%",
                f"✅ {passed}/{total} US 완료\n",
            ]

            # Phase breakdown
            phase_stats: dict[str, list[int]] = {}  # phase -> [passed, total]
            for us in us_list:
                phase = us.get("phase", "unknown")
                if phase not in phase_stats:
                    phase_stats[phase] = [0, 0]
                phase_stats[phase][1] += 1
                if us.get("passes") is True:
                    phase_stats[phase][0] += 1

            # Show incomplete phases
            incomplete = {p: s for p, s in phase_stats.items() if s[0] < s[1]}
            if incomplete:
                lines.append("📋 미완료 Phase:")
                for phase, (p, t) in sorted(incomplete.items()):
                    lines.append(f"  {phase}: {p}/{t}")

            return "\n".join(lines)
        except Exception as exc:
            return f"📊 진행률 오류: {exc}"

    async def _cmd_env(self, text: str, chat_id: int, message: dict) -> str:
        """.env 변수 확인 (민감정보 마스킹)."""
        env_path = _REPO_ROOT / ".env"
        try:
            if not env_path.exists():
                return "🔑 .env 파일 없음"

            content = env_path.read_text(encoding="utf-8")
            lines = ["🔑 환경변수 (.env)\n━━━━━━━━━━━━━━━"]
            sensitive_keys = {"TOKEN", "SECRET", "PASSWORD", "KEY", "API"}

            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue

                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")

                # Mask sensitive values
                is_sensitive = any(s in key.upper() for s in sensitive_keys)
                if is_sensitive and len(value) > 4:
                    display = value[:4] + "****"
                else:
                    display = value

                lines.append(f"  {key}={display}")

            return "\n".join(lines[:40])  # Max 40 vars
        except Exception as exc:
            return f"🔑 .env 조회 오류: {exc}"

    async def _cmd_engine_stub(self, text: str, chat_id: int, message: dict) -> str:
        """엔진 제어는 InfraBot으로 이동됨."""
        return "🔧 /engine 명령은 InfraBot으로 이동했습니다.\nInfraBot에서 /engine을 사용하세요."

    async def _cmd_help(self, text: str, chat_id: int, message: dict) -> str:
        """도움말."""
        return (
            "🛠️ LEVIATHAN-DEV 봇\n"
            "━━━━━━━━━━━━━━━\n"
            "📋 조회\n"
            "  /phase — Phase 현황\n"
            "  /tests — PRD 진행률\n"
            "  /progress — 진행률 상세\n"
            "  /session — 세션 상태\n"
            "  /errors — 에러 로그\n"
            "  /env — 환경변수 (마스킹)\n\n"
            "🔧 제어\n"
            "  /test — pytest 원격 실행\n"
            "  /shadow — Shadow 10min\n"
            "  /cmd &lt;명령&gt; — 원격 명령\n"
            "  /git — Git 상태\n"
            "  /logs — 최근 로그\n"
            "  /deploy — Git Push (확인)\n"
            "  /go [메시지] — Claude CLI 수동 재개\n\n"
            "✅ 승인\n"
            "  /approve [phase] — 승인\n"
            "  /reject [사유] — 거부\n\n"
            "❓ /help — 이 도움말"
        )

    # ------------------------------------------------------------------
    # Proactive notification helpers (Push)
    # ------------------------------------------------------------------

    async def send_phase_complete_with_approval(
        self, phase: str, summary: str = ""
    ) -> None:
        """Phase 완료 알림 + Go/No-Go 인라인 버튼."""
        kb = InlineKeyboard()
        kb.row(("✅ 승인", f"approve_{phase}"), ("❌ 거부", f"approve_reject_{phase}"))
        text = f"🏁 Phase {phase} 완료\n━━━━━━━━━━━━━━━"
        if summary:
            text += f"\n{summary}"
        text += "\n\nGo/No-Go 결정을 선택하세요."
        await self.send_message(text, reply_markup=kb.to_markup())

    async def _cb_approve(self, callback_query: dict) -> str | None:
        """Go/No-Go 인라인 버튼 콜백."""
        data = callback_query["data"]
        msg = callback_query["message"]
        chat_id = msg["chat"]["id"]
        message_id = msg["message_id"]

        if data.startswith("approve_reject_"):
            phase = data.replace("approve_reject_", "")
            await self.edit_message(
                chat_id, message_id,
                f"❌ Phase {phase} 거부됨\n/reject 로 사유를 입력하세요.",
            )
            return "거부"
        elif data.startswith("approve_"):
            phase = data.replace("approve_", "")
            await self.edit_message(
                chat_id, message_id,
                f"✅ Phase {phase} 승인 완료\n{time.strftime('%Y-%m-%d %H:%M:%S')}",
            )
            return "승인"
        return None

    async def send_phase_notification(self, phase: str, status: str, details: str = "") -> None:
        """Phase 완료/실패 알림."""
        icon = "✅" if status.lower() in ("complete", "pass", "완료") else "❌"
        text = f"{icon} Phase {phase} — {status}"
        if details:
            text += f"\n{details}"
        await self.send_message(text)

    async def send_test_failure(self, test_name: str, error: str) -> None:
        """테스트 실패 알림."""
        await self.send_message(
            f"❌ pytest FAIL: {test_name}\n━━━━━━━━━━━━━━━\n{error[:800]}"
        )

    async def send_build_error(self, error: str) -> None:
        """빌드 에러 알림."""
        await self.send_message(f"🔴 빌드 에러\n━━━━━━━━━━━━━━━\n{error[:800]}")

    async def send_build_result(
        self, test_count: int, passed: int, failed: int, duration: float
    ) -> None:
        """빌드/테스트 결과 알림."""
        icon = "✅" if failed == 0 else "❌"
        text = (
            f"{icon} 테스트 결과\n"
            f"총: {test_count} | 통과: {passed} | 실패: {failed}\n"
            f"소요: {duration:.1f}s"
        )
        await self.send_message(text)

    async def send_error_report(
        self, component: str, error: str, traceback_str: str = ""
    ) -> None:
        """에러 리포트."""
        text = f"🔴 에러 [{component}]\n{error}"
        if traceback_str:
            tb = traceback_str[-800:] if len(traceback_str) > 800 else traceback_str
            text += f"\n\n{tb}"
        await self.send_message(text)

    async def send_l5_escalation(self, issue: str) -> None:
        """L5 에스컬레이션 알림 — 사장님 승인 요청."""
        kb = InlineKeyboard()
        kb.row(("✅ 확인", "approve_l5_ack"))
        await self.send_message(
            f"🚨 L5 에스컬레이션\n━━━━━━━━━━━━━━━\n{issue}\n\n사장님 확인이 필요합니다.",
            reply_markup=kb.to_markup(),
        )

    async def send_shadow_result(self, result: dict) -> None:
        """Shadow 완료 알림."""
        pnl = result.get("total_pnl", 0.0)
        trades = result.get("trades_executed", 0)
        wr = result.get("win_rate", 0.0)
        mdd = result.get("max_drawdown_pct", 0.0)
        passed = result.get("passed", pnl > 0)
        icon = "✅ PASS" if passed else "❌ FAIL"
        await self.send_message(
            f"🔄 Shadow 결과: {icon}\n"
            "━━━━━━━━━━━━━━━\n"
            f"💰 PnL: ${pnl:+,.4f}\n"
            f"🔁 거래: {trades}건\n"
            f"🎯 승률: {wr*100:.1f}%\n"
            f"📉 MDD: {mdd*100:.2f}%"
        )

    # Security H-2: Fixed allowed messages for /go (no arbitrary input to tmux)
    _GO_ALLOWED_MSGS: list[str] = [
        "멈추지 말고 계속 진행해. Stage 전환 즉시 실행.",
        "계속 진행해.",
        "다음 Stage로 넘어가.",
    ]

    async def _cmd_go(self, text: str, chat_id: int, message: dict) -> str:
        """텔레그램에서 Claude CLI 수동 재개 (/go). 고정 메시지만 허용."""
        import subprocess

        parts = text.strip().split(maxsplit=1)
        # Only allow pre-defined messages (prevent tmux injection)
        if len(parts) > 1:
            idx = int(parts[1]) - 1 if parts[1].isdigit() else 0
        else:
            idx = 0
        resume_msg = self._GO_ALLOWED_MSGS[min(idx, len(self._GO_ALLOWED_MSGS) - 1)]

        tmux_session = os.getenv("WATCHDOG_TMUX_SESSION", "leviathan")
        tmux_pane = f"{tmux_session}:0.0"

        try:
            # Check if tmux session exists
            result = subprocess.run(
                ["tmux", "has-session", "-t", tmux_session],
                capture_output=True, timeout=5,
            )
            if result.returncode != 0:
                return f"tmux session '{tmux_session}' not found"

            # Send keys to tmux
            subprocess.run(
                ["tmux", "send-keys", "-t", tmux_pane, resume_msg, "Enter"],
                capture_output=True, timeout=5,
            )
            logger.info("telegram_go_command", session=tmux_session, msg=resume_msg[:50])
            return f"Sent to {tmux_session}: {resume_msg[:100]}"
        except Exception as exc:
            logger.error("telegram_go_failed", error=str(exc))
            return f"Failed: {exc}"

    async def send_git_push_complete(self, commit_hash: str, message: str) -> None:
        """git push 완료 알림."""
        await self.send_message(f"✅ Push 완료: {commit_hash}\n{message[:200]}")

    # ------------------------------------------------------------------
    # WorkflowTelegramAlerter adapter
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Watchdog — tmux 멈춤 감지 + 자동 재개 (핵심 기능)
    # ------------------------------------------------------------------

    _STALL_PATTERNS = re.compile(
        r"Worked for|Baked for|Churned for|Crunched for|Sautéed for"
    )
    _STAGE_PATTERNS = re.compile(
        r"Stage [ABC] 완료|Phase .+ 완료|PASS|pytest.*passed"
    )

    async def watchdog_loop(
        self,
        tmux_session: str = "leviathan",
        check_interval: float = 5.0,
        cooldown: float = 30.0,
        resume_delay: float = 2.0,
        resume_msg: str = "멈추지 말고 계속 진행해. Stage 전환 즉시 실행.",
        stall_timeout: float = 60.0,
    ) -> None:
        """tmux 세션 모니터링: 멈춤 감지 → 알림 → 자동 재개.

        `python -m src.infra.telegram_dev_bot` 실행 시 poll_loop와 병렬.
        시간 기반 멈춤 감지: 60초 이상 출력 무변화 → stall alert.
        _STALL_PATTERNS regex는 보조 fast-detect로 병행 유지.
        """
        import subprocess

        tmux_pane = f"{tmux_session}:0.0"
        last_stall = 0.0
        stall_count = 0
        last_output = ""
        last_change_time = time.monotonic()

        logger.info("watchdog_started", session=tmux_session, interval=check_interval)
        await self.send_message(
            f"🐕 Watchdog 시작\nSession: <code>{tmux_session}</code>\n"
            f"Check: {check_interval}s | Cooldown: {cooldown}s | StallTimeout: {stall_timeout}s"
        )

        while True:
            await asyncio.sleep(check_interval)
            try:
                # Check if tmux session exists
                result = subprocess.run(
                    ["tmux", "has-session", "-t", tmux_session],
                    capture_output=True, timeout=5,
                )
                if result.returncode != 0:
                    continue  # session not found, wait

                # Capture last 5 lines of tmux pane
                result = subprocess.run(
                    ["tmux", "capture-pane", "-t", tmux_pane, "-p", "-S", "-5"],
                    capture_output=True, text=True, timeout=5,
                )
                output = result.stdout.strip()

                # 출력이 변경되면 last_change_time 갱신
                if output and output != last_output:
                    last_change_time = time.monotonic()
                    last_output = output

                # 보조 fast-detect: _STALL_PATTERNS regex 매치 시 즉시 stall 처리
                fast_stall = bool(output) and bool(self._STALL_PATTERNS.search(output))

                # 시간 기반 stall 감지: 60초 이상 출력 무변화
                time_stall = bool(output) and (time.monotonic() - last_change_time > stall_timeout)

                if fast_stall or time_stall:
                    now = time.monotonic()
                    if now - last_stall < cooldown:
                        # Stage completion 체크는 cooldown 중에도 계속
                        pass
                    else:
                        last_stall = now
                        stall_count += 1
                        reason = "regex 패턴 감지" if fast_stall else f"{stall_timeout:.0f}초 무변화"
                        logger.warning("watchdog_stall_detected", count=stall_count, reason=reason)
                        await self.send_message(
                            f"🔴 <b>Claude CLI 멈춤 감지</b> (#{stall_count})\n"
                            f"사유: {reason}\n"
                            f"Action: {resume_delay}s 후 자동 재개"
                        )
                        await asyncio.sleep(resume_delay)
                        subprocess.run(
                            ["tmux", "send-keys", "-t", tmux_pane, resume_msg, "Enter"],
                            capture_output=True, timeout=5,
                        )
                        # stall 해소 후 last_change_time 리셋 (연속 알림 방지)
                        last_change_time = time.monotonic()
                        logger.info("watchdog_resume_sent", msg=resume_msg[:50])

                # Check for stage completion
                if output:
                    for match in self._STAGE_PATTERNS.finditer(output):
                        await self.send_message(f"📋 <b>Progress</b>: {match.group()}")

            except Exception as exc:
                logger.warning("watchdog_error", error=str(exc))

    async def send_phase_complete(self, phase: str, result: dict) -> None:
        """WorkflowTelegramAlerter.send_phase_complete() 대체."""
        status = "완료" if result.get("success", False) else "실패"
        details = result.get("summary", "")
        if result.get("success", False):
            await self.send_phase_complete_with_approval(phase, details)
        else:
            await self.send_phase_notification(phase, status, details)


# ======================================================================
# Standalone entry point: python -m src.infra.telegram_dev_bot
# Runs poll_loop (텔레그램 명령) + watchdog_loop (tmux 멈춤 감지) 병렬
# ======================================================================

async def _main() -> None:
    """Dev봇 독립 프로세스 — watchdog + 텔레그램 polling 동시 실행."""
    import signal as _signal

    bot = DevTelegramBot()
    if not bot.enabled:
        logger.error("DevTelegramBot disabled — set DEV_TELEGRAM_ENABLED=true")
        return

    tmux_session = os.getenv("WATCHDOG_TMUX_SESSION", "leviathan")
    check_interval = float(os.getenv("WATCHDOG_INTERVAL", "5"))
    cooldown = float(os.getenv("WATCHDOG_COOLDOWN", "30"))
    resume_delay = float(os.getenv("WATCHDOG_RESUME_DELAY", "2"))

    logger.info(
        "dev_bot_standalone_start",
        tmux=tmux_session,
        interval=check_interval,
    )

    loop = asyncio.get_event_loop()
    stop = asyncio.Event()

    def _shutdown(*_: object) -> None:
        logger.info("dev_bot_shutdown_signal")
        stop.set()

    for sig in (_signal.SIGINT, _signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown)

    poll_task = asyncio.create_task(bot.poll_loop(), name="dev_poll")
    watchdog_task = asyncio.create_task(
        bot.watchdog_loop(
            tmux_session=tmux_session,
            check_interval=check_interval,
            cooldown=cooldown,
            resume_delay=resume_delay,
        ),
        name="dev_watchdog",
    )

    await stop.wait()
    poll_task.cancel()
    watchdog_task.cancel()
    await bot.close()
    logger.info("dev_bot_stopped")


if __name__ == "__main__":
    asyncio.run(_main())
