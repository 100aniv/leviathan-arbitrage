"""LEVIATHAN Trade Telegram Bot.

US-291-c: 거래 알림 + 명령어 + 인라인 키보드.
US-291-f: Kill Switch 2단계 인라인 키보드.
US-291-g: 조회 메뉴 인라인 키보드.
US-291-h: 설정 변경 (/settings).
US-295: 일일 요약 리포트 09:00 KST.
"""
from __future__ import annotations

import asyncio
import datetime
import enum
import os
import time
from typing import Any

import structlog

from src.infra.telegram import TelegramAlerter
from src.infra.telegram_bot_base import InlineKeyboard, TelegramBotBase

logger = structlog.get_logger(__name__)


class AlertLevel(enum.Enum):
    ALL = "all"               # 모든 거래 체결 포함
    IMPORTANT = "important"   # WARNING + CRITICAL + EMERGENCY만
    CRITICAL_ONLY = "critical_only"  # CRITICAL + EMERGENCY만


class TradeTelegramBot(TelegramBotBase):
    """LEVIATHAN 거래 전용 Telegram 봇.

    환경변수:
      TRADE_TELEGRAM_BOT_TOKEN  (fallback: TELEGRAM_BOT_TOKEN)
      TRADE_TELEGRAM_CHAT_ID    (fallback: TELEGRAM_CHAT_ID)
      TRADE_TELEGRAM_ENABLED    (fallback: TELEGRAM_ENABLED, default "false")
    """

    def __init__(self, engine_context: Any = None) -> None:
        token = os.getenv("TRADE_TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TRADE_TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID", "")
        enabled_str = (
            os.getenv("TRADE_TELEGRAM_ENABLED") or os.getenv("TELEGRAM_ENABLED", "false")
        )
        super().__init__(
            bot_token=token,
            chat_id=chat_id,
            enabled=enabled_str.lower() == "true",
            bot_name="LEVIATHAN-TRADE",
        )
        self._engine_context = engine_context
        self._alert_level = AlertLevel.IMPORTANT
        self._alerter = TelegramAlerter(
            bot_token=token, chat_id=chat_id, enabled=self._enabled
        )
        self._pending_kills: dict[int, float] = {}

        self._register_all_commands()
        self._register_all_callbacks()

    # ------------------------------------------------------------------
    # Registration helpers
    # ------------------------------------------------------------------

    def _register_all_commands(self) -> None:
        self.register_command("/status", self._cmd_status)
        self.register_command("/pnl", self._cmd_pnl)
        self.register_command("/strategies", self._cmd_strategies)
        self.register_command("/risk", self._cmd_risk)
        self.register_command("/kill", self._cmd_kill)
        self.register_command("/pause", self._cmd_pause)
        self.register_command("/resume", self._cmd_resume)
        self.register_command("/alerts", self._cmd_alerts)
        self.register_command("/menu", self._cmd_menu)
        self.register_command("/settings", self._cmd_settings)
        self.register_command("/chart", self._cmd_chart)
        self.register_command("/help", self._cmd_help)

    def _register_all_callbacks(self) -> None:
        self.register_callback("kill_", self._cb_kill)
        self.register_callback("menu_", self._cb_menu)
        self.register_callback("settings_", self._cb_settings)

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    async def _cmd_status(self, text: str, chat_id: int, message: dict) -> str | None:
        snapshot = self._get_shadow_snapshot()
        ctx = self._engine_context
        mode = getattr(ctx, "mode", "unknown") if ctx else "unknown"
        kill = getattr(ctx, "kill_switch_active", False) if ctx else False
        if not snapshot:
            return (
                f"⚙️ <b>엔진 상태</b>\n"
                f"  모드: {mode}\n"
                f"  Kill Switch: {'🔴 활성' if kill else '🟢 비활성'}\n"
                f"  Shadow: 데이터 없음"
            )
        pnl = snapshot.get("total_pnl", 0.0)
        trades = snapshot.get("trades_executed", 0)
        wr = snapshot.get("win_rate", 0.0)
        return (
            f"⚙️ <b>엔진 상태</b>\n"
            f"  모드: {mode}\n"
            f"  Kill Switch: {'🔴 활성' if kill else '🟢 비활성'}\n"
            f"  PnL: ${pnl:+,.6f} | 거래: {trades}건 | 승률: {wr*100:.1f}%"
        )

    async def _cmd_pnl(self, text: str, chat_id: int, message: dict) -> str | None:
        return await self._get_pnl_text()

    async def _cmd_strategies(self, text: str, chat_id: int, message: dict) -> str | None:
        return await self._get_strategies_text()

    async def _cmd_risk(self, text: str, chat_id: int, message: dict) -> str | None:
        return await self._get_risk_text()

    async def _cmd_kill(self, text: str, chat_id: int, message: dict) -> str | None:
        kb = InlineKeyboard()
        kb.row(("🔴 정말 중단", "kill_confirm"), ("❌ 취소", "kill_cancel"))
        await self.send_message(
            "⚠️ <b>Kill Switch 활성화</b>\n\n정말로 모든 거래를 중단하시겠습니까?",
            reply_markup=kb.to_markup(),
            chat_id=str(chat_id),
        )
        self._pending_kills[chat_id] = time.time()
        return None

    async def _cmd_pause(self, text: str, chat_id: int, message: dict) -> str | None:
        ctx = self._engine_context
        if ctx:
            ctx.paused = True
        return "⏸️ <b>거래 일시중단</b>\n신규 거래가 일시 중단되었습니다."

    async def _cmd_resume(self, text: str, chat_id: int, message: dict) -> str | None:
        ctx = self._engine_context
        if ctx:
            ctx.paused = False
        return "▶️ <b>거래 재개</b>\n신규 거래가 재개되었습니다."

    async def _cmd_alerts(self, text: str, chat_id: int, message: dict) -> str | None:
        ctx = self._engine_context
        alerts = getattr(ctx, "active_alerts", []) if ctx else []
        if not alerts:
            return "✅ <b>미해결 알림 없음</b>"
        lines = ["🔔 <b>미해결 알림:</b>"]
        for a in alerts[:10]:
            lines.append(f"  • {a}")
        return "\n".join(lines)

    async def _cmd_menu(self, text: str, chat_id: int, message: dict) -> str | None:
        kb = InlineKeyboard()
        kb.row(("📈 PnL", "menu_pnl"), ("⚙️ 전략", "menu_strategies"))
        kb.row(("🛡️ 리스크", "menu_risk"), ("🏦 거래소", "menu_exchanges"))
        kb.row(("⬅️ 닫기", "menu_close"))
        await self.send_message(
            "📊 <b>조회 메뉴</b>",
            reply_markup=kb.to_markup(),
            chat_id=str(chat_id),
        )
        return None

    async def _cmd_settings(self, text: str, chat_id: int, message: dict) -> str | None:
        current = self._alert_level.value
        kb = InlineKeyboard()
        for level in AlertLevel:
            marker = "✅ " if level == self._alert_level else ""
            kb.row((f"{marker}{level.value}", f"settings_{level.value}"))
        await self.send_message(
            f"⚙️ <b>알림 설정</b>\n현재: {current}",
            reply_markup=kb.to_markup(),
            chat_id=str(chat_id),
        )
        return None

    async def _cmd_chart(self, text: str, chat_id: int, message: dict) -> str | None:
        parts = text.strip().split()
        chart_type = parts[1] if len(parts) > 1 else "pnl"
        try:
            from src.infra.telegram_charts import generate_chart  # type: ignore[import]

            snapshot = self._get_shadow_snapshot()
            png_bytes = await generate_chart(chart_type, snapshot)
            if png_bytes:
                await self.send_photo(
                    png_bytes, caption=f"📊 {chart_type.upper()} 차트", chat_id=str(chat_id)
                )
            else:
                return f"📊 {chart_type} 차트 데이터가 없습니다."
        except ImportError:
            return "차트 모듈을 사용할 수 없습니다 (matplotlib 미설치)"
        except Exception as exc:
            return f"차트 생성 실패: {exc}"
        return None

    async def _cmd_help(self, text: str, chat_id: int, message: dict) -> str | None:
        return (
            "📖 <b>LEVIATHAN Trade Bot 명령어</b>\n\n"
            "/status — 엔진 상태 + PnL + 전략\n"
            "/pnl — 현재 PnL 조회\n"
            "/strategies — 전략별 상태\n"
            "/risk — 리스크 메트릭\n"
            "/kill — Kill Switch (2단계 확인)\n"
            "/pause — 거래 일시중단\n"
            "/resume — 거래 재개\n"
            "/alerts — 미해결 알림 목록\n"
            "/menu — 조회 메뉴 (인라인 키보드)\n"
            "/settings — 알림 레벨 설정\n"
            "/chart [pnl|equity|strategy] — 차트 조회\n"
            "/help — 이 도움말"
        )

    # ------------------------------------------------------------------
    # Callback handlers
    # ------------------------------------------------------------------

    async def _cb_kill(self, callback_query: dict) -> str | None:
        data = callback_query["data"]
        msg = callback_query["message"]
        chat_id: int = msg["chat"]["id"]
        message_id: int = msg["message_id"]

        if data == "kill_confirm":
            pending_ts = self._pending_kills.get(chat_id)
            if pending_ts is not None and (time.time() - pending_ts) < 30:
                ctx = self._engine_context
                if ctx:
                    ctx.kill_switch_active = True
                del self._pending_kills[chat_id]
                await self.edit_message(
                    chat_id, message_id, "🔴 <b>Kill Switch 활성화됨</b>\n모든 거래가 중단되었습니다."
                )
                return "Kill Switch 활성화"
            else:
                self._pending_kills.pop(chat_id, None)
                await self.edit_message(
                    chat_id, message_id, "⏰ <b>시간 초과</b>\n다시 /kill 명령을 입력하세요."
                )
                return "시간 초과"
        elif data == "kill_cancel":
            self._pending_kills.pop(chat_id, None)
            await self.edit_message(chat_id, message_id, "✅ <b>취소됨</b>\n거래가 계속됩니다.")
            return "취소됨"
        return None

    async def _cb_menu(self, callback_query: dict) -> str | None:
        data = callback_query["data"]
        msg = callback_query["message"]
        chat_id: int = msg["chat"]["id"]
        message_id: int = msg["message_id"]

        if data == "menu_pnl":
            text = await self._get_pnl_text()
        elif data == "menu_strategies":
            text = await self._get_strategies_text()
        elif data == "menu_risk":
            text = await self._get_risk_text()
        elif data == "menu_exchanges":
            text = await self._get_exchanges_text()
        elif data == "menu_close":
            text = "📊 메뉴 닫힘"
        else:
            return None

        await self.edit_message(chat_id, message_id, text)
        return None

    async def _cb_settings(self, callback_query: dict) -> str | None:
        data = callback_query["data"]
        level_str = data.replace("settings_", "", 1)
        try:
            self._alert_level = AlertLevel(level_str)
        except ValueError:
            return "잘못된 설정값"
        msg = callback_query["message"]
        await self.edit_message(
            msg["chat"]["id"],
            msg["message_id"],
            f"✅ <b>알림 레벨 변경됨</b>\n새 설정: {self._alert_level.value}",
        )
        return f"설정: {self._alert_level.value}"

    # ------------------------------------------------------------------
    # Adapter methods — backward compat with TelegramAlerter callers
    # ------------------------------------------------------------------

    async def send_alert(self, message: str, level: str = "INFO") -> bool:
        """기존 TelegramAlerter.send_alert() 호환."""
        return await self._alerter.send_alert(message, level=level)

    async def send_kill_switch_event(self, event: Any) -> bool:
        return await self._alerter.send_kill_switch_event(event)

    async def send_circuit_breaker_event(self, state: str, reason: str) -> bool:
        return await self._alerter.send_circuit_breaker_event(state, reason)

    async def send_signal_found(self, signal: Any) -> bool:
        if self._alert_level == AlertLevel.CRITICAL_ONLY:
            return False
        return await self._alerter.send_signal_found(signal)

    async def send_daily_summary(self, data: dict) -> bool:
        return await self._alerter.send_daily_summary(data)

    async def send_fill_kr(self, data: dict) -> bool:
        if self._alert_level == AlertLevel.CRITICAL_ONLY:
            return False
        return await self._alerter.send_fill_kr(data)

    # ------------------------------------------------------------------
    # Daily report scheduler (US-295)
    # ------------------------------------------------------------------

    async def schedule_daily_report(self) -> None:
        """09:00 KST (= 00:00 UTC) 일일 리포트 스케줄."""
        while True:
            now = datetime.datetime.now(datetime.timezone.utc)
            target = now.replace(hour=0, minute=0, second=0, microsecond=0)
            if now >= target:
                target += datetime.timedelta(days=1)
            wait_seconds = (target - now).total_seconds()
            await asyncio.sleep(wait_seconds)
            try:
                snapshot = self._get_shadow_snapshot()
                if snapshot:
                    await self._alerter.send_daily_report_kr(snapshot)
            except Exception:
                logger.error("daily_report_failed", exc_info=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_shadow_snapshot(self) -> dict | None:
        ctx = self._engine_context
        if not ctx:
            return None
        shadow = getattr(ctx, "shadow_mode", None)
        if not shadow:
            return None
        try:
            return shadow.get_snapshot()
        except Exception:
            return None

    async def _get_pnl_text(self) -> str:
        snapshot = self._get_shadow_snapshot()
        if not snapshot:
            return "PnL 정보 없음"
        pnl = snapshot.get("total_pnl", 0.0)
        wr = snapshot.get("win_rate", 0.0)
        trades = snapshot.get("trades_executed", 0)
        dd = snapshot.get("max_drawdown_pct", 0.0)
        emoji = "📈" if pnl >= 0 else "📉"
        return (
            f"{emoji} <b>현재 PnL:</b> ${pnl:+,.6f}\n"
            f"🎯 승률: {wr * 100:.1f}%\n"
            f"🔁 거래: {trades}건\n"
            f"📉 MDD: {dd * 100:.2f}%"
        )

    async def _get_strategies_text(self) -> str:
        snapshot = self._get_shadow_snapshot()
        if not snapshot:
            return "전략 정보 없음"
        by_strategy = snapshot.get("by_strategy", [])
        if not by_strategy:
            return "등록된 전략 없음"
        lines = ["⚙️ <b>전략 상태:</b>"]
        for s in by_strategy:
            sid = s.get("strategy_id", "?")
            s_pnl = s.get("pnl", 0.0)
            s_trades = s.get("trades", 0)
            icon = "🟢" if s_trades > 0 else "⚪"
            lines.append(f"  {icon} {sid}: ${s_pnl:+,.4f} ({s_trades}건)")
        return "\n".join(lines)

    async def _get_risk_text(self) -> str:
        snapshot = self._get_shadow_snapshot()
        mdd = snapshot.get("max_drawdown_pct", 0.0) if snapshot else 0.0
        ctx = self._engine_context
        kill_active = getattr(ctx, "kill_switch_active", False) if ctx else False
        return (
            f"🛡️ <b>리스크 메트릭:</b>\n"
            f"  📉 MDD: {mdd * 100:.2f}%\n"
            f"  🔴 Kill Switch: {'활성' if kill_active else '비활성'}"
        )

    async def _get_exchanges_text(self) -> str:
        ctx = self._engine_context
        if not ctx:
            return "거래소 정보 없음"
        exchanges: dict[str, float] = getattr(ctx, "exchange_health", {})
        if not exchanges:
            return "거래소 정보 없음"
        lines = ["🏦 <b>거래소 상태:</b>"]
        for ex_id, health in sorted(exchanges.items()):
            icon = "🟢" if health > 0.95 else ("🟡" if health > 0.5 else "🔴")
            lines.append(f"  {icon} {ex_id}: {health * 100:.0f}%")
        return "\n".join(lines)
