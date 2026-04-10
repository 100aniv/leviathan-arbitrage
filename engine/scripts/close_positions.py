"""
PHOENIX — 오픈 오더 취소 + 포지션 전체 클로즈 스크립트
Usage: cd engine && python scripts/close_positions.py [--dry-run]
       cd engine && python scripts/close_positions.py --execute

--dry-run (default): 실제 주문 전송 없이 현황만 출력
--execute: 실제로 오더 취소 + 포지션 시장가 청산
"""
import asyncio
import os
import sys
import argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
_engine_env = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
load_dotenv(_engine_env, override=True)

from decimal import Decimal
from src.infra.exchange import create_native_adapter
from src.core.models import Order, OrderSide, OrderType


EXCHANGES = {
    "binance_futures": {
        "api_key": lambda: os.getenv("BINANCE_API_KEY", ""),
        "api_secret": lambda: os.getenv("BINANCE_API_SECRET", ""),
        "passphrase": "",
    },
    "bitget_futures": {
        "api_key": lambda: os.getenv("BITGET_API_KEY", ""),
        "api_secret": lambda: os.getenv("BITGET_API_SECRET", ""),
        "passphrase": lambda: os.getenv("BITGET_PASSPHRASE", ""),
    },
}


async def close_all(dry_run: bool = True):
    mode_str = "[DRY-RUN]" if dry_run else "[LIVE — REAL ORDERS]"
    print(f"\n{'='*60}")
    print(f"CANCEL ALL ORDERS + CLOSE ALL POSITIONS {mode_str}")
    print(f"{'='*60}")

    for eid, creds in EXCHANGES.items():
        print(f"\n{'─'*40}")
        print(f"Exchange: {eid}")
        print(f"{'─'*40}")
        try:
            passphrase = creds["passphrase"]
            if callable(passphrase):
                passphrase = passphrase()

            adapter = create_native_adapter(
                exchange_id=eid,
                api_key=creds["api_key"](),
                api_secret=creds["api_secret"](),
                passphrase=passphrase,
            )
            await adapter.connect()

            # ── Step 1: Cancel all open orders ────────────────────────
            print(f"\n  [1/2] Cancelling open orders...")
            if not dry_run:
                try:
                    cancelled = await adapter.cancel_all_orders(symbol=None)
                    print(f"    -> Cancelled {cancelled} order(s)")
                except Exception as e:
                    print(f"    -> cancel_all_orders ERROR: {e}")
            else:
                print(f"    -> (dry-run, skipped)")

            # ── Step 2: Close all open positions ──────────────────────
            # Bug 28 workaround: Bitget REST stale data — total=0 right after creation.
            # Retry up to 5 times with 5s wait for Bitget to settle. Collect ALL symbols
            # seen across all retries for paranoid close pass (even size=0 stale positions).
            print(f"\n  [2/2] Fetching open positions...")
            retries = 5 if eid == "bitget_futures" else 1
            positions = []
            all_bitget_symbols_seen: set[str] = set()
            bitget_raw_symbols_paranoid: set[str] = set()  # raw Bitget fmt (BTCUSDT) incl. total=0 stale

            # Bitget: direct raw call to capture ALL positions including stale total=0
            # (adapter.get_positions() filters total=0, so stale hidden positions are invisible there)
            if eid == "bitget_futures" and hasattr(adapter, '_request'):
                try:
                    raw_resp = await adapter._request(
                        "GET", "/api/v2/mix/position/all-position",
                        params={"productType": "USDT-FUTURES"},
                        signed=True,
                    )
                    for item in (raw_resp.get("data") or []):
                        raw_sym = item.get("symbol", "")
                        if raw_sym:
                            bitget_raw_symbols_paranoid.add(raw_sym)
                    if bitget_raw_symbols_paranoid:
                        print(f"    -> Bitget raw positions (incl. stale size=0): {sorted(bitget_raw_symbols_paranoid)}")
                    else:
                        print(f"    -> Bitget raw: no positions in API response")
                except Exception as _exc:
                    print(f"    -> Bitget raw position fetch warning: {_exc}")

            for attempt in range(retries):
                batch = await adapter.get_positions()
                all_bitget_symbols_seen.update(p.symbol for p in batch)
                positions = batch
                open_positions_check = [p for p in positions if p.size != 0]
                if open_positions_check or attempt == retries - 1:
                    break
                import asyncio as _asyncio
                print(f"    -> Bitget: no positions yet, waiting 5s (attempt {attempt+1}/{retries})...")
                await _asyncio.sleep(5)
            open_positions = [p for p in positions if p.size != 0]

            if not open_positions:
                print(f"    -> No open positions (REST visible)")
                # Bitget: still run paranoid close — raw API may have stale total=0 positions
                _has_paranoid = eid == "bitget_futures" and not dry_run and (
                    all_bitget_symbols_seen or bitget_raw_symbols_paranoid
                )
                if not _has_paranoid:
                    await adapter.disconnect()
                    continue

            for pos in open_positions:
                side = OrderSide.SELL if pos.size > 0 else OrderSide.BUY
                side_str = "SELL" if pos.size > 0 else "BUY"
                size = abs(pos.size)
                pnl_str = f"unrealized_pnl={pos.unrealized_pnl:.4f}"
                print(f"    {'WOULD CLOSE' if dry_run else 'CLOSING'}: {pos.symbol} {side_str} {size} (entry={pos.entry_price}, {pnl_str})")

                if not dry_run:
                    hold_side = "long" if pos.size > 0 else "short"
                    # Bitget: use close-positions endpoint (place-order tradeSide=close returns 22002)
                    # Other exchanges: use place-order with reduceOnly
                    if eid == "bitget_futures" and hasattr(adapter, '_request'):
                        sym_normalized = pos.symbol.replace("/", "")
                        body = {
                            "symbol": sym_normalized,
                            "productType": "USDT-FUTURES",
                            "holdSide": hold_side,
                        }
                        try:
                            resp = await adapter._request(
                                "POST", "/api/v2/mix/order/close-positions", data=body, signed=True
                            )
                            success_list = resp.get("data", {}).get("successList", [])
                            if success_list:
                                print(f"      -> CLOSED (flash): orderId={success_list[0].get('orderId')}")
                            else:
                                failure_list = resp.get("data", {}).get("failureList", [])
                                print(f"      -> WARN: {failure_list}")
                        except Exception as e:
                            err_str = str(e)
                            if "22002" in err_str or "No position to close" in err_str:
                                print(f"      -> SKIPPED (ghost/already closed): {pos.symbol}")
                            else:
                                print(f"      -> ERROR closing {pos.symbol}: {e}")
                    else:
                        pos_side = "long" if pos.size > 0 else "short"
                        close_order = Order(
                            exchange_id=eid,
                            symbol=pos.symbol,
                            side=side,
                            order_type=OrderType.MARKET,
                            amount=size,
                            metadata={"reduceOnly": True, "tradeSide": "close", "posSide": pos_side},
                        )
                        try:
                            result = await adapter.place_order(close_order)
                            print(f"      -> CLOSED: order_id={result.trade_id} fill={result.amount}@{result.price}")
                        except Exception as e:
                            err_str = str(e)
                            if "22002" in err_str or "No position to close" in err_str:
                                print(f"      -> SKIPPED (ghost/already closed): {pos.symbol}")
                            else:
                                print(f"      -> ERROR closing {pos.symbol}: {e}")

            # ── Paranoid close (Bitget stale-data guard) ──────────────
            # Force-close both sides for ALL symbols ever seen in this run,
            # even if REST reported size=0 (Bitget hides fresh positions for minutes).
            # 22002 = "No position to close" is expected/silent for already-flat symbols.
            # Combine: adapter-visible symbols (normalized) + raw API symbols (incl. total=0 stale)
            _paranoid_raw: set[str] = bitget_raw_symbols_paranoid | {s.replace("/", "") for s in all_bitget_symbols_seen}
            if eid == "bitget_futures" and not dry_run and _paranoid_raw and hasattr(adapter, '_request'):
                print(f"\n  [PARANOID CLOSE] Bitget stale-data guard — {len(_paranoid_raw)} symbol(s) (incl. stale)...")
                paranoid_closed = 0
                for sym_normalized in sorted(_paranoid_raw):
                    for hold_side in ("long", "short"):
                        try:
                            resp = await adapter._request(
                                "POST", "/api/v2/mix/order/close-positions",
                                data={"symbol": sym_normalized, "productType": "USDT-FUTURES", "holdSide": hold_side},
                                signed=True,
                            )
                            success_list = resp.get("data", {}).get("successList", [])
                            failure_list = resp.get("data", {}).get("failureList", [])
                            if success_list:
                                paranoid_closed += 1
                                print(f"    -> PARANOID CLOSED: {sym_normalized} {hold_side} orderId={success_list[0].get('orderId')}")
                            elif failure_list:
                                err_code = str(failure_list[0].get("errorCode", ""))
                                if err_code != "22002":
                                    print(f"    -> PARANOID WARN: {sym_normalized} {hold_side}: {failure_list[0]}")
                        except Exception as e:
                            if "22002" not in str(e) and "No position to close" not in str(e):
                                print(f"    -> PARANOID ERROR: {sym_normalized} {hold_side}: {e}")
                print(f"  [PARANOID CLOSE] done — {paranoid_closed} additional position(s) closed")

            await adapter.disconnect()

        except Exception as e:
            print(f"  FATAL ERROR for {eid}: {e}")
            import traceback
            traceback.print_exc()

    print(f"\n{'='*60}")
    if dry_run:
        print("DRY-RUN complete. Run with --execute to actually cancel orders + close positions.")
    else:
        print("Done. Check above for any ERRORs.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="Actually cancel orders + close positions")
    args = parser.parse_args()
    asyncio.run(close_all(dry_run=not args.execute))
