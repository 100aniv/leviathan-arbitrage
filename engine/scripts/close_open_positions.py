"""
PHOENIX WS-0-1 — Auto-close all open hedge positions on Binance + Bitget Futures.

Fetches live positions, sends reduceOnly MARKET close orders, and writes a CSV audit
trail (mark_price_pre, fill_price, slippage_bps, realized_usd) for manual reconciliation.

Usage:
    # Dry-run (default — no real orders, just logs what would happen)
    cd engine && python scripts/close_open_positions.py
    cd engine && python scripts/close_open_positions.py --dry-run

    # Live execution (sends real reduceOnly market orders)
    cd engine && python scripts/close_open_positions.py --execute

CSV output: engine/logs/manual_close_YYYYMMDD_HHMMSS.csv
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

# Repo path setup — mirrors engine/src main.py pattern
_ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ENGINE_DIR))

from dotenv import load_dotenv  # noqa: E402

# Load engine/.env first (primary secret source), fall back to repo-root .env
load_dotenv(_ENGINE_DIR / ".env", override=True)
load_dotenv(_ENGINE_DIR.parent / ".env")

from src.core.models import Order, OrderSide, OrderType, Position  # noqa: E402
from src.infra.exchange import create_native_adapter  # noqa: E402

EXCHANGES: dict[str, dict] = {
    "binance_futures": {
        "api_key_env": "BINANCE_API_KEY",
        "api_secret_env": "BINANCE_API_SECRET",
        "passphrase_env": None,
    },
    "bitget_futures": {
        "api_key_env": "BITGET_API_KEY",
        "api_secret_env": "BITGET_API_SECRET",
        "passphrase_env": "BITGET_PASSPHRASE",
    },
}

CSV_COLUMNS = [
    "timestamp",
    "exchange",
    "symbol",
    "side",
    "qty",
    "mark_price_pre",
    "fill_price",
    "slippage_bps",
    "realized_usd",
    "status",
]


def _fmt(v) -> str:
    """Decimal / None / str -> CSV-safe string."""
    if v is None:
        return ""
    if isinstance(v, Decimal):
        return f"{v:f}"
    return str(v)


def _calc_slippage_bps(mark_pre: Decimal | None, fill: Decimal | None) -> Decimal | None:
    if mark_pre is None or fill is None or mark_pre == 0:
        return None
    return (abs(fill - mark_pre) / mark_pre) * Decimal("10000")


def _calc_realized_usd(
    entry: Decimal | None, fill: Decimal | None, qty: Decimal, pos_size: Decimal
) -> Decimal | None:
    """Best-effort realized PnL in USD — (fill − entry) × qty × sign(pos_size)."""
    if entry is None or fill is None or entry == 0:
        return None
    sign = Decimal("1") if pos_size > 0 else Decimal("-1")
    return (fill - entry) * qty * sign


async def _close_one(
    adapter,
    exchange_id: str,
    pos: Position,
    dry_run: bool,
) -> dict:
    """Close a single position. Returns a row dict for the CSV."""
    side = OrderSide.SELL if pos.size > 0 else OrderSide.BUY
    qty = abs(pos.size)
    pos_side = "long" if pos.size > 0 else "short"

    # Snapshot mark price BEFORE order (best effort — Position already carries it)
    mark_pre = pos.mark_price if pos.mark_price and pos.mark_price > 0 else None

    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "exchange": exchange_id,
        "symbol": pos.symbol,
        "side": side.value,
        "qty": _fmt(qty),
        "mark_price_pre": _fmt(mark_pre),
        "fill_price": "",
        "slippage_bps": "",
        "realized_usd": "",
        "status": "dry_run" if dry_run else "pending",
    }

    action = "WOULD CLOSE" if dry_run else "CLOSING"
    print(
        f"    {action}: {pos.symbol} {side.value.upper()} qty={qty} "
        f"(entry={pos.entry_price}, mark={pos.mark_price}, unrealized={pos.unrealized_pnl})"
    )

    if dry_run:
        return row

    close_order = Order(
        exchange_id=exchange_id,
        symbol=pos.symbol,
        side=side,
        order_type=OrderType.MARKET,
        amount=qty,
        metadata={
            "reduceOnly": True,
            "tradeSide": "close",
            "posSide": pos_side,
        },
    )
    try:
        trade = await adapter.place_order(close_order)
        fill_price = trade.price
        slip_bps = _calc_slippage_bps(mark_pre, fill_price)
        realized = _calc_realized_usd(pos.entry_price, fill_price, qty, pos.size)
        row["fill_price"] = _fmt(fill_price)
        row["slippage_bps"] = _fmt(slip_bps)
        row["realized_usd"] = _fmt(realized)
        row["status"] = "closed"
        print(
            f"      -> CLOSED: trade_id={trade.trade_id} fill={trade.amount}@{fill_price} "
            f"slippage_bps={slip_bps} realized_usd={realized}"
        )
    except Exception as exc:  # noqa: BLE001
        row["status"] = "failed"
        row["fill_price"] = f"ERROR: {exc}"[:200]
        print(f"      -> ERROR closing {pos.symbol}: {exc}")

    return row


async def _process_exchange(
    exchange_id: str,
    creds: dict,
    dry_run: bool,
) -> list[dict]:
    """Fetch positions on one exchange and close each. Returns list of CSV rows."""
    rows: list[dict] = []

    api_key = os.getenv(creds["api_key_env"], "")
    api_secret = os.getenv(creds["api_secret_env"], "")
    passphrase = os.getenv(creds["passphrase_env"], "") if creds["passphrase_env"] else ""

    if not api_key or not api_secret:
        print(
            f"  SKIP {exchange_id}: missing {creds['api_key_env']} / {creds['api_secret_env']} in env"
        )
        return rows

    print(f"\n{'-' * 60}")
    print(f"Exchange: {exchange_id}")
    print(f"{'-' * 60}")

    adapter = None
    try:
        adapter = create_native_adapter(
            exchange_id=exchange_id,
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
        )
        await adapter.connect()

        positions = await adapter.get_positions()
        open_positions = [p for p in positions if p.size != 0]

        if not open_positions:
            print("  No open positions.")
            return rows

        print(f"  Found {len(open_positions)} open position(s).")
        for pos in open_positions:
            row = await _close_one(adapter, exchange_id, pos, dry_run)
            rows.append(row)
            # Bitget 2 req/s guard on live path
            if not dry_run and exchange_id == "bitget_futures":
                await asyncio.sleep(0.5)

    except Exception as exc:  # noqa: BLE001
        print(f"  FATAL {exchange_id}: {exc}")
        import traceback
        traceback.print_exc()
    finally:
        if adapter is not None:
            try:
                await adapter.disconnect()
            except Exception:  # noqa: BLE001
                pass

    return rows


def _write_csv(rows: list[dict], csv_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in CSV_COLUMNS})


def _print_summary(rows: list[dict], dry_run: bool) -> None:
    total = len(rows)
    closed = [r for r in rows if r["status"] == "closed"]
    failed = [r for r in rows if r["status"] == "failed"]
    dryruns = [r for r in rows if r["status"] == "dry_run"]

    total_slip_usd = Decimal("0")
    total_realized = Decimal("0")
    for r in closed:
        try:
            qty = Decimal(r["qty"] or "0")
            mark_pre = Decimal(r["mark_price_pre"] or "0")
            fill = Decimal(r["fill_price"] or "0")
            if mark_pre > 0 and fill > 0:
                total_slip_usd += abs(fill - mark_pre) * qty
            if r["realized_usd"]:
                total_realized += Decimal(r["realized_usd"])
        except Exception:  # noqa: BLE001
            continue

    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")
    print(f"  positions_processed : {total}")
    if dry_run:
        print(f"  dry_run_entries     : {len(dryruns)}")
    else:
        print(f"  closed              : {len(closed)}")
        print(f"  failed              : {len(failed)}")
        print(f"  total_slippage_usd  : {total_slip_usd:f}")
        print(f"  total_realized_usd  : {total_realized:f}")
        if failed:
            print("  FAILURES (manual intervention required):")
            for r in failed:
                print(f"    - {r['exchange']} {r['symbol']} {r['side']} qty={r['qty']} :: {r['fill_price']}")
    print(f"{'=' * 60}\n")


async def main(dry_run: bool) -> None:
    mode = "[DRY-RUN]" if dry_run else "[LIVE — REAL ORDERS]"
    print(f"\n{'=' * 60}")
    print(f"WS-0-1  close_open_positions  {mode}")
    print(f"{'=' * 60}")

    all_rows: list[dict] = []
    for exchange_id, creds in EXCHANGES.items():
        rows = await _process_exchange(exchange_id, creds, dry_run)
        all_rows.extend(rows)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    csv_path = _ENGINE_DIR / "logs" / f"manual_close_{ts}.csv"
    _write_csv(all_rows, csv_path)
    print(f"\nCSV written: {csv_path}")

    _print_summary(all_rows, dry_run)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Auto-close all open Binance + Bitget futures positions (WS-0-1)."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="(default) Log what would happen, no real orders.",
    )
    group.add_argument(
        "--execute",
        action="store_true",
        default=False,
        help="Actually send reduceOnly market close orders.",
    )
    args = parser.parse_args()

    is_dry = not args.execute
    asyncio.run(main(dry_run=is_dry))
