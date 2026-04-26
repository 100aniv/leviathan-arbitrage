"""Pre-Day 9 — Binance /fapi/v1/income 30-day CSV fetcher.

Signed HMAC-SHA256 GET /fapi/v1/income over the last 30 days, paginated in
7-day windows (Binance returns max 1000 rows per call). Writes rows to
``engine/logs/binance_income_reconciliation_30d.csv`` with a summary at end.

Run: ``python engine/scripts/binance_income_reconciliation.py``
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import os
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = REPO_ROOT / ".env"
OUT_CSV = REPO_ROOT / "engine" / "logs" / "binance_income_reconciliation_30d.csv"

BINANCE_FAPI = "https://fapi.binance.com"
INCOME_PATH = "/fapi/v1/income"
RECV_WINDOW_MS = 5000
# 30 days back, paginated in 7-day windows to stay well below 1000 rows/window.
WINDOW_DAYS = 7
TOTAL_DAYS = 30
PER_CALL_LIMIT = 1000

CSV_HEADER = [
    "timestamp_ms",
    "timestamp_iso",
    "symbol",
    "income_type",
    "income_usd",
    "asset",
    "trade_id",
    "tran_id",
]


def _load_env(path: Path) -> dict[str, str]:
    """Parse a .env file into a dict. Minimal parser, no quoting edge cases."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def _sign(secret: str, query: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        query.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _signed_get(
    api_key: str,
    api_secret: str,
    params: dict[str, int | str],
) -> list[dict]:
    params_with_meta = {
        **params,
        "recvWindow": RECV_WINDOW_MS,
        "timestamp": int(time.time() * 1000),
    }
    query = urllib.parse.urlencode(params_with_meta)
    signature = _sign(api_secret, query)
    url = f"{BINANCE_FAPI}{INCOME_PATH}?{query}&signature={signature}"
    req = urllib.request.Request(
        url,
        headers={"X-MBX-APIKEY": api_key},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
        import json as _json
        raw = resp.read().decode("utf-8")
        data = _json.loads(raw)
    if not isinstance(data, list):
        raise RuntimeError(f"binance income unexpected response: {data!r}")
    return data


def _iso(ms: int) -> str:
    import datetime as _dt
    return _dt.datetime.fromtimestamp(
        ms / 1000.0, tz=_dt.timezone.utc,
    ).isoformat()


def main() -> int:
    env = _load_env(ENV_PATH)
    # Allow OS env override for CI safety.
    api_key = os.environ.get("BINANCE_API_KEY") or env.get("BINANCE_API_KEY", "")
    api_secret = (
        os.environ.get("BINANCE_API_SECRET") or env.get("BINANCE_API_SECRET", "")
    )
    if not api_key or not api_secret:
        print("ERROR: BINANCE_API_KEY/SECRET missing in .env", file=sys.stderr)
        return 1

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)

    now_ms = int(time.time() * 1000)
    start_ms = now_ms - TOTAL_DAYS * 24 * 60 * 60 * 1000
    window_ms = WINDOW_DAYS * 24 * 60 * 60 * 1000

    rows: list[list] = []
    totals_by_type: dict[str, float] = defaultdict(float)
    grand_total = 0.0
    windows_polled = 0

    cursor = start_ms
    while cursor < now_ms:
        w_end = min(cursor + window_ms, now_ms)
        try:
            data = _signed_get(
                api_key, api_secret,
                {
                    "startTime": cursor,
                    "endTime": w_end,
                    "limit": PER_CALL_LIMIT,
                },
            )
        except Exception as exc:  # noqa: BLE001
            print(
                f"WARN window {cursor}..{w_end} failed: {exc}", file=sys.stderr,
            )
            cursor = w_end
            continue
        windows_polled += 1
        print(
            f"window {_iso(cursor)}..{_iso(w_end)} events={len(data)}",
            flush=True,
        )
        for ev in data:
            try:
                ts_ms = int(ev.get("time") or 0)
                amount = float(ev.get("income") or 0)
            except (TypeError, ValueError):
                continue
            income_type = str(ev.get("incomeType", "UNKNOWN")).upper()
            symbol = str(ev.get("symbol", "") or "")
            asset = str(ev.get("asset", "") or "")
            trade_id = str(ev.get("tradeId") or "")
            tran_id = str(ev.get("tranId") or "")
            rows.append([
                ts_ms, _iso(ts_ms), symbol, income_type,
                f"{amount:.10f}", asset, trade_id, tran_id,
            ])
            totals_by_type[income_type] += amount
            grand_total += amount
        # Binance may cap the window; if we hit the page limit, extend cursor
        # by one ms past the latest event to avoid infinite loops.
        if len(data) >= PER_CALL_LIMIT:
            last_ts = max((int(ev.get("time") or cursor) for ev in data), default=cursor)
            cursor = max(last_ts + 1, cursor + 1)
        else:
            cursor = w_end

    rows.sort(key=lambda r: r[0])
    with OUT_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(CSV_HEADER)
        for row in rows:
            writer.writerow(row)
        # Summary as comment-style footer rows (still valid CSV via first column).
        writer.writerow([])
        writer.writerow(["# SUMMARY"])
        writer.writerow(["# windows_polled", windows_polled])
        writer.writerow(["# total_events", len(rows)])
        for itype, total in sorted(totals_by_type.items()):
            writer.writerow([f"# total_{itype.lower()}_usd", f"{total:.10f}"])
        writer.writerow(["# grand_total_usd", f"{grand_total:.10f}"])

    print(
        f"wrote {OUT_CSV} events={len(rows)} grand_total_usd={grand_total:.6f}",
    )
    for itype, total in sorted(totals_by_type.items()):
        print(f"  {itype}: {total:.6f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
