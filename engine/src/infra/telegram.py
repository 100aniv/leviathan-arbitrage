"""LEVIATHAN Telegram Alerter.

Async, fire-and-forget Telegram bot integration.
Rate limited to 20 msgs/min (Telegram API limit).
Failures are logged but NEVER crash the engine.

US-207: Korean infra bot templates (daily report, kill switch, circuit breaker, DB)
US-208: Korean trade bot templates (fill notification, daily settlement)
US-209: Severity-based rate limiting (EMERGENCY/CRITICAL/WARNING/INFO)
"""
from __future__ import annotations

import enum
import os
import time
from collections import deque
from typing import TYPE_CHECKING, Any

import httpx
import structlog

# Suppress httpx request logging to prevent Telegram bot token leakage in logs
logging_module = __import__("logging")
logging_module.getLogger("httpx").setLevel(logging_module.WARNING)

if TYPE_CHECKING:
    from src.risk.kill_switch import KillSwitchEvent
    from src.core.models import Signal

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Level emoji mapping
# ---------------------------------------------------------------------------
_LEVEL_EMOJI: dict[str, str] = {
    "INFO": "ℹ️",
    "WARNING": "⚠️",
    "ERROR": "❌",
    "CRITICAL": "🚨",
}


# ---------------------------------------------------------------------------
# US-209: Alert severity enum & rate-limit intervals
# ---------------------------------------------------------------------------


class AlertSeverity(enum.Enum):
    """Alert severity levels with per-level rate-limit intervals (seconds)."""

    EMERGENCY = 0    # Send immediately, no throttle
    CRITICAL = 60    # At most once per 60s
    WARNING = 300    # At most once per 300s
    INFO = 1800      # Batch: at most once per 1800s

    @property
    def min_interval(self) -> int:
        return self.value


class SeverityFilter:
    """Per-severity rate limiter for Telegram alerts (US-209).

    Tracks the last send time per severity level and decides whether
    a new message of that severity should be sent.
    """

    def __init__(self) -> None:
        self._last_sent: dict[AlertSeverity, float] = {}

    def should_send(self, severity: AlertSeverity) -> bool:
        """Return True if enough time has elapsed since the last send for this severity."""
        if severity == AlertSeverity.EMERGENCY:
            return True
        now = time.monotonic()
        last = self._last_sent.get(severity, 0.0)
        return (now - last) >= severity.min_interval

    def record_send(self, severity: AlertSeverity) -> None:
        """Record that a message of this severity was just sent."""
        self._last_sent[severity] = time.monotonic()

    def reset(self) -> None:
        """Clear all rate-limit state."""
        self._last_sent.clear()


