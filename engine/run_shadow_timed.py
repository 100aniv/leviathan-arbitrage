"""Timed Shadow Runtime — runs for N minutes then gracefully stops."""
import asyncio
import os
import sys
import time
from decimal import Decimal

sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv("../.env")

# Force shadow mode
os.environ["DATA_MODE"] = "shadow"
os.environ["EXECUTION_MODE"] = "paper"

from src.main import Engine


def _print_spread_report(shadow) -> None:
    """Print cross-exchange spread analysis from PriceHub."""
    hub = shadow._signal_generator._hub
    books = shadow._books
    krw_rate = shadow._krw_rate

    print(f"  KRW/USDT rate: {krw_rate:.1f}")

    for symbol in sorted(books.keys()):
        exchanges = sorted(books[symbol].keys())
        if len(exchanges) < 2:
            continue

        best_bid = hub.best_bid(symbol)
        best_ask = hub.best_ask(symbol)
        if best_bid is None or best_ask is None:
            continue

        # Raw spread (before friction)
        if best_bid.exchange != best_ask.exchange and best_bid.price > best_ask.price:
            spread_pct = float((best_bid.price - best_ask.price) / best_ask.price * 100)
            status = f"SPREAD +{spread_pct:.3f}%"
        else:
            spread_pct = float((best_bid.price - best_ask.price) / best_ask.price * 100) if best_ask.price > 0 else 0
            status = f"no-arb {spread_pct:+.3f}%"

        prices = []
        for ex in exchanges:
            book = books[symbol].get(ex)
            if book:
                b = book.best_bid()
                a = book.best_ask()
                if b and a:
                    prices.append(f"{ex}={float(b):.4f}/{float(a):.4f}")

        print(f"  {symbol}: {status} | best_bid={best_bid.exchange}@{float(best_bid.price):.4f} "
              f"best_ask={best_ask.exchange}@{float(best_ask.price):.4f} | {' '.join(prices)}")


async def _timer(engine: Engine, duration_minutes: float):
    """Wait for duration, print stats, then trigger graceful shutdown."""
    start = time.time()
    deadline = start + (duration_minutes * 60)

    print(f"\n{'='*60}")
    print(f"Shadow Runtime — {duration_minutes} min")
    print(f"Deadline: {time.strftime('%H:%M:%S', time.localtime(deadline))}")
    print(f"{'='*60}\n")

    try:
        while time.time() < deadline:
            elapsed = time.time() - start

            # Print stats every 30 seconds
            if int(elapsed) % 30 == 0 and int(elapsed) > 0:
                shadow = getattr(engine, '_shadow_mode', None)
                if shadow and hasattr(shadow, '_stats'):
                    s = shadow._stats
                    cm = shadow._collector_manager
                    connected = cm.connected_count if cm else 0
                    print(f"\n[{elapsed:.0f}s] signals={s.signals_detected} trades={s.trades_executed} "
                          f"pnl={s.total_pnl:.4f} drawdown={s.max_drawdown:.4f} "
                          f"collectors={connected}")
                    try:
                        _print_spread_report(shadow)
                    except Exception as exc:
                        print(f"  (spread report error: {exc})")

            await asyncio.sleep(1.0)
    except asyncio.CancelledError:
        pass

    # Print final stats
    shadow = getattr(engine, '_shadow_mode', None)
    if shadow and hasattr(shadow, '_stats'):
        s = shadow._stats
        elapsed = time.time() - start
        print(f"\n{'='*60}")
        print(f"Shadow Runtime Complete — {elapsed:.1f}s")
        print(f"  Signals: {s.signals_detected}")
        print(f"  Trades:  {s.trades_executed} (won: {s.trades_won}, lost: {s.trades_lost})")
        print(f"  PnL:     {s.total_pnl:.6f}")
        print(f"  Peak:    {s.peak_pnl:.6f}")
        print(f"  Drawdown:{s.max_drawdown:.6f}")
        try:
            _print_spread_report(shadow)
        except Exception:
            pass
        print(f"{'='*60}")

    # Trigger graceful shutdown
    await engine.stop()


async def run(duration_minutes: float):
    engine = Engine()
    # engine.run() blocks until _shutdown_event; _timer stops it after deadline
    await asyncio.gather(
        engine.run(),
        _timer(engine, duration_minutes),
    )


if __name__ == "__main__":
    minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 10
    asyncio.run(run(minutes))
