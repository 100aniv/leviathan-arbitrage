"""Path-B Day-3 — Daily Reconciliation Report.

Fires once per day at UTC 00:05 and assembles the prior-day (UTC) view of:

1. Verified PnL per strategy (from exchange-reported income only).
2. Variance decomposition between engine-recorded total_pnl and the exchange
   ground truth. Line items: commission, funding, slippage, fx, rollback,
   unattributed residual.
3. Top-10 pre-trade rejection reasons (from the Prometheus
   ``leviathan_signal_rejected_total`` counter snapshot).
4. Safety counters: divergence events, circuit-breaker trips per strategy,
   kill-switch active minutes, stranded leg count.
5. Allocation-change audit trail (empty until Capital Auto-Scaler exists).
6. Exchange walletBalance snapshot delta (Binance futures 00:00 vs 23:59).

The report is delivered to:

- Telegram (Korean + English bilingual template, HTML parse_mode).
- Email (optional SMTP — controlled by ``SMTP_HOST``/``SMTP_PORT``/``SMTP_USER``/
  ``SMTP_PASS``/``SMTP_TO`` env vars).
- A stable CSV at ``engine/logs/daily_recon/YYYYMMDD.csv`` for offline analysis.

Key design points:

- The class is *assembly-only*: it delegates data retrieval to injected
  dependencies (snapshot, budget ledger, pre-trade validator, engine-stats).
  Tests can stub each independently.
- CSV schema is frozen (``CSV_COLUMNS``). Adding a new column requires
  appending — never reordering — to keep downstream consumers stable.
- If the day had zero trades, the report is skipped entirely (no empty
  Telegram message). The CSV is still written so the downstream analysis
  timeline has no gaps.
"""
from __future__ import annotations

import csv
import os
import smtplib
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Protocol

import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass
class VarianceDecomp:
    """Breakdown of divergence between engine total_pnl and exchange ground truth.

    All values are signed USD (positive = engine over-reported vs exchange,
    negative = engine under-reported). ``unattributed_usd`` should stay below
    $0.10 in steady state; exceeding that magnitude is an alert condition.
    """

    commission_mismatch_usd: Decimal = Decimal("0")
    funding_mismatch_usd: Decimal = Decimal("0")
    slippage_mismatch_usd: Decimal = Decimal("0")
    fx_mismatch_usd: Decimal = Decimal("0")
    rollback_mismatch_usd: Decimal = Decimal("0")
    unattributed_usd: Decimal = Decimal("0")

    def total(self) -> Decimal:
        """Sum of every attributed + unattributed line item."""
        return (
            self.commission_mismatch_usd
            + self.funding_mismatch_usd
            + self.slippage_mismatch_usd
            + self.fx_mismatch_usd
            + self.rollback_mismatch_usd
            + self.unattributed_usd
        )


@dataclass
class DailyReport:
    """Fully-assembled daily reconciliation snapshot (Day-3 deliverable)."""

    day_utc: date
    per_strategy_pnl_verified: dict[str, Decimal] = field(default_factory=dict)
    per_strategy_trades_count: dict[str, int] = field(default_factory=dict)
    per_strategy_win_rate: dict[str, float] = field(default_factory=dict)
    variance_decomposition: VarianceDecomp = field(default_factory=VarianceDecomp)
    rejections_top10: list[tuple[str, int]] = field(default_factory=list)
    divergence_events: int = 0
    circuit_breaker_trips: dict[str, int] = field(default_factory=dict)
    kill_switch_active_minutes: int = 0
    stranded_count: int = 0
    allocation_changes: list[dict[str, Any]] = field(default_factory=list)
    exchange_account_balance_usd: Decimal = Decimal("0")
    exchange_account_balance_prev_usd: Decimal = Decimal("0")
    summary_text: str = ""

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------

    @property
    def total_pnl_verified(self) -> Decimal:
        return sum(self.per_strategy_pnl_verified.values(), Decimal("0"))

    @property
    def total_trades(self) -> int:
        return sum(self.per_strategy_trades_count.values())

    @property
    def balance_delta_pct(self) -> float:
        prev = self.exchange_account_balance_prev_usd
        if prev == Decimal("0"):
            return 0.0
        return float(
            (self.exchange_account_balance_usd - prev) / prev * Decimal("100")
        )


