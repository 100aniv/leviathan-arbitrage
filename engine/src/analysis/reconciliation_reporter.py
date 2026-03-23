"""3-Way Reconciliation Reporter.

US-335: Daily comparison of Engine PnL vs Exchange Balances vs DB Records.
Detects drift between the three sources to catch accounting errors early.
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
class ReconciliationResult:
    """Single reconciliation check result."""
    timestamp: float
    engine_pnl: float
    exchange_balance_delta: float
    db_pnl: float
    engine_vs_exchange_pct: float
    engine_vs_db_pct: float
    exchange_vs_db_pct: float
    max_drift_pct: float
    status: str  # "OK" | "WARNING" | "CRITICAL"


class ReconciliationReporter:
    """3-Way PnL reconciliation: Engine vs Exchange vs DB.

    Compares three independent PnL sources to detect accounting drift.
    Threshold: <1% drift = OK, 1-5% = WARNING, >5% = CRITICAL.
    """

    THRESHOLD_WARNING_PCT = 1.0
    THRESHOLD_CRITICAL_PCT = 5.0

    def __init__(
        self,
        output_path: str | Path = ".omc/state/reconciliation-latest.json",
    ) -> None:
        self._output_path = Path(output_path)
        self._results: list[ReconciliationResult] = []

    def reconcile(
        self,
        engine_pnl: float,
        exchange_balance_delta: float,
        db_pnl: float,
    ) -> ReconciliationResult:
        """Perform 3-way reconciliation.

        Args:
            engine_pnl: PnL calculated by the trading engine
            exchange_balance_delta: Actual balance change on exchanges
            db_pnl: PnL recorded in TimescaleDB
        """
        def _pct_diff(a: float, b: float) -> float:
            denom = max(abs(a), abs(b), 0.001)
            return abs(a - b) / denom * 100

        e_vs_ex = _pct_diff(engine_pnl, exchange_balance_delta)
        e_vs_db = _pct_diff(engine_pnl, db_pnl)
        ex_vs_db = _pct_diff(exchange_balance_delta, db_pnl)
        max_drift = max(e_vs_ex, e_vs_db, ex_vs_db)

        if max_drift > self.THRESHOLD_CRITICAL_PCT:
            status = "CRITICAL"
        elif max_drift > self.THRESHOLD_WARNING_PCT:
            status = "WARNING"
        else:
            status = "OK"

        result = ReconciliationResult(
            timestamp=time.time(),
            engine_pnl=round(engine_pnl, 4),
            exchange_balance_delta=round(exchange_balance_delta, 4),
            db_pnl=round(db_pnl, 4),
            engine_vs_exchange_pct=round(e_vs_ex, 2),
            engine_vs_db_pct=round(e_vs_db, 2),
            exchange_vs_db_pct=round(ex_vs_db, 2),
            max_drift_pct=round(max_drift, 2),
            status=status,
        )

        self._results.append(result)
        self._write_report(result)

        if status != "OK":
            logger.warning(
                "reconciliation_%s drift=%.2f%% engine=%.4f exchange=%.4f db=%.4f",
                status.lower(), max_drift, engine_pnl, exchange_balance_delta, db_pnl,
            )
        else:
            logger.info("reconciliation_ok drift=%.2f%%", max_drift)

        return result

    def _write_report(self, result: ReconciliationResult) -> None:
        try:
            self._output_path.parent.mkdir(parents=True, exist_ok=True)
            report = {
                "timestamp": result.timestamp,
                "engine_pnl": result.engine_pnl,
                "exchange_balance_delta": result.exchange_balance_delta,
                "db_pnl": result.db_pnl,
                "drift": {
                    "engine_vs_exchange_pct": result.engine_vs_exchange_pct,
                    "engine_vs_db_pct": result.engine_vs_db_pct,
                    "exchange_vs_db_pct": result.exchange_vs_db_pct,
                    "max_pct": result.max_drift_pct,
                },
                "status": result.status,
                "check_count": len(self._results),
            }
            self._output_path.write_text(json.dumps(report, indent=2))
        except Exception as exc:
            logger.warning("reconciliation_write_failed: %s", exc)

    def get_history(self) -> list[dict]:
        return [
            {"timestamp": r.timestamp, "max_drift_pct": r.max_drift_pct, "status": r.status}
            for r in self._results
        ]
