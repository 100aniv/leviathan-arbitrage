"""LEVIATHAN Infrastructure Telegram Bot.

US-291-b: 인프라 모니터링 전용 봇.
Phase S20-B: /metrics, /resources, /restart 추가.
Phase S20-C: /engine 명령 DevBot에서 이동 (역할 재정의).

환경변수: INFRA_TELEGRAM_BOT_TOKEN, INFRA_TELEGRAM_CHAT_ID, INFRA_TELEGRAM_ENABLED
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import structlog

from src.infra.telegram_bot_base import InlineKeyboard, TelegramBotBase

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
        self._pending_restarts: dict[int, tuple[str, float]] = {}
        self._pending_engines: dict[int, float] = {}
        self._pending_closepositions: dict[int, float] = {}
        self._watchdog_enabled: bool = False
        self._watchdog_task: asyncio.Task | None = None
        self._redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

        self.register_command("/health", self._cmd_health)
        self.register_command("/docker", self._cmd_docker)
        self.register_command("/checklist", self._cmd_checklist)
        self.register_command("/metrics", self._cmd_metrics)
        self.register_command("/resources", self._cmd_resources)
        self.register_command("/restart", self._cmd_restart)
        self.register_command("/engine", self._cmd_engine)
        self.register_command("/watchdog", self._cmd_watchdog)
        self.register_command("/closepositions", self._cmd_closepositions)
        self.register_command("/help", self._cmd_help)

        self.register_callback("restart_", self._cb_restart)
        self.register_callback("engine_", self._cb_engine)
        self.register_callback("closepos_", self._cb_closepositions)

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
        """인프라 헬스체크 — 독립 모드 지원 (MonitorDaemon or HTTP 직접)."""
        lines = ["🏥 인프라 헬스체크\n━━━━━━━━━━━━━━━"]
        results: dict[str, bool] = {}

        # Try MonitorDaemon first (when running inside engine)
        if self._monitor_daemon is not None:
            try:
                results = await self._monitor_daemon.check_all()
            except Exception:
                pass

        # Standalone mode: HTTP direct checks
        if not results:
            import httpx
            from src.core.config_loader import get_config as _gc

            # Engine check
            engine_url = _gc("monitoring.engine_url", default="http://localhost:8000")
            try:
                async with httpx.AsyncClient(timeout=5.0) as c:
                    resp = await c.get(f"{engine_url}/health")
                    results["engine"] = resp.status_code == 200
            except Exception:
                results["engine"] = False

            # Redis check
            try:
                import redis.asyncio as aioredis
                redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
                r = aioredis.from_url(redis_url)
                pong = await r.ping()
                results["redis"] = bool(pong)
                await r.aclose()
            except Exception:
                results["redis"] = False

            # DB check
            try:
                db_url = os.getenv("DATABASE_URL", "")
                if db_url:
                    async with httpx.AsyncClient(timeout=5.0) as c:
                        # Use engine's /health which checks DB
                        pass  # Already checked via engine
                results.setdefault("database", results.get("engine", False))
            except Exception:
                results["database"] = False

        for service, ok in results.items():
            icon = "🟢" if ok else "🔴"
            lines.append(f"{icon} {service}: {'정상' if ok else '장애'}")
        all_ok = all(results.values()) if results else False
        lines.append(f"\n{'✅ 전체 정상' if all_ok else '⚠️ 일부 장애 감지'}")
        return "\n".join(lines)

    async def _cmd_docker(self, text: str, chat_id: int, message: dict) -> str:
        """Docker 컨테이너 상태 조회 (async subprocess)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "compose", "ps", "--format", "json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
            raw = (stdout or b"").decode().strip()
            if not raw:
                return "🐳 Docker 상태\n━━━━━━━━━━━━━━━\n컨테이너 없음"

            import json as _json
            lines = ["🐳 Docker 상태\n━━━━━━━━━━━━━━━"]
            for line in raw.splitlines():
                try:
                    c = _json.loads(line)
                    name = c.get("Name", c.get("Service", "?"))
                    state = c.get("State", c.get("Status", "unknown"))
                    health = c.get("Health", "")
                    if "running" in state.lower():
                        icon = "🟢"
                    elif "exited" in state.lower():
                        icon = "🔴"
                    else:
                        icon = "🟡"
                    status_text = state
                    if health:
                        status_text += f" ({health})"
                    lines.append(f"{icon} {name}: {status_text}")
                except _json.JSONDecodeError:
                    continue
            if len(lines) == 1:
                proc2 = await asyncio.create_subprocess_exec(
                    "docker", "compose", "ps", "--format",
                    "table {{.Name}}\t{{.Status}}",
                    stdout=asyncio.subprocess.PIPE,
                )
                stdout2, _ = await asyncio.wait_for(proc2.communicate(), timeout=10)
                plain = (stdout2 or b"").decode().strip()
                for row in plain.splitlines()[1:]:
                    parts = row.split("\t")
                    if len(parts) >= 2:
                        name, status = parts[0].strip(), parts[1].strip()
                        icon = "🟢" if "Up" in status else "🔴"
                        lines.append(f"{icon} {name}: {status}")
            return "\n".join(lines)
        except asyncio.TimeoutError:
            return "🐳 Docker 조회 타임아웃 (15s)"
        except FileNotFoundError:
            return "🐳 docker 명령을 찾을 수 없습니다"
        except Exception as exc:
            logger.error("infra_bot_docker_error", error=str(exc))
            return f"🐳 Docker 조회 오류: {exc}"

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
                lines = ["📋 StartupChecker\n━━━━━━━━━━━━━━━"]
                for item, ok in results.items():
                    icon = "✅" if ok else "❌"
                    lines.append(f"{icon} {item}")
                return "\n".join(lines)
            return f"📋 StartupChecker\n{results}"
        except Exception as exc:
            logger.error("infra_bot_checklist_error", error=str(exc))
            return f"체크리스트 오류: {exc}"

    async def _cmd_metrics(self, text: str, chat_id: int, message: dict) -> str:
        """Prometheus 핵심 메트릭 스냅샷."""
        try:
            import httpx
            from src.core.config_loader import get_config as _gc
            engine_url = _gc("monitoring.engine_url", default="http://localhost:8000")
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{engine_url}/metrics")
                resp.raise_for_status()
                raw = resp.text

            # Extract key metrics
            metrics: dict[str, str] = {}
            target_prefixes = [
                "leviathan_pnl_total",
                "leviathan_trades_total",
                "leviathan_active_strategies",
                "leviathan_exchange_health",
                "leviathan_uptime_seconds",
                "process_resident_memory_bytes",
            ]
            for line in raw.splitlines():
                if line.startswith("#"):
                    continue
                for prefix in target_prefixes:
                    if line.startswith(prefix):
                        parts = line.split()
                        if len(parts) >= 2:
                            name = parts[0]
                            value = parts[1]
                            # Shorten name
                            short = name.replace("leviathan_", "").replace("process_", "")
                            try:
                                fval = float(value)
                                if "bytes" in short:
                                    metrics[short] = f"{fval / 1024 / 1024:.1f} MB"
                                elif "seconds" in short:
                                    metrics[short] = f"{fval / 3600:.1f}h"
                                else:
                                    metrics[short] = f"{fval:,.4f}"
                            except ValueError:
                                metrics[short] = value

            if not metrics:
                return "📊 메트릭\n━━━━━━━━━━━━━━━\n수집된 메트릭 없음"

            lines = ["📊 Prometheus 메트릭\n━━━━━━━━━━━━━━━"]
            for name, val in metrics.items():
                lines.append(f"  {name}: {val}")
            return "\n".join(lines)

        except Exception as exc:
            return f"📊 메트릭 조회 오류: {exc}\n\n수동 확인: curl localhost:8000/metrics"

    async def _cmd_resources(self, text: str, chat_id: int, message: dict) -> str:
        """시스템 리소스 (CPU/메모리/디스크/네트워크/로드)."""
        lines = ["🖥️ 시스템 리소스\n━━━━━━━━━━━━━━━"]

        try:
            import psutil

            # CPU
            cpu_pct = psutil.cpu_percent(interval=0.5)
            cpu_cores = psutil.cpu_count(logical=True)
            freq = psutil.cpu_freq()
            freq_str = f" @ {freq.current:.0f}MHz" if freq else ""
            load = psutil.getloadavg()

            cpu_bar = self._bar(cpu_pct)
            lines.append(f"\n🔧 CPU ({cpu_cores}코어{freq_str})")
            lines.append(f"  {cpu_bar} {cpu_pct:.1f}%")
            lines.append(f"  Load: {load[0]:.2f} / {load[1]:.2f} / {load[2]:.2f}")

            # Memory
            mem = psutil.virtual_memory()
            mem_bar = self._bar(mem.percent)
            lines.append(f"\n💾 메모리")
            lines.append(f"  {mem_bar} {mem.percent:.1f}%")
            lines.append(f"  {mem.used / (1024**3):.1f}GB / {mem.total / (1024**3):.1f}GB")

            swap = psutil.swap_memory()
            if swap.total > 0:
                lines.append(f"  Swap: {swap.used / (1024**3):.1f}GB / {swap.total / (1024**3):.1f}GB ({swap.percent:.0f}%)")

            # Disk
            disk = psutil.disk_usage("/")
            disk_bar = self._bar(disk.percent)
            lines.append(f"\n📀 디스크 (/)")
            lines.append(f"  {disk_bar} {disk.percent:.1f}%")
            lines.append(f"  {disk.used / (1024**3):.1f}GB / {disk.total / (1024**3):.1f}GB")

            # Network I/O
            net = psutil.net_io_counters()
            lines.append(f"\n🌐 네트워크")
            lines.append(f"  ⬆️ 송신: {net.bytes_sent / (1024**3):.2f}GB")
            lines.append(f"  ⬇️ 수신: {net.bytes_recv / (1024**3):.2f}GB")
            if net.errin or net.errout:
                lines.append(f"  ⚠️ 에러: in={net.errin} out={net.errout}")

            # Top processes by memory
            procs = sorted(
                psutil.process_iter(["pid", "name", "memory_percent"]),
                key=lambda p: p.info.get("memory_percent", 0) or 0,
                reverse=True,
            )[:5]
            lines.append(f"\n📊 Top 프로세스 (메모리)")
            for p in procs:
                name = (p.info.get("name") or "?")[:20]
                mem_pct = p.info.get("memory_percent", 0) or 0
                lines.append(f"  {name}: {mem_pct:.1f}%")

        except ImportError:
            # Fallback: stdlib only
            import os
            import shutil

            load = os.getloadavg()
            lines.append(f"  Load: {load[0]:.2f} / {load[1]:.2f} / {load[2]:.2f}")

            disk = shutil.disk_usage("/")
            pct = (disk.used / disk.total) * 100
            lines.append(f"\n📀 디스크: {disk.used // (1024**3)}GB / {disk.total // (1024**3)}GB ({pct:.1f}%)")
            lines.append("\n⚠️ psutil 미설치 — 상세 메트릭 제한")

        except Exception as exc:
            lines.append(f"\n⚠️ 조회 오류: {exc}")

        return "\n".join(lines)

    @staticmethod
    def _bar(percent: float, length: int = 10) -> str:
        """프로그레스 바 생성."""
        filled = int(length * percent / 100)
        return "█" * filled + "░" * (length - filled)

    async def _cmd_restart(self, text: str, chat_id: int, message: dict) -> str:
        """Docker 서비스 재시작 (2단계 확인)."""
        parts = text.strip().split()
        if len(parts) < 2:
            return (
                "🔄 사용법: /restart &lt;service&gt;\n\n"
                "예: /restart engine\n"
                "    /restart redis\n"
                "    /restart timescaledb"
            )

        service = parts[1].strip()
        # Allowed services
        allowed = {"engine", "redis", "timescaledb", "prometheus", "grafana", "nginx", "alertmanager", "dashboard"}
        if service not in allowed:
            return f"🚫 허용되지 않은 서비스: {service}\n허용: {', '.join(sorted(allowed))}"

        kb = InlineKeyboard()
        kb.row(
            (f"🔄 {service} 재시작", f"restart_confirm_{service}"),
            ("❌ 취소", "restart_cancel"),
        )
        await self.send_message(
            f"⚠️ {service} 재시작\n\n정말로 {service}를 재시작하시겠습니까?",
            reply_markup=kb.to_markup(),
            chat_id=str(chat_id),
        )
        self._pending_restarts[chat_id] = (service, time.time())
        return None  # type: ignore[return-value]

    async def _cb_restart(self, callback_query: dict) -> str | None:
        """Restart 인라인 키보드 콜백."""
        data = callback_query["data"]
        msg = callback_query["message"]
        chat_id: int = msg["chat"]["id"]
        message_id: int = msg["message_id"]

        if data == "restart_cancel":
            self._pending_restarts.pop(chat_id, None)
            await self.edit_message(chat_id, message_id, "✅ 취소됨")
            return "취소됨"

        if data.startswith("restart_confirm_"):
            service = data.replace("restart_confirm_", "")
            pending = self._pending_restarts.get(chat_id)
            if pending is None or (time.time() - pending[1]) > 60:
                self._pending_restarts.pop(chat_id, None)
                await self.edit_message(chat_id, message_id, "⏰ 시간 초과 — 다시 /restart")
                return "시간 초과"

            del self._pending_restarts[chat_id]
            await self.edit_message(chat_id, message_id, f"🔄 {service} 재시작 중...")

            try:
                proc = await asyncio.create_subprocess_exec(
                    "docker", "compose", "restart", service,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
                if proc.returncode == 0:
                    await self.edit_message(
                        chat_id, message_id,
                        f"✅ {service} 재시작 완료",
                    )
                    return "재시작 완료"
                else:
                    err = (stderr or b"").decode()[:300]
                    await self.edit_message(
                        chat_id, message_id,
                        f"❌ {service} 재시작 실패\n{err}",
                    )
                    return "재시작 실패"
            except asyncio.TimeoutError:
                await self.edit_message(chat_id, message_id, f"⏰ {service} 재시작 타임아웃")
                return "타임아웃"
            except Exception as exc:
                await self.edit_message(chat_id, message_id, f"❌ 오류: {exc}")
                return "오류"
        return None

    async def _cmd_engine(self, text: str, chat_id: int, message: dict) -> str:
        """엔진 프로세스 제어 (/engine start|stop|restart|status)."""
        parts = text.strip().split()
        action = parts[1] if len(parts) > 1 else "status"

        if action == "status":
            try:
                proc = await asyncio.create_subprocess_exec(
                    "docker", "compose", "ps", "engine", "--format", "json",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
                raw = (stdout or b"").decode().strip()
                if not raw:
                    return "🔧 Engine: NOT RUNNING"

                import json as _json
                for line in raw.splitlines():
                    try:
                        c = _json.loads(line)
                        state = c.get("State", c.get("Status", "unknown"))
                        health = c.get("Health", "")
                        status_text = state
                        if health:
                            status_text += f" ({health})"
                        icon = "🟢" if "running" in state.lower() else "🔴"
                        return f"{icon} Engine: {status_text}"
                    except _json.JSONDecodeError:
                        continue
                return f"🔧 Engine: {raw[:200]}"
            except asyncio.TimeoutError:
                return "⏰ Engine 상태 조회 타임아웃"
            except FileNotFoundError:
                return "🔧 docker 명령을 찾을 수 없습니다"
            except Exception as exc:
                return f"🔧 Engine 상태 조회 오류: {exc}"

        if action in ("start", "stop", "restart"):
            kb = InlineKeyboard()
            kb.row(
                (f"✅ Engine {action}", f"engine_confirm_{action}"),
                ("❌ 취소", "engine_cancel"),
            )
            await self.send_message(
                f"⚠️ Engine {action} 실행하시겠습니까?",
                reply_markup=kb.to_markup(),
                chat_id=str(chat_id),
            )
            self._pending_engines[chat_id] = time.time()
            return None  # type: ignore[return-value]

        return "사용법: /engine start|stop|restart|status"

    async def _cb_engine(self, callback_query: dict) -> str | None:
        """Engine 제어 인라인 키보드 콜백."""
        data = callback_query["data"]
        msg = callback_query["message"]
        chat_id: int = msg["chat"]["id"]
        message_id: int = msg["message_id"]

        if data == "engine_cancel":
            self._pending_engines.pop(chat_id, None)
            await self.edit_message(chat_id, message_id, "✅ 취소됨")
            return "취소됨"

        if data.startswith("engine_confirm_"):
            pending_ts = self._pending_engines.get(chat_id)
            if pending_ts is None or (time.time() - pending_ts) > 60:
                self._pending_engines.pop(chat_id, None)
                await self.edit_message(chat_id, message_id, "⏰ 시간 초과 — 다시 /engine")
                return "시간 초과"
            del self._pending_engines[chat_id]
            action = data.replace("engine_confirm_", "")
            await self.edit_message(chat_id, message_id, f"🔧 Engine {action} 실행 중...")

            try:
                if action == "start":
                    cmd = ["docker", "compose", "up", "-d", "engine"]
                elif action == "stop":
                    cmd = ["docker", "compose", "stop", "engine"]
                else:  # restart
                    cmd = ["docker", "compose", "restart", "engine"]

                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)

                if proc.returncode == 0:
                    await self.edit_message(
                        chat_id, message_id,
                        f"✅ Engine {action} 완료",
                    )
                    return f"Engine {action} 완료"
                else:
                    err = (stderr or stdout or b"").decode()[:300]
                    await self.edit_message(
                        chat_id, message_id,
                        f"❌ Engine {action} 실패\n{err}",
                    )
                    return f"Engine {action} 실패"
            except asyncio.TimeoutError:
                await self.edit_message(chat_id, message_id, f"⏰ Engine {action} 타임아웃")
                return "타임아웃"
            except Exception as exc:
                await self.edit_message(chat_id, message_id, f"❌ 오류: {exc}")
                return "오류"
        return None

    async def _cmd_watchdog(self, text: str, chat_id: int, message: dict) -> str:
        """엔진 하트비트 감시 — Dead Man's Switch 모니터링."""
        parts = text.strip().split()
        action = parts[1] if len(parts) > 1 else "status"

        if action == "on":
            if not self._watchdog_enabled:
                self._watchdog_enabled = True
                self._watchdog_task = asyncio.create_task(self._watchdog_loop())
                return (
                    "🐕 Watchdog 활성화\n"
                    "━━━━━━━━━━━━━━━\n"
                    "Redis leviathan:heartbeat 모니터링 시작\n"
                    "30초 이상 무응답 시 알림 전송"
                )
            return "🐕 Watchdog 이미 활성화 상태"

        if action == "off":
            self._watchdog_enabled = False
            if self._watchdog_task:
                self._watchdog_task.cancel()
                self._watchdog_task = None
            return "🐕 Watchdog 비활성화됨"

        if action == "status":
            status = "활성화 ✅" if self._watchdog_enabled else "비활성화 ❌"
            return (
                f"🐕 Watchdog 상태: {status}\n"
                f"Redis URL: {self._redis_url}\n"
                f"모니터 키: leviathan:heartbeat (TTL=30s)"
            )

        return "사용법: /watchdog on|off|status"

    async def _watchdog_loop(self) -> None:
        """Redis leviathan:heartbeat 키 TTL 모니터링.

        엔진이 5초마다 TTL=30s 키를 갱신. 만료 시 → 엔진 장애.
        """
        missed = 0
        while self._watchdog_enabled:
            try:
                await asyncio.sleep(15)
                import redis.asyncio as aioredis
                r = aioredis.from_url(self._redis_url, socket_timeout=5)
                try:
                    val = await r.get("leviathan:heartbeat")
                    if val is None:
                        missed += 1
                        if missed >= 2:  # 30초 이상 무응답
                            await self.send_message(
                                f"🚨 엔진 하트비트 소실!\n"
                                f"━━━━━━━━━━━━━━━\n"
                                f"leviathan:heartbeat 키 없음\n"
                                f"연속 무응답: {missed}회 ({missed * 15}초)\n\n"
                                f"즉시 확인:\n"
                                f"  /health — 인프라 상태\n"
                                f"  /engine status — 엔진 상태\n"
                                f"  /closepositions — 긴급 청산"
                            )
                        else:
                            logger.warning("watchdog_heartbeat_missed count=%d", missed)
                    else:
                        if missed > 0:
                            await self.send_message(
                                f"✅ 엔진 하트비트 복구\n"
                                f"무응답 {missed}회 후 정상화"
                            )
                        missed = 0
                finally:
                    await r.aclose()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("watchdog_loop_error error=%s", exc)

    async def _cmd_closepositions(self, text: str, chat_id: int, message: dict) -> str:
        """긴급 포지션 전량 청산 — Redis leviathan:halt 설정 → 엔진 KillSwitch 활성화."""
        kb = InlineKeyboard()
        kb.row(
            ("🚨 전량 청산 실행", "closepos_confirm"),
            ("❌ 취소", "closepos_cancel"),
        )
        await self.send_message(
            "⚠️ 긴급 포지션 전량 청산\n"
            "━━━━━━━━━━━━━━━\n"
            "Redis에 leviathan:halt=1 을 설정합니다.\n\n"
            "▶ 엔진 실행 중: KillSwitch Tier1 즉시 활성화\n"
            "  → Tier2: 미체결 주문 전량 취소\n"
            "  → Tier3: 오픈 포지션 전량 시장가 청산\n\n"
            "▶ 엔진 중단 상태: 재시작 시 Redis halt 감지\n\n"
            "정말 실행하시겠습니까?",
            reply_markup=kb.to_markup(),
            chat_id=str(chat_id),
        )
        self._pending_closepositions[chat_id] = time.time()
        return None  # type: ignore[return-value]

    async def _cb_closepositions(self, callback_query: dict) -> str | None:
        """Close positions 인라인 키보드 콜백."""
        data = callback_query["data"]
        msg = callback_query["message"]
        chat_id: int = msg["chat"]["id"]
        message_id: int = msg["message_id"]

        if data == "closepos_cancel":
            self._pending_closepositions.pop(chat_id, None)
            await self.edit_message(chat_id, message_id, "✅ 청산 취소됨")
            return "취소됨"

        if data == "closepos_confirm":
            pending_ts = self._pending_closepositions.get(chat_id)
            if pending_ts is None or (time.time() - pending_ts) > 60:
                self._pending_closepositions.pop(chat_id, None)
                await self.edit_message(chat_id, message_id, "⏰ 시간 초과 — 다시 /closepositions")
                return "시간 초과"

            del self._pending_closepositions[chat_id]
            await self.edit_message(chat_id, message_id, "🚨 Redis halt 명령 전송 중...")

            try:
                import redis.asyncio as aioredis
                r = aioredis.from_url(self._redis_url, socket_timeout=5)
                try:
                    await r.set("leviathan:halt", "1", ex=86400)
                    await self.edit_message(
                        chat_id, message_id,
                        "✅ leviathan:halt=1 전송 완료\n"
                        "━━━━━━━━━━━━━━━\n"
                        "엔진이 실행 중이면 5초 내 KillSwitch 활성화됩니다.\n"
                        "엔진이 중단된 경우 재시작 시 halt 감지 후 자동 청산.\n\n"
                        "상태 확인: /health /engine status"
                    )
                finally:
                    await r.aclose()
                return "halt 명령 전송 완료"
            except Exception as exc:
                logger.error("closepositions_redis_error error=%s", exc)
                await self.edit_message(
                    chat_id, message_id,
                    f"❌ Redis 연결 실패: {exc}\n\n"
                    f"수동 청산 필요:\n"
                    f"  scripts/close_positions.py --execute"
                )
                return "Redis 오류"
        return None

    async def _cmd_help(self, text: str, chat_id: int, message: dict) -> str:
        """도움말."""
        return (
            "🏗️ LEVIATHAN-INFRA 봇\n"
            "━━━━━━━━━━━━━━━\n"
            "🏥 /health — 인프라 헬스체크\n"
            "🐳 /docker — Docker 상태\n"
            "📋 /checklist — 시작 체크리스트\n"
            "📊 /metrics — Prometheus 메트릭\n"
            "🖥️ /resources — 시스템 리소스\n"
            "🔄 /restart &lt;svc&gt; — 서비스 재시작\n"
            "🔧 /engine &lt;action&gt; — 엔진 제어\n"
            "🐕 /watchdog &lt;on|off|status&gt; — 하트비트 감시\n"
            "🚨 /closepositions — 긴급 포지션 전량 청산\n"
            "❓ /help — 이 도움말"
        )

    # ------------------------------------------------------------------
    # Proactive alert helpers
    # ------------------------------------------------------------------

    async def send_infra_alert(self, service: str, status: str, error: str) -> None:
        """인프라 장애 알림."""
        text = (
            f"🔴 인프라 장애\n"
            f"서비스: {service}\n"
            f"상태: {status}\n"
            f"오류: {error}"
        )
        await self.send_message(text)

    async def send_recovery_alert(self, service: str) -> None:
        """복구 알림."""
        text = f"🟢 복구 완료\n서비스: {service}"
        await self.send_message(text)