class TelegramAlerter:
    """Async Telegram alerter with rate limiting.

    Fire-and-forget design: failures are logged but never crash the engine.
    Rate limited to 20 msgs/min to respect Telegram API limits.

    Configuration (env vars, overridable via constructor):
        TELEGRAM_BOT_TOKEN  — bot token from @BotFather
        TELEGRAM_CHAT_ID    — target chat/group ID
        TELEGRAM_ENABLED    — "true" to enable (default "false")
    """

    TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
    MAX_MESSAGES_PER_MINUTE = 20

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
        enabled: bool | None = None,
    ) -> None:
        """Initialise the alerter.

        Args:
            bot_token: Telegram bot token. Falls back to TELEGRAM_BOT_TOKEN env var.
            chat_id:   Target chat or group ID. Falls back to TELEGRAM_CHAT_ID env var.
            enabled:   Whether alerting is active. Falls back to TELEGRAM_ENABLED env var.
                       Pass ``False`` explicitly to force-disable regardless of env.
        """
        self._bot_token: str | None = bot_token or os.getenv("TRADE_TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
        self._chat_id: str | None = chat_id or os.getenv("TRADE_TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")

        # If caller explicitly passed True/False, respect it.
        # Only fall back to env var when enabled is None (not provided).
        if enabled is not None:
            self._enabled = enabled
        else:
            self._enabled = (
                os.getenv("TRADE_TELEGRAM_ENABLED")
                or os.getenv("TELEGRAM_ENABLED", "false")
            ).lower() == "true"

        # Sliding-window rate limiter: stores timestamps of recent sends.
        self._send_times: deque[float] = deque()
        # US-209: Per-severity rate limiter
        self._severity_filter = SeverityFilter()
        # Reusable HTTP client for connection pooling (US-168)
        self._http_client: httpx.AsyncClient | None = None

    @property
    def bot_token(self) -> str | None:
        return self._bot_token

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ---------------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------------

    async def send_alert(self, message: str, level: str = "INFO") -> bool:
        """Send a plain alert message with an emoji prefix.

        Args:
            message: Human-readable alert text (HTML allowed).
            level:   Severity level — INFO, WARNING, ERROR, or CRITICAL.

        Returns:
            True if the message was sent successfully, False otherwise.
        """
        _LEVEL_KR = {
            "INFO": "정보",
            "WARNING": "경고",
            "ERROR": "오류",
            "CRITICAL": "긴급",
        }
        emoji = _LEVEL_EMOJI.get(level.upper(), "ℹ️")
        label = _LEVEL_KR.get(level.upper(), level.upper())
        text = f"{emoji} <b>[{label}]</b>\n{message}"
        return await self._send(text)

    async def send_kill_switch_event(self, event: "KillSwitchEvent") -> bool:
        """Send a kill-switch activation alert with full timing breakdown.

        Args:
            event: KillSwitchEvent dataclass from src.risk.kill_switch.

        Returns:
            True if the message was sent successfully, False otherwise.
        """
        def _fmt(ms: float | None) -> str:
            return f"{ms:.2f} ms" if ms is not None else "N/A"

        lines = [
            "🚨 <b>긴급: 킬 스위치 작동</b>",
            "",
            "<b>타이밍 분석:</b>",
            f"  1단계 (로컬 중단):   {_fmt(event.tier1_latency_ms)}",
            f"  2단계 (주문 취소):   {_fmt(event.tier2_latency_ms)}",
            f"  3단계 (포지션 청산): {_fmt(event.tier3_latency_ms)}",
            "",
            f"<b>취소 주문:</b>  {len(event.cancelled_orders)}",
            f"<b>청산 포지션:</b>  {len(event.closed_positions)}",
            f"<b>Redis 중단:</b>    {'예' if event.redis_halt_set else '아니오'}",
        ]

        if event.errors:
            lines.append("")
            lines.append(f"<b>오류 ({len(event.errors)}):</b>")
            for err in event.errors[:5]:  # cap to avoid truncation
                lines.append(f"  • {err}")
            if len(event.errors) > 5:
                lines.append(f"  … 외 {len(event.errors) - 5}건")

        return await self._send("\n".join(lines))

    async def send_circuit_breaker_event(self, state: str, reason: str) -> bool:
        """Send a circuit-breaker state-change alert.

        Args:
            state:  New circuit-breaker state (e.g. "OPEN", "CLOSED", "HALF_OPEN").
            reason: Human-readable reason for the transition.

        Returns:
            True if the message was sent successfully, False otherwise.
        """
        _STATE_KR = {
            "OPEN": "열림",
            "HALF_OPEN": "반열림",
            "CLOSED": "닫힘",
        }
        state_upper = state.upper()
        emoji = "🔴" if state_upper == "OPEN" else ("🟡" if state_upper == "HALF_OPEN" else "🟢")
        state_label = _STATE_KR.get(state_upper, state_upper)
        text = (
            f"{emoji} <b>서킷 브레이커: {state_label}</b>\n"
            f"<b>사유:</b> {reason}"
        )
        return await self._send(text)

    async def send_daily_summary(self, data: dict[str, Any]) -> bool:
        """Send the daily PnL and performance summary.

        Expected keys in ``data`` (all optional, shown as N/A if absent):
            - date (str): Trading date.
            - total_pnl (float|str): Realized PnL in USD.
            - trades (int): Number of completed round-trips.
            - win_rate (float): Win rate 0–1.
            - max_drawdown (float): Maximum intraday drawdown 0–1.
            - strategy (str): Strategy name.

        Args:
            data: Summary statistics dictionary.

        Returns:
            True if the message was sent successfully, False otherwise.
        """
        def _v(key: str, fmt: str = "{}") -> str:
            val = data.get(key)
            return fmt.format(val) if val is not None else "N/A"

        pnl = data.get("total_pnl")
        pnl_str = f"${float(pnl):+,.2f}" if pnl is not None else "N/A"
        pnl_emoji = "📈" if (pnl is not None and float(pnl) >= 0) else "📉"

        win_rate = data.get("win_rate")
        win_rate_str = f"{float(win_rate) * 100:.1f}%" if win_rate is not None else "N/A"

        dd = data.get("max_drawdown")
        dd_str = f"{float(dd) * 100:.2f}%" if dd is not None else "N/A"

        lines = [
            f"📊 <b>Daily Summary — {_v('date')}</b>",
            f"<b>Strategy:</b> {_v('strategy')}",
            "",
            f"{pnl_emoji} <b>Total PnL:</b>       {pnl_str}",
            f"🔁 <b>Trades:</b>          {_v('trades')}",
            f"🎯 <b>Win Rate:</b>         {win_rate_str}",
            f"📉 <b>Max Drawdown:</b>     {dd_str}",
        ]
        return await self._send("\n".join(lines))

    async def send_signal_found(self, signal: "Signal") -> bool:
        """Send an arbitrage signal notification.

        Args:
            signal: Signal model from src.core.models.

        Returns:
            True if the message was sent successfully, False otherwise.
        """
        spread_pct = float(signal.spread_pct) * 100
        confidence_pct = signal.confidence * 100

        net_profit = signal.metadata.get("net_profit", "N/A")
        net_edge = signal.metadata.get("net_edge_pct", "N/A")

        lines = [
            "💹 <b>ARBITRAGE SIGNAL</b>",
            f"<b>Strategy:</b>   {signal.strategy_id}",
            f"<b>Symbol:</b>     {signal.symbol}",
            "",
            f"<b>Buy:</b>        {signal.buy_exchange} @ {signal.buy_price}",
            f"<b>Sell:</b>       {signal.sell_exchange} @ {signal.sell_price}",
            f"<b>Spread:</b>     {spread_pct:.4f}%",
            f"<b>Volume:</b>     {signal.volume}",
            "",
            f"<b>Net Profit:</b> ${net_profit}",
            f"<b>Net Edge:</b>   {net_edge}%",
            f"<b>Confidence:</b> {confidence_pct:.1f}%",
        ]
        return await self._send("\n".join(lines))

    # ---------------------------------------------------------------------------
    # US-209: Severity-filtered alert
    # ---------------------------------------------------------------------------

    async def send_alert_with_severity(
        self, message: str, severity: AlertSeverity
    ) -> bool:
        """Send an alert respecting per-severity rate limits.

        Args:
            message: Human-readable alert text (HTML allowed).
            severity: AlertSeverity enum value.

        Returns:
            True if the message was sent, False if throttled or failed.
        """
        if not self._severity_filter.should_send(severity):
            logger.debug(
                "telegram_severity_throttled",
                severity=severity.name,
                min_interval=severity.min_interval,
            )
            return False
        level_map = {
            AlertSeverity.EMERGENCY: "CRITICAL",
            AlertSeverity.CRITICAL: "ERROR",
            AlertSeverity.WARNING: "WARNING",
            AlertSeverity.INFO: "INFO",
        }
        level = level_map.get(severity, "INFO")
        result = await self.send_alert(message, level=level)
        if result:
            self._severity_filter.record_send(severity)
        return result

    # ---------------------------------------------------------------------------
    # US-207: Korean infra bot templates
    # ---------------------------------------------------------------------------

    async def send_daily_report_kr(self, data: dict[str, Any]) -> bool:
        """일일 가동 리포트 (한국어).

        Expected keys in ``data``:
            - date (str): 날짜
            - total_pnl (float): 총 PnL (USD)
            - trades (int): 거래 수
            - win_rate (float): 승률 0-1
            - active_strategies (int): 활성 전략 수
            - exchange_status (dict[str, str]): 거래소별 상태 {id: "정상"/"장애"}
        """
        pnl = data.get("total_pnl")
        pnl_str = f"${float(pnl):+,.2f}" if pnl is not None else "N/A"
        pnl_emoji = "📈" if (pnl is not None and float(pnl) >= 0) else "📉"

        wr = data.get("win_rate")
        wr_str = f"{float(wr) * 100:.1f}%" if wr is not None else "N/A"

        exchanges = data.get("exchange_status", {})
        ex_lines = []
        for ex_id, status in sorted(exchanges.items()):
            icon = "🟢" if status == "정상" else "🔴"
            ex_lines.append(f"  {icon} {ex_id}: {status}")

        lines = [
            f"📊 <b>일일 가동 리포트 — {data.get('date', 'N/A')}</b>",
            "",
            f"{pnl_emoji} <b>총 PnL:</b> {pnl_str}",
            f"🔁 <b>거래 수:</b> {data.get('trades', 'N/A')}",
            f"🎯 <b>승률:</b> {wr_str}",
            f"⚙️ <b>활성 전략:</b> {data.get('active_strategies', 'N/A')}개",
            "",
            "<b>거래소 상태:</b>",
        ]
        lines.extend(ex_lines if ex_lines else ["  정보 없음"])
        return await self._send("\n".join(lines))

    async def send_alert_kr(self, alert_type: str, data: dict[str, Any]) -> bool:
        """구조화 경보 (한국어).

        Args:
            alert_type: 경보 유형 문자열.
                기존: "kill_switch" | "circuit_breaker" | "db_failure"
                신규: "shadow_start" | "shadow_daily_breakdown" |
                      "krw_soft_block" | "krw_killswitch" | "krw_recovered" |
                      "position_discrepancy" | "position_tracking_fail" |
                      "inventory_critical" | "inventory_rebalance" |
                      "order_cancel_fail" | "balance_mismatch" |
                      "orphan_positions" | "data_collector_start" |
                      "live_mode_start" | "shadow_mode_start"
            data: Alert-specific data dict.
        """
        if alert_type == "kill_switch":
            lines = [
                "🚨 <b>긴급: 킬 스위치 작동</b>",
                "",
                f"<b>사유:</b> {data.get('reason', '알 수 없음')}",
                f"<b>취소 주문:</b> {data.get('cancelled_orders', 0)}건",
                f"<b>청산 포지션:</b> {data.get('closed_positions', 0)}건",
                f"<b>Redis 중단:</b> {'예' if data.get('redis_halt') else '아니오'}",
            ]
        elif alert_type == "circuit_breaker":
            state = data.get("state", "UNKNOWN")
            emoji = "🔴" if state == "OPEN" else ("🟡" if state == "HALF_OPEN" else "🟢")
            lines = [
                f"{emoji} <b>서킷 브레이커: {state}</b>",
                "",
                f"<b>사유:</b> {data.get('reason', '알 수 없음')}",
            ]
        elif alert_type == "db_failure":
            lines = [
                "🔴 <b>DB 장애 감지</b>",
                "",
                f"<b>유형:</b> {data.get('db_type', 'TimescaleDB')}",
                f"<b>오류:</b> {data.get('error', '알 수 없음')}",
                f"<b>영향:</b> {data.get('impact', '데이터 저장 불가')}",
            ]
        # --- shadow.py templates ---
        elif alert_type == "signal_found":
            pnl = data.get("net_profit", 0)
            pnl_str = f"${float(pnl):+,.4f}" if pnl else "N/A"
            lines = [
                "💹 <b>차익거래 시그널 감지</b>",
                "",
                f"<b>전략:</b> {data.get('strategy', 'N/A')}",
                f"<b>심볼:</b> {data.get('symbol', 'N/A')}",
                f"<b>매수:</b> {data.get('buy_exchange', 'N/A')}",
                f"<b>매도:</b> {data.get('sell_exchange', 'N/A')}",
                f"<b>스프레드:</b> {data.get('spread_pct', 0):.2f}%",
                f"<b>예상 수익:</b> {pnl_str}",
            ]
        elif alert_type == "shadow_start":
            lines = [
                "🌑 <b>섀도 모드 시작</b>",
                "",
                "<b>모드:</b> 실데이터 + 페이퍼 실행",
            ]
        elif alert_type == "shadow_daily_breakdown":
            lines = ["📊 <b>전략별 성과</b>", ""]
            for sb in data.get("strategies", []):
                lines.append(
                    f"  • {sb.get('strategy_id', '?')}: "
                    f"{sb.get('trades', 0)}건 / "
                    f"{sb.get('win_rate', 0):.0%} 승률 / "
                    f"${sb.get('pnl', 0):+.4f}"
                )
            lines.extend([
                "",
                f"<b>거부:</b> {data.get('trades_rejected', 0)}건",
                f"<b>부분 체결:</b> {data.get('trades_partial_fill', 0)}건",
            ])
        elif alert_type == "krw_soft_block":
            lines = [
                "🟡 <b>KRW 환율 지연 — 소프트 차단</b>",
                "",
                f"<b>지연:</b> {data.get('stale_seconds', 0):.0f}초",
                f"<b>조치:</b> KRW 거래소 일시 차단",
            ]
        elif alert_type == "krw_killswitch":
            lines = [
                "🚨 <b>긴급: KRW 환율 장기 지연 — 킬 스위치</b>",
                "",
                f"<b>지연:</b> {data.get('stale_seconds', 0):.0f}초 (10분 이상)",
                "<b>조치:</b> 전체 킬 스위치 작동",
            ]
        elif alert_type == "krw_recovered":
            lines = [
                "🟢 <b>KRW 환율 복구</b>",
                "",
                "<b>조치:</b> 소프트 차단 해제",
            ]
        # --- main.py templates ---
        elif alert_type == "position_discrepancy":
            count = data.get("count", 0)
            summary = data.get("summary", "N/A")
            lines = [
                "🔴 <b>포지션 불일치 감지</b>",
                "",
                f"<b>건수:</b> {count}건",
                f"<b>상세:</b> {summary}",
            ]
        elif alert_type == "position_tracking_fail":
            lines = [
                "🔴 <b>포지션 추적 지속 실패</b>",
                "",
                f"<b>실패 횟수:</b> {data.get('error_count', 0)}회",
                "<b>영향:</b> 리스크 데이터 불신뢰",
            ]
        elif alert_type == "inventory_critical":
            lines = [
                "🚨 <b>인벤토리 심각한 불균형</b>",
                "",
                "<b>조치:</b> 즉시 확인 필요",
            ]
        elif alert_type == "inventory_rebalance":
            suggestions = data.get("suggestions", [])
            lines = [
                f"⚠️ <b>인벤토리 리밸런싱 필요 ({len(suggestions)}건)</b>",
                "",
            ]
            for s in suggestions:
                lines.append(
                    f"  • {s.get('from')} → {s.get('to')}: "
                    f"${s.get('amount_usd', 0):.0f} ({s.get('reason', '')})"
                )
        elif alert_type == "order_cancel_fail":
            lines = [
                "🔴 <b>주문 취소 실패</b>",
                "",
                f"<b>거래소:</b> {data.get('exchange', 'N/A')}",
                f"<b>주문 ID:</b> {data.get('order_id', 'N/A')}",
                f"<b>오류:</b> {data.get('error', '알 수 없음')}",
            ]
        elif alert_type == "balance_mismatch":
            lines = [
                "⚠️ <b>잔고 불일치</b>",
                "",
                f"<b>상세:</b> {data.get('detail', 'N/A')}",
            ]
        elif alert_type == "orphan_positions":
            lines = [
                "⚠️ <b>시작 시 미정리 포지션 발견</b>",
                "",
                f"<b>발견:</b> {data.get('found', 0)}건",
                f"<b>종료:</b> {data.get('closed', 0)}건",
                f"<b>재개:</b> {data.get('resumed', 0)}건",
            ]
        elif alert_type == "data_collector_start":
            lines = [
                "📡 <b>실 데이터 수집 시작</b>",
                "",
                f"<b>거래소:</b> {data.get('exchanges', 'N/A')}",
                f"<b>심볼:</b> {data.get('symbols', 'N/A')}",
            ]
        elif alert_type == "live_mode_start":
            lines = [
                "🚀 <b>라이브 모드 시작</b>",
                "",
                f"<b>거래소:</b> {data.get('exchanges', 'N/A')}",
                f"<b>심볼:</b> {data.get('symbols', 'N/A')}",
            ]
        elif alert_type == "shadow_mode_start":
            lines = [
                "🌑 <b>섀도 모드 활성화</b>",
                "",
                f"<b>거래소:</b> {data.get('exchanges', 'N/A')}",
                f"<b>심볼:</b> {data.get('symbols', 'N/A')}",
                f"<b>라이브게이트:</b> {data.get('live_gate', '비활성')}",
            ]
        else:
            lines = [
                f"⚠️ <b>경보: {alert_type}</b>",
                "",
                f"<b>상세:</b> {data.get('detail', '알 수 없음')}",
            ]
        return await self._send("\n".join(lines))

    # ---------------------------------------------------------------------------
    # US-208: Korean trade bot templates
    # ---------------------------------------------------------------------------

    async def send_fill_kr(self, data: dict[str, Any]) -> bool:
        """체결 알림 (한국어).

        Expected keys in ``data``:
            - strategy (str): 전략명
            - symbol (str): 심볼
            - buy_exchange (str): 매수 거래소
            - sell_exchange (str): 매도 거래소
            - pnl (float): 실현 PnL
            - spread_pct (float): 스프레드 %
        """
        pnl = data.get("pnl")
        pnl_str = f"${float(pnl):+,.4f}" if pnl is not None else "N/A"
        pnl_emoji = "💰" if (pnl is not None and float(pnl) >= 0) else "📉"

        lines = [
            f"{pnl_emoji} <b>체결 완료</b>",
            "",
            f"<b>전략:</b> {data.get('strategy', 'N/A')}",
            f"<b>심볼:</b> {data.get('symbol', 'N/A')}",
            f"<b>매수:</b> {data.get('buy_exchange', 'N/A')}",
            f"<b>매도:</b> {data.get('sell_exchange', 'N/A')}",
            f"<b>스프레드:</b> {data.get('spread_pct', 'N/A')}%",
            f"<b>PnL:</b> {pnl_str}",
        ]
        return await self._send("\n".join(lines))

    async def send_daily_settlement_kr(self, data: dict[str, Any]) -> bool:
        """일일 정산 리포트 (한국어).

        Expected keys in ``data``:
            - date (str): 날짜
            - total_pnl (float): 총 PnL
            - win_rate (float): 승률 0-1
            - total_trades (int): 거래 수
            - strategy_breakdown (list[dict]): 전략별 breakdown
              [{strategy, pnl, trades, win_rate}]
        """
        pnl = data.get("total_pnl")
        pnl_str = f"${float(pnl):+,.2f}" if pnl is not None else "N/A"
        pnl_emoji = "📈" if (pnl is not None and float(pnl) >= 0) else "📉"

        wr = data.get("win_rate")
        wr_str = f"{float(wr) * 100:.1f}%" if wr is not None else "N/A"

        lines = [
            f"📋 <b>일일 정산 리포트 — {data.get('date', 'N/A')}</b>",
            "",
            f"{pnl_emoji} <b>총 PnL:</b> {pnl_str}",
            f"🎯 <b>승률:</b> {wr_str}",
            f"🔁 <b>거래 수:</b> {data.get('total_trades', 'N/A')}",
            "",
            "<b>전략별 실적:</b>",
        ]

        breakdown = data.get("strategy_breakdown", [])
        if breakdown:
            for item in breakdown:
                s_pnl = item.get("pnl")
                s_pnl_str = f"${float(s_pnl):+,.4f}" if s_pnl is not None else "N/A"
                s_wr = item.get("win_rate")
                s_wr_str = f"{float(s_wr) * 100:.1f}%" if s_wr is not None else "N/A"
                lines.append(
                    f"  • {item.get('strategy', '?')}: {s_pnl_str} "
                    f"({item.get('trades', 0)}건, 승률 {s_wr_str})"
                )
        else:
            lines.append("  데이터 없음")

        return await self._send("\n".join(lines))

    # ---------------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------------

    def _check_rate_limit(self) -> bool:
        """Sliding-window rate limiter — max 20 messages per 60 seconds.

        Returns:
            True if a message may be sent, False if the rate limit is exceeded.
        """
        now = time.monotonic()
        window_start = now - 60.0

        # Evict timestamps outside the sliding window.
        while self._send_times and self._send_times[0] < window_start:
            self._send_times.popleft()

        if len(self._send_times) >= self.MAX_MESSAGES_PER_MINUTE:
            return False

        self._send_times.append(now)
        return True

    async def _send(self, text: str, parse_mode: str = "HTML") -> bool:
        """Perform the actual HTTP POST to the Telegram Bot API.

        All exceptions are caught and logged; the engine is never interrupted.

        Args:
            text:       Message text (HTML or Markdown depending on parse_mode).
            parse_mode: Telegram parse mode — "HTML" or "MarkdownV2".

        Returns:
            True if Telegram returned a successful response, False otherwise.
        """
        if not self._enabled:
            logger.debug("telegram_alerter_disabled", text_preview=text[:80])
            return False

        if not self._bot_token or not self._chat_id:
            logger.warning(
                "telegram_alerter_misconfigured",
                has_token=bool(self._bot_token),
                has_chat_id=bool(self._chat_id),
            )
            return False

        if not self._check_rate_limit():
            logger.warning(
                "telegram_rate_limit_exceeded",
                max_per_minute=self.MAX_MESSAGES_PER_MINUTE,
            )
            return False

        url = self.TELEGRAM_API.format(token=self._bot_token)
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }

        try:
            if self._http_client is None:
                self._http_client = httpx.AsyncClient(timeout=5.0)
            response = await self._http_client.post(url, json=payload)
            response.raise_for_status()
            logger.debug(
                "telegram_alert_sent",
                status_code=response.status_code,
                text_preview=text[:80],
            )
            return True
        except httpx.HTTPStatusError as exc:
            logger.error(
                "telegram_http_error",
                status_code=exc.response.status_code,
                response_text=exc.response.text[:200],
            )
            return False
        except httpx.TimeoutException:
            logger.error("telegram_timeout", timeout_seconds=5.0)
            return False
        except Exception as exc:
            logger.error("telegram_unexpected_error", error=str(exc), exc_info=True)
            return False

    async def close(self) -> None:
        """Close the reusable HTTP client (US-168)."""
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None


