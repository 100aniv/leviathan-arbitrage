"""
PHOENIX — 오픈 포지션 즉시 클로즈 스크립트 (실제 주문 전송)
Usage: cd engine && python scripts/close_positions.py [--dry-run]

--dry-run: 실제 주문 전송 없이 포지션만 출력
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


async def close_positions(dry_run: bool = True):
    exchanges = {
        "binance_futures": {
            "api_key": os.getenv("BINANCE_API_KEY", ""),
            "api_secret": os.getenv("BINANCE_API_SECRET", ""),
            "passphrase": "",
        },
        "bitget_futures": {
            "api_key": os.getenv("BITGET_API_KEY", ""),
            "api_secret": os.getenv("BITGET_API_SECRET", ""),
            "passphrase": os.getenv("BITGET_PASSPHRASE", ""),
        },
    }

    mode_str = "[DRY-RUN]" if dry_run else "[LIVE — REAL ORDERS]"
    print(f"\n{'='*50}")
    print(f"CLOSE ALL POSITIONS {mode_str}")
    print(f"{'='*50}")

    for eid, creds in exchanges.items():
        print(f"\nExchange: {eid}")
        try:
            adapter = create_native_adapter(
                exchange_id=eid,
                api_key=creds["api_key"],
                api_secret=creds["api_secret"],
                passphrase=creds["passphrase"],
            )
            await adapter.connect()

            positions = await adapter.get_positions()
            if not positions:
                print(f"  No open positions")
                await adapter.disconnect()
                continue

            for pos in positions:
                if pos.size == 0:
                    continue
                side = "SELL" if pos.size > 0 else "BUY"
                size = abs(pos.size)
                print(f"  {'WOULD CLOSE' if dry_run else 'CLOSING'}: {pos.symbol} {side} {size} (entry={pos.entry_price}, unrealized_pnl={pos.unrealized_pnl})")

                if not dry_run:
                    close_order = Order(
                        exchange_id=eid,
                        symbol=pos.symbol,
                        side=OrderSide.SELL if pos.size > 0 else OrderSide.BUY,
                        order_type=OrderType.MARKET,
                        amount=size,
                        metadata={"reduceOnly": True},
                    )
                    try:
                        result = await adapter.place_order(close_order)
                        print(f"    -> CLOSED: order_id={result.trade_id} fill={result.amount}@{result.price}")
                    except Exception as e:
                        print(f"    -> ERROR: {e}")

            await adapter.disconnect()
        except Exception as e:
            print(f"  ERROR: {e}")

    if dry_run:
        print(f"\n{'='*50}")
        print("DRY-RUN complete. Run with --execute to actually close positions.")
        print(f"{'='*50}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="Actually close positions (default: dry-run)")
    args = parser.parse_args()
    asyncio.run(close_positions(dry_run=not args.execute))
