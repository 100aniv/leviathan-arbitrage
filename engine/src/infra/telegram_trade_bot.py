"""LEVIATHAN Trade Telegram Bot.

US-291-c: 거래 알림 + 명령어 + 인라인 키보드.
US-291-f: Kill Switch 2단계 인라인 키보드.
US-291-g: 조회 메뉴 인라인 키보드.
US-291-h: 설정 변경 (/settings).
US-295: 일일 요약 리포트 09:00 KST.
Phase S20-B: 기관급 기능 확장 (12개 → 20개 명령어).
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
        self._pending_strategy_toggles: dict[int, tuple[str, str, float]] = {}

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
        # Phase S20-B: New commands
        self.register_command("/positions", self._cmd_positions)
        self.register_command("/fills", self._cmd_fills)
        self.register_command("/strategy", self._cmd_strategy_toggle)
        self.register_command("/exchanges", self._cmd_exchanges)
        self.register_command("/whitelist", self._cmd_whitelist)
        self.register_command("/blacklist", self._cmd_blacklist)
        self.register_command("/params", self._cmd_params)
        self.register_command("/report", self._cmd_report)
        self.register_command("/balance", self._cmd_balance)
        self.register_command("/help", self._cmd_help)

    def _register_all_callbacks(self) -> None:
        self.register_callback("kill_", self._cb_kill)
        self.register_callback("menu_", self._cb_menu)
        self.register_callback("settings_", self._cb_settings)
        self.register_callback("strategy_", self._cb_strategy)

    # ------------------------------------------------------------------
    # Original command handlers
    # ------------------------------------------------------------------

    async def _cmd_status(self, text: str, chat_id: int, message: dict) -> str | None:
        snapshot = self._get_paper_snapshot()
        ctx = self._engine_context
        mode = getattr(ctx, "mode", "N/A") if ctx else "대기 (엔진 미연결)"
        kill = getattr(ctx, "kill_switch_active", False) if ctx else False
        if not snapshot:
            return (
                "⚙️ 엔진 상태\n"
                "━━━━━━━━━━━━━━━\n"
                f"📡 모드: {mode}\n"
                f"🔴 Kill Switch: {'활성' if kill else '비활성'}\n"
                "📊 Paper: 데이터 없음"
            )
        pnl = snapshot.get("total_pnl", 0.0)
        trades = snapshot.get("trades_executed", 0)
        wr = snapshot.get("win_rate", 0.0)
        emoji = "📈" if pnl >= 0 else "📉"
        return (
            "⚙️ 엔진 상태\n"
            "━━━━━━━━━━━━━━━\n"
            f"📡 모드: {mode}\n"
            f"🔴 Kill Switch: {'활성' if kill else '비활성'}\n"
            f"{emoji} PnL: ${pnl:+,.6f}\n"
            f"🔁 거래: {trades}건\n"
            f"🎯 승률: {wr*100:.1f}%"
        )

    async def _cmd_pnl(self, text: str, chat_id: int, message: dict) -> str | None:
        return await self._get_pnl_text()

    async def _cmd_balance(self, text: str, chat_id: int, message: dict) -> str | None:
        ctx = self._engine_context
        shadow = getattr(ctx, "paper_mode", None) if ctx else None
        if not shadow:
            return (
                "💰 가상 잔고\n"
                "━━━━━━━━━━━━━━━\n"
                "Paper 모드 미연결 — 데이터 없음"
            )
        tracker = getattr(shadow, "_balance_tracker", None)
        if not tracker:
            return (
                "💰 가상 잔고\n"
                "━━━━━━━━━━━━━━━\n"
                "잔고 트래커 없음"
            )
        initial = float(tracker._initial)
        balances = tracker.summary()
        snapshot = self._get_paper_snapshot()
        total_pnl = snapshot.get("total_pnl", 0.0) if snapshot else 0.0

        lines = [
            "💰 가상 잔고 (Paper 모드)\n━━━━━━━━━━━━━━━",
            f"🏦 초기 자본: ${initial:,.2f}",
            f"📈 총 PnL: ${total_pnl:+,.6f}",
            "",
            "📊 거래소별 가상 잔고:",
        ]
        if balances:
            for ex_id, bal_str in sorted(balances.items()):
                try:
                    bal = float(bal_str)
                    diff = bal - initial
                    icon = "📈" if diff >= 0 else "📉"
                    lines.append(f"  {icon} {ex_id}: ${bal:,.2f} ({diff:+.2f})")
                except (ValueError, TypeError):
                    lines.append(f"  • {ex_id}: {bal_str}")
        else:
            lines.append("  아직 거래 없음 (초기 잔고 유지)")
        return "\n".join(lines)

    async def _cmd_strategies(self, text: str, chat_id: int, message: dict) -> str | None:
        return await self._get_strategies_text()

    async def _cmd_risk(self, text: str, chat_id: int, message: dict) -> str | None:
        return await self._get_risk_text()

    async def _cmd_kill(self, text: str, chat_id: int, message: dict) -> str | None:
        kb = InlineKeyboard()
        kb.row(("🔴 정말 중단", "kill_confirm"), ("❌ 취소", "kill_cancel"))
        await self.send_message(
            "⚠️ Kill Switch 활성화\n\n정말로 모든 거래를 중단하시겠습니까?",
            reply_markup=kb.to_markup(),
            chat_id=str(chat_id),
        )
        self._pending_kills[chat_id] = time.time()
        return None

    async def _cmd_pause(self, text: str, chat_id: int, message: dict) -> str | None:
        ctx = self._engine_context
        if ctx:
            ctx.paused = True
        return "⏸️ 거래 일시중단\n신규 거래가 일시 중단되었습니다."

    async def _cmd_resume(self, text: str, chat_id: int, message: dict) -> str | None:
        ctx = self._engine_context
        if ctx:
            ctx.paused = False
            ctx.kill_switch_active = False
            ctx.running = True
        try:
            from src.risk.kill_switch import clear_halt
            clear_halt()
        except Exception:
            pass
        return "▶️ 거래 재개\n신규 거래가 재개되었습니다."

    async def _cmd_alerts(self, text: str, chat_id: int, message: dict) -> str | None:
        ctx = self._engine_context
        alerts = getattr(ctx, "active_alerts", []) if ctx else []
        if not alerts:
            return "✅ 미해결 알림 없음"
        lines = ["🔔 미해결 알림:"]
        for a in alerts[:10]:
            lines.append(f"  {a}")
        return "\n".join(lines)

    async def _cmd_menu(self, text: str, chat_id: int, message: dict) -> str | None:
        kb = InlineKeyboard()
        kb.row(("📈 PnL", "menu_pnl"), ("⚙️ 전략", "menu_strategies"))
        kb.row(("🛡️ 리스크", "menu_risk"), ("🏦 거래소", "menu_exchanges"))
        kb.row(("📊 포지션", "menu_positions"), ("📋 체결", "menu_fills"))
        kb.row(("⬅️ 닫기", "menu_close"))
        await self.send_message(
            "📊 조회 메뉴",
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
            f"⚙️ 알림 설정\n현재: {current}",
            reply_markup=kb.to_markup(),
            chat_id=str(chat_id),
        )
        return None

    async def _cmd_chart(self, text: str, chat_id: int, message: dict) -> str | None:
        parts = text.strip().split()
        chart_type = parts[1] if len(parts) > 1 else "pnl"
        try:
            from src.infra.telegram_charts import generate_chart  # type: ignore[import]

            snapshot = self._get_paper_snapshot()
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

    # ------------------------------------------------------------------
    # Phase S20-B: New command handlers
    # ------------------------------------------------------------------

    async def _cmd_positions(self, text: str, chat_id: int, message: dict) -> str | None:
        """오픈 포지션 목록."""
        ctx = self._engine_context
        positions = getattr(ctx, "positions", {}) if ctx else {}
        if not positions:
            return "📊 오픈 포지션\n━━━━━━━━━━━━━━━\n포지션 없음"

        lines = ["📊 오픈 포지션\n━━━━━━━━━━━━━━━"]
        if isinstance(positions, dict):
            for symbol, pos in list(positions.items())[:15]:
                side = getattr(pos, "side", "N/A") if hasattr(pos, "side") else pos.get("side", "N/A") if isinstance(pos, dict) else "N/A"
                qty = getattr(pos, "quantity", 0) if hasattr(pos, "quantity") else pos.get("quantity", 0) if isinstance(pos, dict) else 0
                pnl = getattr(pos, "unrealized_pnl", 0) if hasattr(pos, "unrealized_pnl") else pos.get("unrealized_pnl", 0) if isinstance(pos, dict) else 0
                icon = "🟢" if pnl >= 0 else "🔴"
                lines.append(f"{icon} {symbol}\n   {side} {qty} | PnL: ${pnl:+,.4f}")
        elif isinstance(positions, list):
            for pos in positions[:15]:
                symbol = pos.get("symbol", "?") if isinstance(pos, dict) else getattr(pos, "symbol", "?")
                side = pos.get("side", "N/A") if isinstance(pos, dict) else getattr(pos, "side", "N/A")
                pnl = pos.get("unrealized_pnl", 0) if isinstance(pos, dict) else getattr(pos, "unrealized_pnl", 0)
                icon = "🟢" if pnl >= 0 else "🔴"
                lines.append(f"{icon} {symbol} | {side} | ${pnl:+,.4f}")

        return "\n".join(lines)

    async def _cmd_fills(self, text: str, chat_id: int, message: dict) -> str | None:
        """최근 체결 내역 (10건)."""
        ctx = self._engine_context
        fills = getattr(ctx, "recent_fills", []) if ctx else []
        if not fills:
            # Try shadow snapshot
            snapshot = self._get_paper_snapshot()
            if snapshot:
                trades = snapshot.get("trades_executed", 0)
                return (
                    "📋 최근 체결\n━━━━━━━━━━━━━━━\n"
                    f"총 {trades}건 체결 (상세 내역은 대시보드에서 확인)"
                )
            return "📋 최근 체결\n━━━━━━━━━━━━━━━\n체결 내역 없음"

        lines = ["📋 최근 체결 (10건)\n━━━━━━━━━━━━━━━"]
        for fill in fills[-10:]:
            if isinstance(fill, dict):
                symbol = fill.get("symbol", "?")
                side = fill.get("side", "?")
                pnl = fill.get("pnl", 0)
                fee = fill.get("fee", 0)
                ts = fill.get("timestamp", "")
            else:
                symbol = getattr(fill, "symbol", "?")
                side = getattr(fill, "side", "?")
                pnl = getattr(fill, "pnl", 0)
                fee = getattr(fill, "fee", 0)
                ts = getattr(fill, "timestamp", "")

            icon = "💰" if pnl > 0 else "💸" if pnl < 0 else "➖"
            lines.append(f"{icon} {symbol} {side}\n   PnL: ${pnl:+,.4f} | 수수료: ${fee:.4f}")

        return "\n".join(lines)

    async def _cmd_strategy_toggle(self, text: str, chat_id: int, message: dict) -> str | None:
        """전략 활성/비활성 (/strategy <name> on/off)."""
        parts = text.strip().split()
        if len(parts) < 3:
            # Show strategy list with inline keyboard
            ctx = self._engine_context
            strategies = getattr(ctx, "strategy_manager", None)
            strategy_names = []
            if strategies:
                registered = getattr(strategies, "strategies", {})
                strategy_names = list(registered.keys()) if isinstance(registered, dict) else []

            if not strategy_names:
                strategy_names = [
                    "cross_exchange", "spot_futures", "futures_futures",
                    "triangular", "funding_rate", "statistical_arb", "cex_dex",
                ]

            kb = InlineKeyboard()
            for name in strategy_names[:8]:
                kb.row(
                    (f"🟢 {name} ON", f"strategy_on_{name}"),
                    (f"🔴 {name} OFF", f"strategy_off_{name}"),
                )
            await self.send_message(
                "⚙️ 전략 제어\n전략을 선택하세요:",
                reply_markup=kb.to_markup(),
                chat_id=str(chat_id),
            )
            return None

        strategy_name = parts[1]
        action = parts[2].lower()

        if action not in ("on", "off"):
            return "사용법: /strategy &lt;name&gt; on|off"

        ctx = self._engine_context
        if ctx:
            sm = getattr(ctx, "strategy_manager", None)
            if sm:
                enabled_strategies = getattr(sm, "enabled_strategies", None)
                if enabled_strategies is not None and isinstance(enabled_strategies, set):
                    if action == "on":
                        enabled_strategies.add(strategy_name)
                    else:
                        enabled_strategies.discard(strategy_name)

        icon = "🟢" if action == "on" else "🔴"
        return f"{icon} {strategy_name}: {'활성화' if action == 'on' else '비활성화'}"

    async def _cb_strategy(self, callback_query: dict) -> str | None:
        """전략 on/off 인라인 버튼 콜백."""
        data = callback_query["data"]
        msg = callback_query["message"]
        chat_id = msg["chat"]["id"]
        message_id = msg["message_id"]

        if data.startswith("strategy_on_"):
            name = data.replace("strategy_on_", "")
            # Simulate /strategy name on
            await self._cmd_strategy_toggle(f"/strategy {name} on", chat_id, msg)
            await self.edit_message(chat_id, message_id, f"🟢 {name} 활성화됨")
            return f"{name} ON"
        elif data.startswith("strategy_off_"):
            name = data.replace("strategy_off_", "")
            await self._cmd_strategy_toggle(f"/strategy {name} off", chat_id, msg)
            await self.edit_message(chat_id, message_id, f"🔴 {name} 비활성화됨")
            return f"{name} OFF"
        return None

    async def _cmd_exchanges(self, text: str, chat_id: int, message: dict) -> str | None:
        """거래소 연결 상태 + latency."""
        return await self._get_exchanges_text()

    async def _cmd_whitelist(self, text: str, chat_id: int, message: dict) -> str | None:
        """심볼 화이트리스트 관리."""
        parts = text.strip().split()
        ctx = self._engine_context
        whitelist: set[str] = getattr(ctx, "symbol_whitelist", set()) if ctx else set()

        if len(parts) == 1:
            # Show current whitelist
            if not whitelist:
                return "✅ 화이트리스트\n━━━━━━━━━━━━━━━\n제한 없음 (모든 심볼 허용)"
            lines = ["✅ 화이트리스트\n━━━━━━━━━━━━━━━"]
            for s in sorted(whitelist):
                lines.append(f"  {s}")
            return "\n".join(lines)

        action = parts[1].lower()
        if action == "add" and len(parts) >= 3:
            symbol = parts[2].upper()
            whitelist.add(symbol)
            if ctx:
                ctx.symbol_whitelist = whitelist
            return f"✅ {symbol} 화이트리스트에 추가됨"
        elif action == "remove" and len(parts) >= 3:
            symbol = parts[2].upper()
            whitelist.discard(symbol)
            if ctx:
                ctx.symbol_whitelist = whitelist
            return f"✅ {symbol} 화이트리스트에서 제거됨"
        elif action == "clear":
            if ctx:
                ctx.symbol_whitelist = set()
            return "✅ 화이트리스트 초기화됨"

        return "사용법: /whitelist [add|remove|clear] [SYMBOL]"

    async def _cmd_blacklist(self, text: str, chat_id: int, message: dict) -> str | None:
        """심볼 블랙리스트 관리."""
        parts = text.strip().split()
        ctx = self._engine_context
        blacklist: set[str] = getattr(ctx, "symbol_blacklist", set()) if ctx else set()

        if len(parts) == 1:
            if not blacklist:
                return "🚫 블랙리스트\n━━━━━━━━━━━━━━━\n차단 심볼 없음"
            lines = ["🚫 블랙리스트\n━━━━━━━━━━━━━━━"]
            for s in sorted(blacklist):
                lines.append(f"  {s}")
            return "\n".join(lines)

        action = parts[1].lower()
        if action == "add" and len(parts) >= 3:
            symbol = parts[2].upper()
            blacklist.add(symbol)
            if ctx:
                ctx.symbol_blacklist = blacklist
            return f"🚫 {symbol} 블랙리스트에 추가됨"
        elif action == "remove" and len(parts) >= 3:
            symbol = parts[2].upper()
            blacklist.discard(symbol)
            if ctx:
                ctx.symbol_blacklist = blacklist
            return f"✅ {symbol} 블랙리스트에서 제거됨"
        elif action == "clear":
            if ctx:
                ctx.symbol_blacklist = set()
            return "✅ 블랙리스트 초기화됨"

        return "사용법: /blacklist [add|remove|clear] [SYMBOL]"

    async def _cmd_params(self, text: str, chat_id: int, message: dict) -> str | None:
        """핵심 파라미터 조회."""
        from src.core.config_loader import get_config as _gc
        params: dict[str, str] = {
            "MIN_EDGE_BPS": str(_gc("risk.min_edge_bps", default=_gc("strategy_filters.min_edge_bps", default=5))),
            "MDD_LIMIT": str(_gc("risk.max_daily_loss_pct", default=50.0)),
            "MAX_POSITION_SIZE": str(_gc("risk.max_position_usd", default=1000)),
            "SLIPPAGE_GAMMA": str(_gc("slippage.gamma", default=0.5)),
            "ENGINE_ENV": str(_gc("env", default="dev")),
            "EXECUTION_MODE": str(_gc("mode", default="paper")),
        }

        # Engine context params
        ctx = self._engine_context
        if ctx:
            settings = getattr(ctx, "settings", None)
            if settings:
                params["min_edge_bps"] = str(getattr(settings, "min_edge_bps", "N/A"))
                params["max_position_usd"] = str(getattr(settings, "max_position_usd", "N/A"))

        lines = ["⚙️ 핵심 파라미터\n━━━━━━━━━━━━━━━"]
        for key, val in params.items():
            lines.append(f"  {key}: {val}")
        return "\n".join(lines)

    async def _cmd_report(self, text: str, chat_id: int, message: dict) -> str | None:
        """수동 일일 리포트 즉시 생성."""
        snapshot = self._get_paper_snapshot()
        if not snapshot:
            return "📊 리포트 생성 불가 — Paper 데이터 없음"

        pnl = snapshot.get("total_pnl", 0.0)
        trades = snapshot.get("trades_executed", 0)
        wr = snapshot.get("win_rate", 0.0)
        mdd = snapshot.get("max_drawdown_pct", 0.0)
        by_strategy = snapshot.get("by_strategy", [])
        pf = snapshot.get("profit_factor", 0.0)

        emoji = "📈" if pnl >= 0 else "📉"
        lines = [
            f"{emoji} 리포트 ({datetime.datetime.now().strftime('%Y-%m-%d %H:%M')})\n"
            "━━━━━━━━━━━━━━━",
            f"💰 총 PnL: ${pnl:+,.6f}",
            f"🔁 총 거래: {trades}건",
            f"🎯 승률: {wr*100:.1f}%",
            f"📉 MDD: {mdd*100:.2f}%",
            f"📊 PF: {pf:.2f}" if pf else "",
        ]

        if by_strategy:
            lines.append("\n⚙️ 전략별:")
            for s in by_strategy:
                sid = s.get("strategy_id", "?")
                s_pnl = s.get("pnl", 0.0)
                s_trades = s.get("trades", 0)
                icon = "🟢" if s_pnl >= 0 else "🔴"
                lines.append(f"  {icon} {sid}: ${s_pnl:+,.4f} ({s_trades}건)")

        return "\n".join(l for l in lines if l)

    async def _cmd_help(self, text: str, chat_id: int, message: dict) -> str | None:
        return (
            "📖 LEVIATHAN Trade Bot\n"
            "━━━━━━━━━━━━━━━\n"
            "📋 조회\n"
            "  /status — 엔진 상태\n"
            "  /pnl — PnL 조회\n"
            "  /strategies — 전략 상태\n"
            "  /risk — 리스크 메트릭\n"
            "  /positions — 오픈 포지션\n"
            "  /fills — 최근 체결\n"
            "  /exchanges — 거래소 상태\n"
            "  /params — 핵심 파라미터\n"
            "  /report — 즉시 리포트\n\n"
            "🔧 제어\n"
            "  /kill — Kill Switch (확인)\n"
            "  /pause — 거래 중단\n"
            "  /resume — 거래 재개\n"
            "  /strategy &lt;name&gt; on|off\n"
            "  /whitelist [add|remove] SYM\n"
            "  /blacklist [add|remove] SYM\n\n"
            "📊 기타\n"
            "  /menu — 조회 메뉴\n"
            "  /settings — 알림 설정\n"
            "  /chart [type] — 차트\n"
            "  /alerts — 미해결 알림\n"
            "  /help — 이 도움말"
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
                    chat_id, message_id, "🔴 Kill Switch 활성화됨\n모든 거래가 중단되었습니다."
                )
                return "Kill Switch 활성화"
            else:
                self._pending_kills.pop(chat_id, None)
                await self.edit_message(
                    chat_id, message_id, "⏰ 시간 초과\n다시 /kill 명령을 입력하세요."
                )
                return "시간 초과"
        elif data == "kill_cancel":
            self._pending_kills.pop(chat_id, None)
            await self.edit_message(chat_id, message_id, "✅ 취소됨\n거래가 계속됩니다.")
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
        elif data == "menu_positions":
            text = await self._cmd_positions("/positions", chat_id, msg) or "포지션 없음"
        elif data == "menu_fills":
            text = await self._cmd_fills("/fills", chat_id, msg) or "체결 없음"
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
            f"✅ 알림 레벨 변경됨\n새 설정: {self._alert_level.value}",
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

    async def send_alert_kr(self, alert_type: str, data: dict) -> bool:
        """구조화 한국어 경보 위임."""
        return await self._alerter.send_alert_kr(alert_type, data)

    async def send_daily_report_kr(self, data: dict) -> bool:
        """일일 리포트 한국어 위임."""
        return await self._alerter.send_daily_report_kr(data)

    async def send_fill_kr(self, data: dict) -> bool:
        if self._alert_level == AlertLevel.CRITICAL_ONLY:
            return False
        return await self._alerter.send_fill_kr(data)

    async def send_fill_enhanced(self, data: dict) -> None:
        """Phase S20-B: 강화된 체결 알림 (기관급 포맷)."""
        if self._alert_level == AlertLevel.CRITICAL_ONLY:
            return
        strategy = data.get("strategy", "N/A")
        symbol = data.get("symbol", "N/A")
        buy_ex = data.get("buy_exchange", "?")
        sell_ex = data.get("sell_exchange", "?")
        pnl = data.get("pnl", 0.0)
        spread = data.get("spread_bps", 0.0)
        fee = data.get("fee", 0.0)
        slippage = data.get("slippage_bps", 0.0)
        latency = data.get("latency_ms", 0)

        # Mode prefix: caller passes full prefix or we derive from EXECUTION_MODE env
        _mode_prefixes = {
            "backtest": "⚪ [BACKTEST]",
            "paper": "🟢 [PAPER]",
            "live": "🔴 [LIVE]",
        }
        from src.core.config_loader import get_config as _gc
        _raw_mode = str(_gc("mode", default=os.getenv("EXECUTION_MODE", "paper"))).lower()
        _default_prefix = _mode_prefixes.get(_raw_mode, f"[{_raw_mode.upper()}]")
        mode = data.get("mode") or _default_prefix
        mode_prefix = f"{mode} "

        icon = "💰" if pnl > 0 else "💸"
        text = (
            f"{mode_prefix}{icon} 체결 완료\n"
            "━━━━━━━━━━━━━━━\n"
            f"⚙️ {strategy}\n"
            f"📌 {symbol}\n"
            f"🏦 {buy_ex} -> {sell_ex}\n\n"
            f"💵 PnL: ${pnl:+,.4f}\n"
            f"📊 스프레드: {spread:.1f} bps\n"
            f"💸 수수료: ${fee:.4f}\n"
            f"📉 슬리피지: {slippage:.1f} bps\n"
            f"⏱️ 체결 시간: {latency}ms"
        )
        await self.send_message(text)

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
                snapshot = self._get_paper_snapshot()
                if snapshot:
                    await self._alerter.send_daily_report_kr(snapshot)
            except Exception:
                logger.error("daily_report_failed", exc_info=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_paper_snapshot(self) -> dict | None:
        ctx = self._engine_context
        if not ctx:
            return None
        shadow = getattr(ctx, "paper_mode", None)
        if not shadow:
            return None
        try:
            return shadow.get_snapshot()
        except Exception:
            return None

    async def _get_pnl_text(self) -> str:
        snapshot = self._get_paper_snapshot()
        if not snapshot:
            return (
                "📊 PnL 조회\n"
                "━━━━━━━━━━━━━━━\n"
                "엔진 미연결 — 데이터 없음\n"
                "엔진 시작 후 다시 시도하세요."
            )
        pnl = snapshot.get("total_pnl", 0.0)
        wr = snapshot.get("win_rate", 0.0)
        trades = snapshot.get("trades_executed", 0)
        dd = snapshot.get("max_drawdown_pct", 0.0)
        emoji = "📈" if pnl >= 0 else "📉"
        return (
            f"{emoji} PnL 현황\n"
            "━━━━━━━━━━━━━━━\n"
            f"💰 총 PnL: ${pnl:+,.6f}\n"
            f"🎯 승률: {wr * 100:.1f}%\n"
            f"🔁 거래: {trades}건\n"
            f"📉 MDD: {dd * 100:.2f}%"
        )

    async def _get_strategies_text(self) -> str:
        snapshot = self._get_paper_snapshot()
        if not snapshot:
            return (
                "⚙️ 전략 상태\n"
                "━━━━━━━━━━━━━━━\n"
                "엔진 미연결 — 데이터 없음"
            )
        by_strategy = snapshot.get("by_strategy", [])
        if not by_strategy:
            return "⚙️ 전략 상태\n━━━━━━━━━━━━━━━\n등록된 전략 없음"
        lines = ["⚙️ 전략 상태\n━━━━━━━━━━━━━━━"]
        for s in by_strategy:
            sid = s.get("strategy_id", "?")
            s_pnl = s.get("pnl", 0.0)
            s_trades = s.get("trades", 0)
            icon = "🟢" if s_trades > 0 else "⚪"
            lines.append(f"{icon} {sid}\n   ${s_pnl:+,.4f} | {s_trades}건")
        return "\n".join(lines)

    async def _get_risk_text(self) -> str:
        snapshot = self._get_paper_snapshot()
        mdd = snapshot.get("max_drawdown_pct", 0.0) if snapshot else 0.0
        ctx = self._engine_context
        kill_active = getattr(ctx, "kill_switch_active", False) if ctx else False
        cb_state = getattr(ctx, "circuit_breaker_state", "N/A") if ctx else "N/A"
        return (
            "🛡️ 리스크 메트릭\n"
            "━━━━━━━━━━━━━━━\n"
            f"📉 MDD: {mdd * 100:.2f}%\n"
            f"🔴 Kill Switch: {'활성' if kill_active else '비활성'}\n"
            f"🔵 Circuit Breaker: {cb_state}"
        )

    async def _get_exchanges_text(self) -> str:
        ctx = self._engine_context
        if not ctx:
            return "🏦 거래소 상태\n━━━━━━━━━━━━━━━\n엔진 미연결 — 데이터 없음"

        exchanges: dict[str, float] = getattr(ctx, "exchange_health", {})
        latencies: dict[str, float] = getattr(ctx, "exchange_latencies", {})

        if not exchanges:
            return "🏦 거래소 상태\n━━━━━━━━━━━━━━━\n정보 없음"

        lines = ["🏦 거래소 상태\n━━━━━━━━━━━━━━━"]
        for ex_id, health in sorted(exchanges.items()):
            icon = "🟢" if health > 0.95 else ("🟡" if health > 0.5 else "🔴")
            lat = latencies.get(ex_id, 0)
            lat_str = f" | {lat:.0f}ms" if lat > 0 else ""
            lines.append(f"{icon} {ex_id}: {health * 100:.0f}%{lat_str}")
        return "\n".join(lines)
