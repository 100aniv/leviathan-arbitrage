"""LEVIATHAN Telegram Alerter.

Async, fire-and-forget Telegram bot integration.
Rate limited to 20 msgs/min (Telegram API limit).
Failures are logged but NEVER crash the engine.
"""
from __future__ import annotations

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
        self._bot_token: str | None = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
        self._chat_id: str | None = chat_id or os.getenv("TELEGRAM_CHAT_ID")

        # If caller explicitly passed True/False, respect it.
        # Only fall back to env var when enabled is None (not provided).
        if enabled is not None:
            self._enabled = enabled
        else:
            self._enabled = os.getenv("TELEGRAM_ENABLED", "false").lower() == "true"

        # Sliding-window rate limiter: stores timestamps of recent sends.
        self._send_times: deque[float] = deque()

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
        emoji = _LEVEL_EMOJI.get(level.upper(), "ℹ️")
        text = f"{emoji} <b>[{level.upper()}]</b>\n{message}"
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
            "🚨 <b>KILL SWITCH ACTIVATED</b>",
            "",
            "<b>Timing Breakdown:</b>",
            f"  Tier 1 (local halt):      {_fmt(event.tier1_latency_ms)}",
            f"  Tier 2 (cancel orders):   {_fmt(event.tier2_latency_ms)}",
            f"  Tier 3 (close positions): {_fmt(event.tier3_latency_ms)}",
            "",
            f"<b>Cancelled orders:</b>  {len(event.cancelled_orders)}",
            f"<b>Closed positions:</b>  {len(event.closed_positions)}",
            f"<b>Redis halt set:</b>    {'Yes' if event.redis_halt_set else 'No'}",
        ]

        if event.errors:
            lines.append("")
            lines.append(f"<b>Errors ({len(event.errors)}):</b>")
            for err in event.errors[:5]:  # cap to avoid truncation
                lines.append(f"  • {err}")
            if len(event.errors) > 5:
                lines.append(f"  … and {len(event.errors) - 5} more")

        return await self._send("\n".join(lines))

    async def send_circuit_breaker_event(self, state: str, reason: str) -> bool:
        """Send a circuit-breaker state-change alert.

        Args:
            state:  New circuit-breaker state (e.g. "OPEN", "CLOSED", "HALF_OPEN").
            reason: Human-readable reason for the transition.

        Returns:
            True if the message was sent successfully, False otherwise.
        """
        state_upper = state.upper()
        emoji = "🔴" if state_upper == "OPEN" else ("🟡" if state_upper == "HALF_OPEN" else "🟢")
        text = (
            f"{emoji} <b>CIRCUIT BREAKER: {state_upper}</b>\n"
            f"<b>Reason:</b> {reason}"
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
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(url, json=payload)
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


# ---------------------------------------------------------------------------
# Workflow Alerter (WORKFLOW_TELEGRAM_BOT_TOKEN — separated from trading alerts)
# ---------------------------------------------------------------------------


class WorkflowTelegramAlerter(TelegramAlerter):
    """Workflow-specific Telegram alerter for LEVIATHAN Stage-Gate notifications.

    Uses WORKFLOW_TELEGRAM_BOT_TOKEN / WORKFLOW_TELEGRAM_CHAT_ID env vars,
    completely separated from the trading alert bot (TELEGRAM_BOT_TOKEN).

    Alert types:
        - Phase completion (Stage E): summary + CEO approval request
        - L5 escalation: same Phase failed 3+ times
        - Context 60% warning: /clear 시도 예고
        - Context clear success: 자동 재개 알림
        - Context clear failed: needs manual intervention
    """

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
        enabled: bool | None = None,
    ) -> None:
        super().__init__(
            bot_token=bot_token or os.getenv("WORKFLOW_TELEGRAM_BOT_TOKEN"),
            chat_id=chat_id or os.getenv("WORKFLOW_TELEGRAM_CHAT_ID"),
            enabled=enabled if enabled is not None else (
                os.getenv("WORKFLOW_TELEGRAM_ENABLED", "false").lower() == "true"
            ),
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


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_telegram_alerter() -> TelegramAlerter:
    """Create a TelegramAlerter from environment variables.

    Reads:
        TELEGRAM_BOT_TOKEN  — bot token from @BotFather
        TELEGRAM_CHAT_ID    — target chat/group ID
        TELEGRAM_ENABLED    — "true" to enable (default "false")

    Returns:
        Configured TelegramAlerter instance.
    """
    return TelegramAlerter(
        bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
        chat_id=os.getenv("TELEGRAM_CHAT_ID"),
        enabled=os.getenv("TELEGRAM_ENABLED", "false").lower() == "true",
    )


def get_workflow_alerter() -> WorkflowTelegramAlerter:
    """Create a WorkflowTelegramAlerter from environment variables.

    Reads:
        WORKFLOW_TELEGRAM_BOT_TOKEN  — workflow bot token from @BotFather
        WORKFLOW_TELEGRAM_CHAT_ID    — CEO chat ID
        WORKFLOW_TELEGRAM_ENABLED    — "true" to enable (default "false")

    Returns:
        Configured WorkflowTelegramAlerter instance.
    """
    return WorkflowTelegramAlerter()
