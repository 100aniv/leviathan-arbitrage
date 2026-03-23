"""Shadow vs Virtual Live comparison reporter.

US-330: Generates periodic reports comparing Shadow execution quality
against theoretical Live execution to measure the simulation gap.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ComparisonReport:
    """Single comparison snapshot."""
    timestamp: float
    shadow_pnl: float
    virtual_live_pnl: float
    pnl_gap_pct: float
    shadow_is_p50_bps: float
    shadow_is_p95_bps: float
    shadow_fill_rate_pct: float
    shadow_trade_count: int
    report_period_h: float = 1.0


class ShadowLiveReporter:
    """Compares Shadow mode execution against Virtual Live estimates.

    Uses TCAAnalyzer data to measure slippage gaps and PnL differences.
    """

    def __init__(
        self,
        tca_analyzer: Any = None,
        output_path: str | Path = ".omc/state/shadow-live-report-latest.json",
    ) -> None:
        self._tca = tca_analyzer
        self._output_path = Path(output_path)
        self._reports: list[ComparisonReport] = []  # bounded by generate_report frequency (~24/day)
        self._max_reports = 1000
        self._last_report_time: float = 0.0
        self._cumulative_shadow_pnl: float = 0.0
        self._cumulative_virtual_pnl: float = 0.0
        self._trade_count: int = 0

    def record_trade(
        self,
        shadow_pnl: float,
        virtual_live_pnl: float | None = None,
    ) -> None:
        """Record a single trade's PnL for both Shadow and Virtual Live."""
        self._cumulative_shadow_pnl += shadow_pnl
        # Virtual Live PnL: None = not provided → use shadow_pnl as estimate
        if virtual_live_pnl is None:
            virtual_live_pnl = shadow_pnl
        self._cumulative_virtual_pnl += virtual_live_pnl
        self._trade_count += 1

    def generate_report(self) -> dict:
        """Generate a comparison report using TCA data."""
        now = time.time()
        tca_summary = {}
        if self._tca is not None:
            try:
                tca_summary = self._tca.get_summary()
            except Exception:
                pass

        pnl_gap_pct = 0.0
        if abs(self._cumulative_virtual_pnl) > 0.001:
            pnl_gap_pct = (
                (self._cumulative_shadow_pnl - self._cumulative_virtual_pnl)
                / abs(self._cumulative_virtual_pnl)
                * 100
            )

        report = ComparisonReport(
            timestamp=now,
            shadow_pnl=self._cumulative_shadow_pnl,
            virtual_live_pnl=self._cumulative_virtual_pnl,
            pnl_gap_pct=round(pnl_gap_pct, 2),
            shadow_is_p50_bps=tca_summary.get("is_p50_bps", 0.0),
            shadow_is_p95_bps=tca_summary.get("is_p95_bps", 0.0),
            shadow_fill_rate_pct=tca_summary.get("fill_rate_pct", 0.0),
            shadow_trade_count=self._trade_count,
        )
        self._reports.append(report)
        if len(self._reports) > self._max_reports:
            self._reports = self._reports[-self._max_reports:]
        self._last_report_time = now

        result = {
            "timestamp": now,
            "shadow_pnl": round(report.shadow_pnl, 4),
            "virtual_live_pnl": round(report.virtual_live_pnl, 4),
            "pnl_gap_pct": report.pnl_gap_pct,
            "slippage": {
                "is_p50_bps": report.shadow_is_p50_bps,
                "is_p95_bps": report.shadow_is_p95_bps,
            },
            "fill_rate_pct": report.shadow_fill_rate_pct,
            "trade_count": report.shadow_trade_count,
            "report_count": len(self._reports),
        }

        # Write to file
        try:
            self._output_path.parent.mkdir(parents=True, exist_ok=True)
            self._output_path.write_text(json.dumps(result, indent=2))
            logger.info("shadow_live_report_written path=%s trades=%d", self._output_path, self._trade_count)
        except Exception as exc:
            logger.warning("shadow_live_report_write_failed: %s", exc)

        return result

    def should_report(self, interval_s: float = 3600) -> bool:
        """Check if enough time has passed for the next report."""
        return (time.time() - self._last_report_time) >= interval_s