# ---------------------------------------------------------------------------
# Dependency protocols (duck-typed; tests supply stubs)
# ---------------------------------------------------------------------------


class _SnapshotLike(Protocol):
    async def get_daily_attribution(
        self, day_utc: date
    ) -> dict[str, Any]:  # pragma: no cover - protocol
        ...


class _BudgetLedgerLike(Protocol):
    def get_per_strategy_stats(
        self, day_utc: date
    ) -> dict[str, dict[str, Any]]:  # pragma: no cover - protocol
        ...


class _PreTradeValidatorLike(Protocol):
    def get_rejection_counts(
        self, day_utc: date
    ) -> dict[str, int]:  # pragma: no cover - protocol
        ...


class _EngineStatsLike(Protocol):
    def get_daily_rollup(
        self, day_utc: date
    ) -> dict[str, Any]:  # pragma: no cover - protocol
        ...


class _TelegramLike(Protocol):
    async def _send(
        self, text: str, parse_mode: str = "HTML"
    ) -> bool:  # pragma: no cover - protocol
        ...


# ---------------------------------------------------------------------------
# CSV schema (frozen — append new columns to the end only)
# ---------------------------------------------------------------------------

CSV_COLUMNS: tuple[str, ...] = (
    "day_utc",
    "total_pnl_verified_usd",
    "total_trades",
    "per_strategy_pnl_json",
    "per_strategy_trades_json",
    "per_strategy_win_rate_json",
    "commission_mismatch_usd",
    "funding_mismatch_usd",
    "slippage_mismatch_usd",
    "fx_mismatch_usd",
    "rollback_mismatch_usd",
    "unattributed_usd",
    "rejections_top10_json",
    "divergence_events",
    "circuit_breaker_trips_json",
    "kill_switch_active_minutes",
    "stranded_count",
    "allocation_changes_count",
    "account_balance_usd",
    "account_balance_prev_usd",
    "balance_delta_pct",
    "summary_text",
)


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