# ---------------------------------------------------------------------------
# Phase S21: WorkflowTelegramAlerter → DevTelegramBot (telegram_dev_bot.py)
# Kept as backward-compat alias for callers that import WorkflowTelegramAlerter
# ---------------------------------------------------------------------------

class _WorkflowTelegramAlerterCompat(TelegramAlerter):
    """DEPRECATED: Original WorkflowTelegramAlerter methods kept for reference.

    Use DevTelegramBot for all workflow notifications.
    """

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
        enabled: bool | None = None,
    ) -> None:
        _enabled = enabled if enabled is not None else (
            (os.getenv("DEV_TELEGRAM_ENABLED") or os.getenv("WORKFLOW_TELEGRAM_ENABLED", "false")).lower() == "true"
        )
        super().__init__(
            bot_token=bot_token or os.getenv("DEV_TELEGRAM_BOT_TOKEN") or os.getenv("WORKFLOW_TELEGRAM_BOT_TOKEN"),
            chat_id=chat_id or os.getenv("DEV_TELEGRAM_CHAT_ID") or os.getenv("WORKFLOW_TELEGRAM_CHAT_ID"),
            enabled=_enabled,
        )

    async def send_phase_complete(self, data: dict[str, Any]) -> bool:
        """Send Phase completion notification to CEO.

        Args:
            data: Phase completion data with keys:
                - phase (str): Phase name (e.g. "Phase K")
                - us_completed (list[str]): Completed US IDs
                - test_count (int): Number of tests passed
                - shadow_pnl (float): Shadow PnL result
                - shadow_wr (float): Shadow win rate 0-1
                - shadow_dd (float): Shadow max drawdown 0-1
                - files_changed (int): Number of files changed

        Returns:
            True if sent successfully.
        """
        wr = data.get("shadow_wr")
        wr_str = f"{float(wr) * 100:.1f}%" if wr is not None else "N/A"

        dd = data.get("shadow_dd")
        dd_str = f"{float(dd) * 100:.2f}%" if dd is not None else "N/A"

        pnl = data.get("shadow_pnl")
        pnl_str = f"${float(pnl):+,.4f}" if pnl is not None else "N/A"

        us_list = data.get("us_completed", [])
        us_str = ", ".join(us_list[:10])
        if len(us_list) > 10:
            us_str += f" ... +{len(us_list) - 10} more"

        lines = [
            f"<b>PHASE COMPLETE: {data.get('phase', 'Unknown')}</b>",
            "",
            f"<b>US:</b> {us_str}",
            f"<b>Tests:</b> {data.get('test_count', 'N/A')} passed",
            f"<b>Files changed:</b> {data.get('files_changed', 'N/A')}",
            "",
            "<b>Shadow Results:</b>",
            f"  PnL: {pnl_str}",
            f"  Win Rate: {wr_str}",
            f"  Max DD: {dd_str}",
            "",
            "<b>Action Required:</b> Reply to approve next Phase.",
        ]
        return await self._send("\n".join(lines))

    async def send_escalation(self, phase: str, failures: int, reason: str) -> bool:
        """Send L5 escalation alert when same Phase fails 3+ times.

        Args:
            phase: Phase name.
            failures: Number of consecutive failures.
            reason: Human-readable failure reason.

        Returns:
            True if sent successfully.
        """
        lines = [
            "<b>L5 ESCALATION</b>",
            "",
            f"<b>Phase:</b> {phase}",
            f"<b>Consecutive failures:</b> {failures}",
            f"<b>Reason:</b> {reason}",
            "",
            "<b>Action Required:</b> Manual intervention needed.",
        ]
        return await self._send("\n".join(lines))

    async def send_context_warning(self, stage: str, context_pct: int) -> bool:
        """Send context 60% warning — /clear attempt incoming.

        Args:
            stage: Current Stage (A-E).
            context_pct: Current context usage percentage.

        Returns:
            True if sent successfully.
        """
        lines = [
            "<b>CONTEXT WARNING</b>",
            "",
            f"<b>Current Stage:</b> {stage}",
            f"<b>Context Usage:</b> {context_pct}%",
            "<b>Status:</b> /clear 시도합니다.",
        ]
        return await self._send("\n".join(lines))

    async def send_context_clear_success(self, stage: str, next_stage: str) -> bool:
        """Send context clear success — auto-resuming from progress.json.

        Args:
            stage: Completed Stage (A-E).
            next_stage: Stage to resume from.

        Returns:
            True if sent successfully.
        """
        lines = [
            "<b>CONTEXT CLEARED</b>",
            "",
            f"<b>Completed Stage:</b> {stage}",
            f"<b>Resuming from:</b> {next_stage}",
            "<b>Status:</b> progress.json으로 자동 재개합니다.",
        ]
        return await self._send("\n".join(lines))

    async def send_context_alert(self, stage: str, handoff_path: str) -> bool:
        """Send context 60% alert when /clear fails.

        Args:
            stage: Current Stage (A-E).
            handoff_path: Path to handoff document.

        Returns:
            True if sent successfully.
        """
        lines = [
            "<b>CONTEXT LIMIT</b>",
            "",
            f"<b>Current Stage:</b> {stage}",
            f"<b>Handoff:</b> {handoff_path}",
            "<b>Status:</b> /clear failed, session paused.",
            "",
            "<b>Action Required:</b> Start new session with /leviathan.",
        ]
        return await self._send("\n".join(lines))


