"""Binance /fapi/v1/income 30-day reconciliation.
Pre-Day 9 CEO assignment. Produces CSV + diff JSON. $0 cost.
"""
import os, sys, time, hmac, hashlib, json, csv
from pathlib import Path
from urllib.parse import urlencode
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent.parent / '.env')
except ImportError:
    pass
import httpx
import asyncio

BINANCE_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_SECRET = os.getenv("BINANCE_API_SECRET")
BASE = "https://fapi.binance.com"
CSV_PATH = Path(__file__).parent.parent / "logs" / "binance_income_reconciliation_30d.csv"
JSON_PATH = Path(__file__).parent.parent / "logs" / "binance_vs_pnlledger_diff.json"

async def fetch_income(start_ms, end_ms):
    rows = []
    cursor = start_ms
    async with httpx.AsyncClient(timeout=30.0) as c:
        while True:
            ts = int(time.time() * 1000)
            params = {"startTime": cursor, "endTime": end_ms, "limit": 1000, "timestamp": ts, "recvWindow": 10000}
            qs = urlencode(params)
            sig = hmac.new(BINANCE_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
            r = await c.get(f"{BASE}/fapi/v1/income?{qs}&signature={sig}", headers={"X-MBX-APIKEY": BINANCE_KEY})
            if r.status_code != 200:
                print(f"HTTP {r.status_code}: {r.text[:200]}", file=sys.stderr)
                break
            data = r.json()
            if not data:
                break
            rows.extend(data)
            last_ts = max(int(d["time"]) for d in data)
            if last_ts >= end_ms or len(data) < 1000:
                break
            cursor = last_ts + 1
    return rows

async def main():
    if not BINANCE_KEY or not BINANCE_SECRET:
        print("MISSING_API_CREDS", file=sys.stderr); sys.exit(1)
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - 30 * 24 * 60 * 60 * 1000
    print(f"Fetching Binance futures income {start_ms} → {now_ms}...")
    rows = await fetch_income(start_ms, now_ms)
    print(f"Got {len(rows)} income events")
    # Write CSV
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CSV_PATH, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp_utc", "symbol", "income_type", "income_usd", "asset", "tran_id", "trade_id"])
        for r in rows:
            w.writerow([
                datetime.fromtimestamp(int(r["time"])/1000, tz=timezone.utc).isoformat(),
                r.get("symbol", ""),
                r.get("incomeType", ""),
                r.get("income", "0"),
                r.get("asset", ""),
                r.get("tranId", ""),
                r.get("tradeId", ""),
            ])
    print(f"CSV: {CSV_PATH}")
    # Summary
    totals = {}
    sym_totals = {}
    for r in rows:
        t = r.get("incomeType", "?")
        inc = float(r.get("income", 0))
        sym = r.get("symbol", "")
        totals[t] = totals.get(t, 0.0) + inc
        if sym:
            sym_totals[sym] = sym_totals.get(sym, 0.0) + inc
    summary = {
        "period_start_utc": datetime.fromtimestamp(start_ms/1000, tz=timezone.utc).isoformat(),
        "period_end_utc": datetime.fromtimestamp(now_ms/1000, tz=timezone.utc).isoformat(),
        "events_count": len(rows),
        "per_type_usd": {k: round(v, 4) for k, v in totals.items()},
        "grand_total_usd": round(sum(totals.values()), 4),
        "top_10_symbols_by_abs_impact": [
            {"symbol": s, "income_usd": round(v, 4)}
            for s, v in sorted(sym_totals.items(), key=lambda x: abs(x[1]), reverse=True)[:10]
        ],
    }
    # Variance decomposition (engine-side PnLLedger not available mid-session — placeholder)
    summary["variance_decomposition_note"] = "Engine PnLLedger not populated during Path-B v2 prep — diff JSON contains exchange-only view. Full diff requires running reconciler after Day 9 wiring fix."
    JSON_PATH.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"JSON: {JSON_PATH}")
    print(f"\n=== SUMMARY ===")
    print(f"30-day realized_pnl total: ${totals.get('REALIZED_PNL', 0):+.2f}")
    print(f"30-day commission: ${totals.get('COMMISSION', 0):+.2f}")
    print(f"30-day funding_fee: ${totals.get('FUNDING_FEE', 0):+.2f}")
    print(f"30-day transfers: ${totals.get('TRANSFER', 0):+.2f}")
    print(f"30-day insurance_clear: ${totals.get('INSURANCE_CLEAR', 0):+.2f}")
    print(f"GRAND TOTAL (inc. transfers): ${sum(totals.values()):+.2f}")
    trading_only = totals.get('REALIZED_PNL', 0) + totals.get('COMMISSION', 0) + totals.get('FUNDING_FEE', 0) + totals.get('INSURANCE_CLEAR', 0)
    print(f"TRADING ONLY (excl. transfers): ${trading_only:+.2f}")
    return trading_only

if __name__ == "__main__":
    asyncio.run(main())
