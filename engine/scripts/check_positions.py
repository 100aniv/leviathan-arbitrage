"""
PHOENIX — 오픈 포지션 확인 스크립트 (READ-ONLY)
Usage: cd engine && python scripts/check_positions.py
"""
import asyncio
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine", ".env"), override=True)
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from src.infra.exchange import create_native_adapter


async def check_positions():
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

    for eid, creds in exchanges.items():
        print(f"\n{'='*40}")
        print(f"Exchange: {eid}")
        print(f"{'='*40}")
        try:
            adapter = create_native_adapter(
                exchange_id=eid,
                api_key=creds["api_key"],
                api_secret=creds["api_secret"],
                passphrase=creds["passphrase"],
            )
            await adapter.connect()

            # Check positions
            positions = await adapter.get_positions()
            if positions:
                print(f"  OPEN POSITIONS ({len(positions)}):")
                for p in positions:
                    print(f"    {p}")
            else:
                print("  No open positions")

            # Check balances
            balances = await adapter.get_balances()
            usdt = next((b for b in balances if b.asset == "USDT"), None)
            if usdt:
                print(f"  USDT Balance: {usdt.free:.4f} free / {usdt.total:.4f} total")

            await adapter.disconnect()
        except Exception as e:
            print(f"  ERROR: {e}")


if __name__ == "__main__":
    asyncio.run(check_positions())