# Backward compat alias — callers that import WorkflowTelegramAlerter still work
WorkflowTelegramAlerter = _WorkflowTelegramAlerterCompat


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_telegram_alerter() -> TelegramAlerter:
    """Create a TelegramAlerter from environment variables.

    Phase S21: Reads TRADE_TELEGRAM_BOT_TOKEN first (3-Bot system),
    falls back to legacy TELEGRAM_BOT_TOKEN for backward compat.

    Returns:
        Configured TelegramAlerter instance.
    """
    return TelegramAlerter()


# ---------------------------------------------------------------------------
# US-220: Weekly auto-report scheduler
# ---------------------------------------------------------------------------


def _format_weekly_report(data: dict[str, Any]) -> str:
    """Format weekly report data into Korean HTML message.

    Expected keys in ``data`` (all optional):
        - week_start (str): 주간 시작일
        - week_end (str): 주간 종료일
        - total_pnl (float): 주간 총 PnL (USD)
        - win_rate (float): 승률 0-1
        - total_trades (int): 거래 수
        - sharpe_ratio (float): 7일 Sharpe ratio
        - strategy_breakdown (list[dict]): 전략별 실적
            [{strategy, pnl, trades, win_rate}]
    """
    pnl = data.get("total_pnl")
    pnl_str = f"${float(pnl):+,.2f}" if pnl is not None else "N/A"
    pnl_emoji = "📈" if (pnl is not None and float(pnl) >= 0) else "📉"

    wr = data.get("win_rate")
    wr_str = f"{float(wr) * 100:.1f}%" if wr is not None else "N/A"

    sharpe = data.get("sharpe_ratio")
    sharpe_str = f"{float(sharpe):.2f}" if sharpe is not None else "N/A"

    lines = [
        f"📊 <b>주간 리포트 — {data.get('week_start', '?')} ~ {data.get('week_end', '?')}</b>",
        "",
        f"{pnl_emoji} <b>총 PnL:</b> {pnl_str}",
        f"🎯 <b>승률:</b> {wr_str}",
        f"🔁 <b>거래 수:</b> {data.get('total_trades', 'N/A')}",
        f"📐 <b>Sharpe (7일):</b> {sharpe_str}",
        "",
        "<b>전략별 실적:</b>",
    ]

    breakdown = data.get("strategy_breakdown", [])
    if breakdown:
        for item in breakdown:
            s_pnl = item.get("pnl")
            s_pnl_str = f"${float(s_pnl):+,.4f}" if s_pnl is not None else "N/A"
            s_wr = item.get("win_rate")
            s_wr_str = f"{float(s_wr) * 100:.1f}%" if s_wr is not None else "N/A"
            lines.append(
                f"  • {item.get('strategy', '?')}: {s_pnl_str} "
                f"({item.get('trades', 0)}건, 승률 {s_wr_str})"
            )
    else:
        lines.append("  데이터 없음")

    return "\n".join(lines)


