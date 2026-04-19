"""WS-D5: Daily TCA CSV writer.

Appends one row per executed trade to ``engine/logs/tca/YYYYMMDD.csv``.
Columns (in order):
    timestamp, strategy, symbol, exchange_pair, gross_bps, commission_bps,
    slippage_bps, funding_bps, realized_pnl_usd, exchange_pnl_usd,
    net_pnl_usd, divergence_pct, pnl_source

Creation of the directory and header is automatic on first write for a new
UTC day. Failures never raise — TCA is observability-only.
"""
from __future__ import annotations

import csv
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CSV_COLUMNS: tuple[str, ...] = (
    "timestamp",
    "strategy",
    "symbol",
    "exchange_pair",
    "gross_bps",
    "commission_bps",
    "slippage_bps",
    "funding_bps",
    "realized_pnl_usd",
    "exchange_pnl_usd",
    "net_pnl_usd",
    "divergence_pct",
    "pnl_source",
)


class TCACsvWriter:
    """Daily-rotating TCA CSV writer.

    Thread-safe via an internal lock so the live event loop and background
    tasks can append concurrently. File rotation by UTC date is automatic.
    """

    def __init__(self, base_dir: str | os.PathLike | None = None) -> None:
        # Default path: {repo}/engine/logs/tca. Allow tests to override.
        if base_dir is None:
            base_dir = Path(__file__).resolve().parents[2] / "logs" / "tca"
        self._base_dir = Path(base_dir)
        self._lock = threading.Lock()

    def _path_for(self, now: datetime) -> Path:
        return self._base_dir / f"{now.strftime('%Y%m%d')}.csv"

    def write_row(self, row: dict[str, Any], *, now: datetime | None = None) -> bool:
        """Append a single trade row. Returns True on success, False on failure.

        Missing keys are filled with empty strings so the CSV stays rectangular.
        """
        ts = now or datetime.now(timezone.utc)
        path = self._path_for(ts)
        try:
            with self._lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                write_header = not path.exists()
                with path.open("a", newline="") as fh:
                    writer = csv.DictWriter(fh, fieldnames=list(_CSV_COLUMNS))
                    if write_header:
                        writer.writeheader()
                    # Ensure timestamp defaults to ISO-8601 UTC
                    if "timestamp" not in row or row.get("timestamp") in (None, ""):
                        row = {**row, "timestamp": ts.isoformat()}
                    # Filter out unexpected keys to protect the schema
                    safe = {k: row.get(k, "") for k in _CSV_COLUMNS}
                    writer.writerow(safe)
            return True
        except Exception as exc:  # pragma: no cover — observability only
            logger.warning("tca_csv_writer.write_failed path=%s err=%s", path, exc)
            return False
