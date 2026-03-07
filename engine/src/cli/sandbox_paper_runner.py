"""Sandbox paper trading runner — real market data + paper execution.

Uses real exchange data from testnets but executes trades via PaperExecutor.
This validates signal generation against real market conditions.

Usage:
    python -m src.cli.sandbox_paper_runner --exchange binance --duration 300
    python -m src.cli.sandbox_paper_runner --exchange binance --exchange upbit --duration 600
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import time
from decimal import Decimal
from pathlib import Path

from src.core.metrics_collector import MetricsCollector

logger = logging.getLogger(__name__)


class SandboxPaperRunner:
    """Run paper trading with real market data from exchange testnets."""

    def __init__(
        self,
        exchanges: list[str],
        symbol: str = "BTC/USDT",
        duration: int = 300,
        initial_capital: float = 70.0,
        sandbox: bool = True,
        verbose: bool = False,
    ) -> None:
        self._exchanges = exchanges
        self._symbol = symbol
        self._duration = duration
        self._initial_capital = initial_capital
        self._sandbox = sandbox
        self._verbose = verbose
        self._running = False
        self._metrics = MetricsCollector(initial_capital=initial_capital)

    async def run(self) -> dict:
        """Run sandbox paper trading."""
        from src.execution.paper_adapter import PaperExchangeAdapter

        self._running = True
        adapters = {}
        use_paper_fallback = False

        # Try to create real exchange adapters
        for exchange_id in self._exchanges:
            prefix = exchange_id.upper()
            api_key = os.environ.get(f"{prefix}_API_KEY", "")

            if api_key:
                try:
                    from src.infra.exchange.ccxt_adapter import CCXTAdapter

                    adapter = CCXTAdapter(
                        exchange_id=exchange_id,
                        api_key=api_key,
                        api_secret=os.environ.get(f"{prefix}_SECRET", ""),
                        passphrase=os.environ.get(f"{prefix}_PASSWORD", ""),
                        sandbox=self._sandbox,
                    )
                    await adapter.connect()
                    adapters[exchange_id] = adapter
                    print(f"  Connected to {exchange_id} {'testnet' if self._sandbox else 'mainnet'}")
                except Exception as e:
                    logger.warning("Failed to connect to %s: %s", exchange_id, e)
                    use_paper_fallback = True
            else:
                use_paper_fallback = True

        # Fallback to paper adapters
        if use_paper_fallback or len(adapters) < 2:
            print("\n  Using paper adapter fallback (no API keys)")
            adapters = {}
            for i, exchange_id in enumerate(self._exchanges):
                adapters[f"paper_{exchange_id}"] = PaperExchangeAdapter(
                    exchange_id=f"paper_{exchange_id}",
                    initial_capital=Decimal(str(self._initial_capital)),
                    spread_injection_rate=0.4,
                    spread_injection_bps=50 + i * 15,
                    tick_interval=0.05,
                )
                await adapters[f"paper_{exchange_id}"].connect()

        # Track orderbooks
        latest_books: dict[str, dict] = {}
        exchange_ids = list(adapters.keys())

        print(f"\nSandbox paper trading started")
        print(f"  Exchanges: {', '.join(exchange_ids)}")
        print(f"  Symbol: {self._symbol}")
        print(f"  Duration: {self._duration}s")
        print(f"  Capital: ${self._initial_capital}")

        start = time.time()
        trade_count = 0

        try:
            while self._running and (time.time() - start) < self._duration:
                # Fetch orderbooks
                for eid, adapter in adapters.items():
                    try:
                        if hasattr(adapter, "get_orderbook_snapshot"):
                            ob = await adapter.get_orderbook_snapshot(self._symbol)
                            latest_books[eid] = ob
                    except Exception as e:
                        logger.debug("Orderbook fetch error for %s: %s", eid, e)

                # Check for arb opportunities between all exchange pairs
                if len(latest_books) >= 2:
                    for i, eid_a in enumerate(exchange_ids):
                        for eid_b in exchange_ids[i + 1:]:
                            if eid_a not in latest_books or eid_b not in latest_books:
                                continue

                            ob_a = latest_books[eid_a]
                            ob_b = latest_books[eid_b]

                            if not (ob_a.bids and ob_a.asks and ob_b.bids and ob_b.asks):
                                continue

                            a_bid = float(ob_a.bids[0].price)
                            a_ask = float(ob_a.asks[0].price)
                            b_bid = float(ob_b.bids[0].price)
                            b_ask = float(ob_b.asks[0].price)

                            # A buy / B sell
                            if b_bid > a_ask:
                                spread_bps = (b_bid - a_ask) / a_ask * 10000
                                if spread_bps > 10:
                                    size = 0.001
                                    pnl = size * (b_bid - a_ask) - size * (a_ask + b_bid) * 0.001
                                    self._metrics.record_trade("cross_exchange_arb", pnl)
                                    trade_count += 1
                                    if self._verbose:
                                        print(
                                            f"  [TRADE] buy@{eid_a}={a_ask:.2f} "
                                            f"sell@{eid_b}={b_bid:.2f} "
                                            f"spread={spread_bps:.1f}bps "
                                            f"pnl=${pnl:.6f}"
                                        )

                            # B buy / A sell
                            if a_bid > b_ask:
                                spread_bps = (a_bid - b_ask) / b_ask * 10000
                                if spread_bps > 10:
                                    size = 0.001
                                    pnl = size * (a_bid - b_ask) - size * (b_ask + a_bid) * 0.001
                                    self._metrics.record_trade("cross_exchange_arb", pnl)
                                    trade_count += 1
                                    if self._verbose:
                                        print(
                                            f"  [TRADE] buy@{eid_b}={b_ask:.2f} "
                                            f"sell@{eid_a}={a_bid:.2f} "
                                            f"spread={spread_bps:.1f}bps "
                                            f"pnl=${pnl:.6f}"
                                        )

                elapsed = time.time() - start
                if int(elapsed) > 0 and int(elapsed) % max(self._duration // 10, 1) == 0:
                    report = self._metrics.get_report()
                    if report.total_trades > 0:
                        print(
                            f"  [{int(elapsed)}s/{self._duration}s] "
                            f"trades={report.total_trades} "
                            f"PnL=${report.total_pnl:.4f} "
                            f"win={report.win_rate * 100:.0f}%"
                        )

                await asyncio.sleep(0.1)

        finally:
            for adapter in adapters.values():
                try:
                    await adapter.disconnect()
                except Exception:
                    pass

        report = self._metrics.get_report()
        print(report.summary())

        return report.to_dict()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="LEVIATHAN Sandbox Paper Trading Runner",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--exchange", action="append", default=None,
        help="Exchange(s) to use (can specify multiple)",
    )
    parser.add_argument("--symbol", default="BTC/USDT", help="Trading symbol")
    parser.add_argument("--duration", type=int, default=300, help="Duration (seconds)")
    parser.add_argument("--capital", type=float, default=70.0, help="Initial capital")
    parser.add_argument("--live", action="store_true", help="Use mainnet (not testnet)")
    parser.add_argument("--verbose", action="store_true", help="Print each trade")
    parser.add_argument("--save-report", type=str, default=None, help="Save report JSON")

    args = parser.parse_args()

    exchanges = args.exchange or ["binance", "upbit"]

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    runner = SandboxPaperRunner(
        exchanges=exchanges,
        symbol=args.symbol,
        duration=args.duration,
        initial_capital=args.capital,
        sandbox=not args.live,
        verbose=args.verbose,
    )

    def handle_sigint(sig, frame):
        runner._running = False
        print("\nStopping...")

    signal.signal(signal.SIGINT, handle_sigint)

    result = asyncio.run(runner.run())

    if args.save_report:
        out_path = Path(args.save_report)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(result, f, indent=2)
        print(f"Report saved to {out_path}")


if __name__ == "__main__":
    main()