async def send_weekly_report(alerter: TelegramAlerter, data: dict[str, Any]) -> bool:
    """Send a formatted weekly report via the given alerter.

    Args:
        alerter: TelegramAlerter instance.
        data: Weekly report data dict.

    Returns:
        True if sent successfully.
    """
    text = _format_weekly_report(data)
    return await alerter._send(text)


def start_weekly_report_scheduler(
    alerter: TelegramAlerter,
    data_fn: Any | None = None,
) -> Any | None:
    """Start APScheduler CronTrigger for weekly report (일요일 23:59 KST).

    Args:
        alerter: TelegramAlerter to use for sending.
        data_fn: Async callable returning dict of weekly stats.
                 If None, sends a placeholder report.

    Returns:
        AsyncIOScheduler instance, or None if APScheduler unavailable.
    """
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.warning("weekly_report_apscheduler_not_available")
        return None

    scheduler = AsyncIOScheduler()

    async def _weekly_job() -> None:
        try:
            if data_fn is not None:
                data = await data_fn()
            else:
                import datetime
                today = datetime.date.today()
                week_start = today - datetime.timedelta(days=6)
                data = {
                    "week_start": week_start.isoformat(),
                    "week_end": today.isoformat(),
                }
            await send_weekly_report(alerter, data)
        except Exception:
            logger.error("weekly_report_job_failed", exc_info=True)

    # 일요일 23:59 KST (= UTC 14:59)
    trigger = CronTrigger(
        day_of_week="sun",
        hour=14,
        minute=59,
        timezone="UTC",
    )
    scheduler.add_job(_weekly_job, trigger, id="weekly_telegram_report")
    scheduler.start()
    logger.info("weekly_report_scheduler_started", schedule="Sun 23:59 KST")
    return scheduler


def get_workflow_alerter() -> TelegramAlerter:
    """DEPRECATED: Use DevTelegramBot instead.

    Phase S21: WorkflowTelegramAlerter removed. Returns a basic TelegramAlerter
    configured with DEV_TELEGRAM_BOT_TOKEN for backward compatibility.
    """
    return TelegramAlerter(
        bot_token=os.getenv("DEV_TELEGRAM_BOT_TOKEN") or os.getenv("WORKFLOW_TELEGRAM_BOT_TOKEN"),
        chat_id=os.getenv("DEV_TELEGRAM_CHAT_ID") or os.getenv("WORKFLOW_TELEGRAM_CHAT_ID"),
        enabled=(os.getenv("DEV_TELEGRAM_ENABLED") or os.getenv("WORKFLOW_TELEGRAM_ENABLED", "false")).lower() == "true",
    )
