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
            print(f"\n  [2/2] Fetching open positions...")
            positions = await adapter.get_positions()
            open_positions = [p for p in positions if p.size != 0]

            if not open_positions:
                print(f"    -> No open positions")
                await adapter.disconnect()
                continue

            for pos in open_positions:
                side = OrderSide.SELL if pos.size > 0 else OrderSide.BUY
                side_str = "SELL" if pos.size > 0 else "BUY"
                size = abs(pos.size)
                pnl_str = f"unrealized_pnl={pos.unrealized_pnl:.4f}"
                print(f"    {'WOULD CLOSE' if dry_run else 'CLOSING'}: {pos.symbol} {side_str} {size} (entry={pos.entry_price}, {pnl_str})")

                if not dry_run:
                    close_order = Order(
                        exchange_id=eid,
                        symbol=pos.symbol,
                        side=side,
                        order_type=OrderType.MARKET,
                        amount=size,
                        metadata={"reduceOnly": True, "tradeSide": "close"},
                    )
                    try:
                        result = await adapter.place_order(close_order)
                        print(f"      -> CLOSED: order_id={result.trade_id} fill={result.amount}@{result.price}")
                    except Exception as e:
                        print(f"      -> ERROR closing {pos.symbol}: {e}")

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