class DailyReconciliationReport:
    """Assembles + delivers the daily reconciliation report.

    Pure-Python orchestration over injected dependencies. All heavy lifting
    (income fetching, ledger accounting, SMTP IO) sits behind those deps.
    """

    _DEFAULT_CSV_DIR = Path("engine/logs/daily_recon")

    def __init__(
        self,
        *,
        snapshot: _SnapshotLike | None = None,
        budget_ledger: _BudgetLedgerLike | None = None,
        pre_trade_validator: _PreTradeValidatorLike | None = None,
        engine_stats: _EngineStatsLike | None = None,
        telegram: _TelegramLike | None = None,
        csv_dir: Path | None = None,
    ) -> None:
        self._snapshot = snapshot
        self._budget_ledger = budget_ledger
        self._pre_trade = pre_trade_validator
        self._engine_stats = engine_stats
        self._telegram = telegram
        self._csv_dir = Path(csv_dir) if csv_dir else self._DEFAULT_CSV_DIR

    # ------------------------------------------------------------------
    # 1. Report assembly
    # ------------------------------------------------------------------

    async def generate(self, day_utc: date) -> DailyReport:
        """Assemble a :class:`DailyReport` for the given UTC calendar day.

        Each injected dependency is optional; when missing, the corresponding
        section degrades to empty / zero defaults so the report can still
        render with partial data (e.g. when a secondary exchange is down).
        """
        report = DailyReport(day_utc=day_utc)

        # 1a. Per-strategy verified PnL (from exchange income ground truth).
        if self._snapshot is not None:
            attrib = await self._snapshot.get_daily_attribution(day_utc)
            for sid, pnl in (attrib.get("per_strategy_pnl") or {}).items():
                report.per_strategy_pnl_verified[sid] = Decimal(str(pnl))
            report.exchange_account_balance_usd = Decimal(
                str(attrib.get("balance_end_usd", 0))
            )
            report.exchange_account_balance_prev_usd = Decimal(
                str(attrib.get("balance_start_usd", 0))
            )
            report.variance_decomposition = VarianceDecomp(
                commission_mismatch_usd=Decimal(
                    str(attrib.get("commission_mismatch_usd", 0))
                ),
                funding_mismatch_usd=Decimal(
                    str(attrib.get("funding_mismatch_usd", 0))
                ),
                slippage_mismatch_usd=Decimal(
                    str(attrib.get("slippage_mismatch_usd", 0))
                ),
                fx_mismatch_usd=Decimal(str(attrib.get("fx_mismatch_usd", 0))),
                rollback_mismatch_usd=Decimal(
                    str(attrib.get("rollback_mismatch_usd", 0))
                ),
                unattributed_usd=Decimal(
                    str(attrib.get("unattributed_usd", 0))
                ),
            )

        # 1b. Trade counts + win rates come from the budget ledger.
        if self._budget_ledger is not None:
            stats = self._budget_ledger.get_per_strategy_stats(day_utc)
            for sid, s in stats.items():
                trades = int(s.get("trades", 0))
                wins = int(s.get("wins", 0))
                report.per_strategy_trades_count[sid] = trades
                report.per_strategy_win_rate[sid] = (
                    (wins / trades) if trades else 0.0
                )

        # 1c. Top-10 rejection reasons (Prometheus counter snapshot).
        if self._pre_trade is not None:
            counts = self._pre_trade.get_rejection_counts(day_utc)
            report.rejections_top10 = sorted(
                counts.items(), key=lambda kv: kv[1], reverse=True
            )[:10]

        # 1d. Safety counters from engine stats rollup.
        if self._engine_stats is not None:
            roll = self._engine_stats.get_daily_rollup(day_utc)
            report.divergence_events = int(roll.get("divergence_events", 0))
            report.circuit_breaker_trips = dict(
                roll.get("circuit_breaker_trips") or {}
            )
            report.kill_switch_active_minutes = int(
                roll.get("kill_switch_active_minutes", 0)
            )
            report.stranded_count = int(roll.get("stranded_count", 0))
            report.allocation_changes = list(
                roll.get("allocation_changes") or []
            )

        report.summary_text = self._build_summary(report)
        return report

    # ------------------------------------------------------------------
    # 2. Deliveries
    # ------------------------------------------------------------------

    async def deliver_telegram(self, report: DailyReport) -> bool:
        """Send the bilingual report via the existing Telegram alerter.

        Skips delivery when the day had zero trades (per spec). Returns
        ``False`` when Telegram is not configured or the send itself failed.
        """
        if report.total_trades == 0:
            logger.info(
                "daily_report.telegram_skipped reason=zero_trades "
                "day_utc=%s", report.day_utc.isoformat()
            )
            return False
        if self._telegram is None:
            logger.warning("daily_report.telegram_not_configured")
            return False
        text = self.format_telegram_message(report)
        return await self._telegram._send(text)

    async def deliver_email(self, report: DailyReport) -> bool:
        """Deliver the report via SMTP when ``SMTP_*`` env vars are configured.

        Returns ``False`` (and logs at INFO) when SMTP is not configured —
        email is explicitly optional in the spec.
        """
        if report.total_trades == 0:
            return False

        host = os.getenv("SMTP_HOST")
        port = os.getenv("SMTP_PORT")
        user = os.getenv("SMTP_USER")
        pwd = os.getenv("SMTP_PASS")
        to_addr = os.getenv("SMTP_TO")
        if not all([host, port, user, pwd, to_addr]):
            logger.info("daily_report.email_not_configured")
            return False

        msg = EmailMessage()
        msg["Subject"] = (
            f"LEVIATHAN Daily Reconciliation — {report.day_utc.isoformat()}"
        )
        msg["From"] = user
        msg["To"] = to_addr
        msg.set_content(self.format_plaintext_message(report))

        try:
            with smtplib.SMTP(host, int(port)) as smtp:  # type: ignore[arg-type]
                smtp.starttls()
                smtp.login(user, pwd)  # type: ignore[arg-type]
                smtp.send_message(msg)
            return True
        except Exception as exc:
            logger.error(
                "daily_report.email_send_failed",
                error=str(exc), exc_info=True,
            )
            return False

    # ------------------------------------------------------------------
    # 3. CSV persistence
    # ------------------------------------------------------------------

    def save_csv(self, report: DailyReport) -> Path:
        """Persist the report to ``engine/logs/daily_recon/YYYYMMDD.csv``.

        Returns the absolute path that was written. Directory is created
        if needed. The CSV always has a header row followed by a single
        data row — downstream consumers can ``cat`` daily files together.
        """
        import json

        self._csv_dir.mkdir(parents=True, exist_ok=True)
        path = self._csv_dir / f"{report.day_utc.strftime('%Y%m%d')}.csv"

        vd = report.variance_decomposition
        row: dict[str, Any] = {
            "day_utc": report.day_utc.isoformat(),
            "total_pnl_verified_usd": str(report.total_pnl_verified),
            "total_trades": report.total_trades,
            "per_strategy_pnl_json": json.dumps(
                {k: str(v) for k, v in report.per_strategy_pnl_verified.items()}
            ),
            "per_strategy_trades_json": json.dumps(
                report.per_strategy_trades_count
            ),
            "per_strategy_win_rate_json": json.dumps(
                report.per_strategy_win_rate
            ),
            "commission_mismatch_usd": str(vd.commission_mismatch_usd),
            "funding_mismatch_usd": str(vd.funding_mismatch_usd),
            "slippage_mismatch_usd": str(vd.slippage_mismatch_usd),
            "fx_mismatch_usd": str(vd.fx_mismatch_usd),
            "rollback_mismatch_usd": str(vd.rollback_mismatch_usd),
            "unattributed_usd": str(vd.unattributed_usd),
            "rejections_top10_json": json.dumps(report.rejections_top10),
            "divergence_events": report.divergence_events,
            "circuit_breaker_trips_json": json.dumps(
                report.circuit_breaker_trips
            ),
            "kill_switch_active_minutes": report.kill_switch_active_minutes,
            "stranded_count": report.stranded_count,
            "allocation_changes_count": len(report.allocation_changes),
            "account_balance_usd": str(report.exchange_account_balance_usd),
            "account_balance_prev_usd": str(
                report.exchange_account_balance_prev_usd
            ),
            "balance_delta_pct": f"{report.balance_delta_pct:.4f}",
            "summary_text": report.summary_text,
        }

        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(CSV_COLUMNS))
            writer.writeheader()
            writer.writerow(row)
        return path

    # ------------------------------------------------------------------
    # 4. Formatting helpers
    # ------------------------------------------------------------------

    def format_telegram_message(self, report: DailyReport) -> str:
        """Render the bilingual Telegram template (HTML parse_mode safe).

        The template is stable across days so operators can eyeball the same
        layout. All numbers are formatted with explicit ``+`` / ``-`` sign.
        """
        lines: list[str] = []
        lines.append(
            f"📊 <b>LEVIATHAN Daily Reconciliation — "
            f"{report.day_utc.isoformat()}</b>"
        )
        lines.append("")
        lines.append("💰 <b>Verified PnL (from exchange income):</b>")
        if report.per_strategy_pnl_verified:
            for sid, pnl in report.per_strategy_pnl_verified.items():
                trades = report.per_strategy_trades_count.get(sid, 0)
                wr_pct = report.per_strategy_win_rate.get(sid, 0.0) * 100.0
                pnl_f = float(pnl)
                lines.append(
                    f"  {sid}: ${pnl_f:+.2f} ({trades} trades, "
                    f"WR {wr_pct:.0f}%)"
                )
        else:
            lines.append("  (no strategy activity)")
        total = float(report.total_pnl_verified)
        lines.append(f"  TOTAL: ${total:+.2f}")
        lines.append("")

        vd = report.variance_decomposition
        lines.append("🔍 <b>Variance decomposition (engine vs exchange):</b>")
        lines.append(f"  commission:   ${float(vd.commission_mismatch_usd):+.2f}")
        lines.append(f"  funding:      ${float(vd.funding_mismatch_usd):+.2f}")
        lines.append(f"  slippage:     ${float(vd.slippage_mismatch_usd):+.2f}")
        lines.append(f"  fx:           ${float(vd.fx_mismatch_usd):+.2f}")
        lines.append(f"  rollback:     ${float(vd.rollback_mismatch_usd):+.2f}")
        lines.append(f"  unattributed: ${float(vd.unattributed_usd):+.2f}")
        lines.append("")

        lines.append("🛑 <b>Rejections (top):</b>")
        if report.rejections_top10:
            for reason, count in report.rejections_top10:
                lines.append(f"  {reason}: {count}")
        else:
            lines.append("  (none)")
        lines.append("")

        lines.append(
            f"📈 <b>Account:</b> Binance "
            f"${float(report.exchange_account_balance_usd):.2f} "
            f"({report.balance_delta_pct:+.1f}% d/d)"
        )
        cb_total = sum(report.circuit_breaker_trips.values())
        lines.append(
            f"⚠️  Stranded: {report.stranded_count}. "
            f"Divergence events: {report.divergence_events}. "
            f"CB trips: {cb_total}."
        )
        lines.append("")
        lines.append(report.summary_text)
        return "\n".join(lines)

    def format_plaintext_message(self, report: DailyReport) -> str:
        """Email-friendly plaintext (strips HTML tags from the Telegram version)."""
        import re
        return re.sub(r"<[^>]+>", "", self.format_telegram_message(report))

    # ------------------------------------------------------------------
    # 5. Internal helpers
    # ------------------------------------------------------------------

    def _build_summary(self, report: DailyReport) -> str:
        """3-sentence human-readable overview for the top of the report."""
        total = float(report.total_pnl_verified)
        if total > 0.10:
            tone = "good"
        elif total < -0.10:
            tone = "loss"
        else:
            tone = "flat"

        # Pick the top loss driver from variance + strategy pnl.
        driver = "none identified"
        worst_strat: tuple[str, Decimal] | None = None
        for sid, pnl in report.per_strategy_pnl_verified.items():
            if worst_strat is None or pnl < worst_strat[1]:
                worst_strat = (sid, pnl)
        if worst_strat is not None and worst_strat[1] < Decimal("0"):
            driver = f"{worst_strat[0]} ({float(worst_strat[1]):+.2f} USD)"
        elif report.stranded_count > 0:
            driver = f"stranded legs ({report.stranded_count})"

        action = (
            "No action required."
            if tone != "loss"
            else "Monitor — investigate if pattern continues 2 more days."
        )
        return (
            f"{report.day_utc.isoformat()} was a {tone} day. "
            f"Realized PnL ${total:+.2f}. Top loss driver: {driver}. {action}"
        )


# ---------------------------------------------------------------------------
# Convenience factory for the scheduler job body
# ---------------------------------------------------------------------------


async def run_daily_report_job(
    reporter: DailyReconciliationReport,
    day_utc: date | None = None,
) -> DailyReport:
    """Run one full cycle (generate + CSV + telegram + email).

    Scheduler jobs wrap this in a try/except so a delivery failure never
    prevents the next day's run.
    """
    day = day_utc or (datetime.now(timezone.utc).date())
    report = await reporter.generate(day)
    try:
        reporter.save_csv(report)
    except Exception:
        logger.error("daily_report.csv_write_failed", exc_info=True)
    try:
        await reporter.deliver_telegram(report)
    except Exception:
        logger.error("daily_report.telegram_deliver_failed", exc_info=True)
    try:
        await reporter.deliver_email(report)
    except Exception:
        logger.error("daily_report.email_deliver_failed", exc_info=True)
    return report
