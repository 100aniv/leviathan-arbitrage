"""Telegram bidirectional command handler — long-polling based.

US-117: 5 commands (/status /kill /mode /balance /help) via Telegram Bot API.
US-219: 6 new commands (/pnl /strategies /risk /pause /resume /alerts).
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

import httpx
import structlog

from src.infra.telegram import TelegramAlerter

logger = structlog.get_logger(__name__)


class TelegramCommandHandler:
    """Long-poll Telegram updates and dispatch engine control commands."""

    COMMANDS = {
        "/status", "/kill", "/mode", "/balance", "/help",
        "/pnl", "/strategies", "/risk", "/pause", "/resume", "/alerts",
    }
    HELP_TEXT = (
        "LEVIATHAN Bot Commands:\n"
        "/status — Engine status + today's PnL + active strategies\n"
        "/kill — Trigger KillSwitch immediately\n"
        "/mode — Current execution mode\n"
        "/balance — Exchange balance summary\n"
        "/pnl — 현재 PnL 조회\n"
        "/strategies — 전략 상태 (활성/비활성, PnL)\n"
        "/risk — 리스크 메트릭 (MDD, Kill Switch, Circuit Breaker)\n"
        "/pause — 거래 일시중단 (Kill Switch 활성화)\n"
        "/resume — 거래 재개 (Kill Switch 해제)\n"
        "/alerts — 미해결 알림 목록\n"
        "/help — Show this help message"
    )

    def __init__(
        self,
        alerter: TelegramAlerter,
        status_fn: Callable[[], Awaitable[str]] | None = None,
        kill_fn: Callable[[], Awaitable[str]] | None = None,
        mode_fn: Callable[[], Awaitable[str]] | None = None,
        balance_fn: Callable[[], Awaitable[str]] | None = None,
        allowed_chat_ids: set[int] | None = None,
        *,
        pnl_fn: Callable[[], Awaitable[str]] | None = None,
        strategies_fn: Callable[[], Awaitable[str]] | None = None,
        risk_fn: Callable[[], Awaitable[str]] | None = None,
        pause_fn: Callable[[], Awaitable[str]] | None = None,
        resume_fn: Callable[[], Awaitable[str]] | None = None,
        alerts_fn: Callable[[], Awaitable[str]] | None = None,
        engine_context: Any | None = None,
    ) -> None:
        self._alerter = alerter
        self._offset: int = 0
        self._running = False
        self._consecutive_errors: int = 0
        # Reusable HTTP client for long-polling (US-168)
        self._http_client: httpx.AsyncClient | None = None
        # Engine context for US-219 data queries
        self._engine_context = engine_context
        # Auth: only allow commands from these chat IDs (fallback to TELEGRAM_CHAT_ID env)
        if allowed_chat_ids is not None:
            self._allowed_chat_ids = allowed_chat_ids
        else:
            import os
            chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
            self._allowed_chat_ids = {int(chat_id)} if chat_id.strip() else set()
        self._handlers: dict[str, Callable[[], Awaitable[str]]] = {
            "/status": status_fn or self._default_status,
            "/kill": kill_fn or self._default_kill,
            "/mode": mode_fn or self._default_mode,
            "/balance": balance_fn or self._default_balance,
            "/help": self._handle_help,
            # US-219 commands
            "/pnl": pnl_fn or self._handle_pnl,
            "/strategies": strategies_fn or self._handle_strategies,
            "/risk": risk_fn or self._handle_risk,
            "/pause": pause_fn or self._handle_pause,
            "/resume": resume_fn or self._handle_resume,
            "/alerts": alerts_fn or self._handle_alerts,
        }

    # ---------------------------------------------------------------------------
    # Default no-op handlers (overridden via constructor)
    # ---------------------------------------------------------------------------

    async def _default_status(self) -> str:
        return "Engine running (no status_fn configured)"

    async def _default_kill(self) -> str:
        return "KillSwitch not configured"

    async def _default_mode(self) -> str:
        return "Mode info not available"

    async def _default_balance(self) -> str:
        return "Balance info not available"

    async def _handle_help(self) -> str:
        return self.HELP_TEXT

    # ---------------------------------------------------------------------------
    # US-219: New command handlers
    # ---------------------------------------------------------------------------

    async def _handle_pnl(self) -> str:
        """현재 PnL 조회 — EngineContext.shadow_mode.get_snapshot()."""
        snapshot = self._get_shadow_snapshot()
        if snapshot is None:
            return "PnL 정보를 가져올 수 없습니다 (엔진 미연결)"
        pnl = snapshot.get("total_pnl", 0.0)
        wr = snapshot.get("win_rate", 0.0)
        trades = snapshot.get("trades_executed", 0)
        dd = snapshot.get("max_drawdown_pct", 0.0)
        emoji = "📈" if pnl >= 0 else "📉"
        return (
            f"{emoji} 현재 PnL: ${pnl:+,.6f}\n"
            f"🎯 승률: {wr * 100:.1f}%\n"
            f"🔁 거래: {trades}건\n"
            f"📉 MDD: {dd * 100:.2f}%"
        )

    async def _handle_strategies(self) -> str:
        """7개 전략 상태 (활성/비활성, PnL)."""
        snapshot = self._get_shadow_snapshot()
        if snapshot is None:
            return "전략 정보를 가져올 수 없습니다 (엔진 미연결)"
        by_strategy = snapshot.get("by_strategy", [])
        if not by_strategy:
            return "등록된 전략이 없습니다"
        lines = ["⚙️ 전략 상태:"]
        for s in by_strategy:
            sid = s.get("strategy_id", "?")
            s_pnl = s.get("pnl", 0.0)
            s_trades = s.get("trades", 0)
            s_wr = s.get("win_rate", 0.0)
            icon = "🟢" if s_trades > 0 else "⚪"
            lines.append(
                f"  {icon} {sid}: ${s_pnl:+,.4f} ({s_trades}건, {s_wr * 100:.1f}%)"
            )
        return "\n".join(lines)

    async def _handle_risk(self) -> str:
        """리스크 메트릭 (MDD, Kill Switch, Circuit Breaker)."""
        ctx = self._engine_context
        snapshot = self._get_shadow_snapshot()
        mdd = snapshot.get("max_drawdown_pct", 0.0) if snapshot else 0.0
        kill_active = getattr(ctx, "kill_switch_active", False) if ctx else False
        return (
            f"🛡️ 리스크 메트릭:\n"
            f"  📉 MDD: {mdd * 100:.2f}%\n"
            f"  🔴 Kill Switch: {'활성' if kill_active else '비활성'}"
        )

    async def _handle_pause(self) -> str:
        """거래 일시중단 (Kill Switch 활성화)."""
        ctx = self._engine_context
        if ctx is None:
            return "엔진 미연결 — 일시중단 불가"
        try:
            ctx.kill_switch_active = True
            logger.info("telegram_bot_pause_activated")
            return "⏸️ 거래 일시중단됨 (Kill Switch 활성화)"
        except Exception as exc:
            logger.error("telegram_bot_pause_failed", error=str(exc))
            return f"일시중단 실패: {exc}"

    async def _handle_resume(self) -> str:
        """거래 재개 (Kill Switch 해제)."""
        ctx = self._engine_context
        if ctx is None:
            return "엔진 미연결 — 재개 불가"
        try:
            ctx.kill_switch_active = False
            logger.info("telegram_bot_resume_activated")
            return "▶️ 거래 재개됨 (Kill Switch 해제)"
        except Exception as exc:
            logger.error("telegram_bot_resume_failed", error=str(exc))
            return f"재개 실패: {exc}"

    async def _handle_alerts(self) -> str:
        """미해결 알림 목록."""
        ctx = self._engine_context
        if ctx is None:
            return "엔진 미연결 — 알림 조회 불가"
        alerts = getattr(ctx, "pending_alerts", None)
        if not alerts:
            return "✅ 미해결 알림 없음"
        lines = [f"🔔 미해결 알림 ({len(alerts)}건):"]
        for i, alert in enumerate(alerts[:10], 1):
            lines.append(f"  {i}. {alert}")
        if len(alerts) > 10:
            lines.append(f"  ... +{len(alerts) - 10}건")
        return "\n".join(lines)

    def _get_shadow_snapshot(self) -> dict[str, Any] | None:
        """Safely get shadow_mode.get_snapshot() from engine context."""
        ctx = self._engine_context
        if ctx is None:
            return None
        shadow = getattr(ctx, "shadow_mode", None)
        if shadow is None:
            return None
        try:
            return shadow.get_snapshot()
        except Exception:
            logger.warning("telegram_bot_snapshot_failed", exc_info=True)
            return None

    # ---------------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------------

    async def process_command(self, text: str) -> str:
        """Parse first token and dispatch to registered handler."""
        cmd = text.strip().split()[0].lower() if text.strip() else ""
        handler = self._handlers.get(cmd)
        if handler is None:
            return f"Unknown command: {cmd}\n\n{self.HELP_TEXT}"
        return await handler()

    async def poll_updates(self) -> list[dict]:
        """Long-poll Telegram getUpdates API (timeout=30s)."""
        if not self._alerter.bot_token or not self._alerter.enabled:
            return []
        url = f"https://api.telegram.org/bot{self._alerter.bot_token}/getUpdates"
        params: dict = {
            "offset": self._offset,
            "timeout": 30,
            "allowed_updates": ["message"],
        }
        try:
            if self._http_client is None:
                self._http_client = httpx.AsyncClient(timeout=35)
            resp = await self._http_client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                logger.warning("telegram_api_error", response=data)
                return []
            self._consecutive_errors = 0
            return data.get("result", [])
        except Exception:
            self._consecutive_errors = getattr(self, "_consecutive_errors", 0) + 1
            logger.warning("telegram_poll_failed", consecutive=self._consecutive_errors, exc_info=True)
            return []

    async def close(self) -> None:
        """Close the reusable HTTP client (US-168)."""
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def poll_loop(self) -> None:
        """Run long-poll loop until stop() is called."""
        self._running = True
        self._consecutive_errors = 0
        while self._running:
            updates = await self.poll_updates()
            for update in updates:
                self._offset = update["update_id"] + 1
                msg = update.get("message", {})
                chat_id = msg.get("chat", {}).get("id")
                text = msg.get("text", "")
                # Auth check: fail-closed — no allowed_chat_ids means block ALL
                if not self._allowed_chat_ids:
                    logger.warning("telegram_no_allowed_chats", chat_id=chat_id)
                    continue
                if chat_id not in self._allowed_chat_ids:
                    logger.warning("telegram_unauthorized_command", chat_id=chat_id, text=text)
                    continue
                if text.startswith("/"):
                    response = await self.process_command(text)
                    await self._alerter.send_alert(response, level="INFO")
            if not updates:
                # Exponential backoff on consecutive errors, capped at 60s
                backoff = min(60, 2 ** min(self._consecutive_errors, 6)) if self._consecutive_errors > 0 else 1
                await asyncio.sleep(backoff)

    def stop(self) -> None:
        """Signal poll_loop to exit after current iteration."""
        self._running = False
