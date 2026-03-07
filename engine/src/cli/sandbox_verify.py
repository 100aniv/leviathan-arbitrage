"""Sandbox verification script — test exchange connectivity and data quality.

Connects to exchange testnets, receives orderbook data, and reports
latency/spread statistics. Validates that the exchange adapter works
correctly before moving to live trading.

Usage:
    python -m src.cli.sandbox_verify --exchange binance --symbol BTC/USDT --duration 30
    python -m src.cli.sandbox_verify --exchange upbit --symbol BTC/KRW --duration 60
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
import time
from decimal import Decimal
from statistics import mean, median

logger = logging.getLogger(__name__)


class SandboxVerifier:
    """Verify exchange connectivity and data quality on testnet."""

    def __init__(
        self,
        exchange_id: str,
        symbol: str,
        duration: int = 60,
        sandbox: bool = True,
    ) -> None:
        self._exchange_id = exchange_id
        self._symbol = symbol
        self._duration = duration
        self._sandbox = sandbox
        self._running = False

        # Metrics
        self._tick_count = 0
        self._latencies: list[float] = []
        self._spreads: list[float] = []
        self._prices: list[float] = []
        self._errors: list[str] = []

    async def run(self) -> dict:
        """Run the verification."""
        from src.infra.exchange.ccxt_adapter import CCXTAdapter

        # Get API credentials from environment
        prefix = self._exchange_id.upper()
        api_key = os.environ.get(f"{prefix}_API_KEY", "")
        api_secret = os.environ.get(f"{prefix}_SECRET", "")
        passphrase = os.environ.get(f"{prefix}_PASSWORD", "")

        if not api_key:
            print(f"\nWARNING: {prefix}_API_KEY not set.")
            print(f"Set environment variables to connect to {self._exchange_id} testnet:")
            print(f"  export {prefix}_API_KEY=your_testnet_key")
            print(f"  export {prefix}_SECRET=your_testnet_secret")
            print(f"\nRunning with paper adapter fallback...")
            return await self._run_paper_fallback()

        print(f"\nConnecting to {self._exchange_id} {'testnet' if self._sandbox else 'mainnet'}...")
        print(f"  Symbol: {self._symbol}")
        print(f"  Duration: {self._duration}s")

        try:
            adapter = CCXTAdapter(
                exchange_id=self._exchange_id,
                api_key=api_key,
                api_secret=api_secret,
                passphrase=passphrase,
                sandbox=self._sandbox,
            )

            await adapter.connect()
            print(f"  Connected! Health: {adapter.health_score:.2f}")

            self._running = True
            start = time.time()

            while self._running and (time.time() - start) < self._duration:
                tick_start = time.time()
                try:
                    ob = await adapter.get_orderbook_snapshot(self._symbol, depth=5)
                    latency = (time.time() - tick_start) * 1000  # ms

                    if ob.bids and ob.asks:
                        best_bid = float(ob.bids[0].price)
                        best_ask = float(ob.asks[0].price)
                        spread_bps = (best_ask - best_bid) / best_bid * 10000

                        self._tick_count += 1
                        self._latencies.append(latency)
                        self._spreads.append(spread_bps)
                        self._prices.append((best_bid + best_ask) / 2)

                        if self._tick_count % 10 == 0:
                            print(
                                f"  [{self._tick_count:4d}] "
                                f"bid={best_bid:.2f} ask={best_ask:.2f} "
                                f"spread={spread_bps:.1f}bps "
                                f"latency={latency:.0f}ms"
                            )

                except Exception as e:
                    self._errors.append(str(e))
                    logger.warning("Tick error: %s", e)

                await asyncio.sleep(0.5)

            await adapter.disconnect()

        except Exception as e:
            self._errors.append(f"Connection error: {e}")
            print(f"\n  Connection failed: {e}")
            return self._build_report(success=False)

        return self._build_report(success=True)

    async def _run_paper_fallback(self) -> dict:
        """Run verification with paper adapter when no API keys available."""
        from src.execution.paper_adapter import PaperExchangeAdapter

        adapter = PaperExchangeAdapter(
            exchange_id=f"paper_{self._exchange_id}",
            initial_capital=Decimal("1000"),
            tick_interval=0.1,
        )

        await adapter.connect()
        print(f"  Paper adapter connected (simulated {self._exchange_id})")

        self._running = True
        start = time.time()

        while self._running and (time.time() - start) < self._duration:
            tick_start = time.time()
            try:
                ob = await adapter.get_orderbook_snapshot(self._symbol)
                latency = (time.time() - tick_start) * 1000

                if ob.bids and ob.asks:
                    best_bid = float(ob.bids[0].price)
                    best_ask = float(ob.asks[0].price)
                    spread_bps = (best_ask - best_bid) / best_bid * 10000

                    self._tick_count += 1
                    self._latencies.append(latency)
                    self._spreads.append(spread_bps)
                    self._prices.append((best_bid + best_ask) / 2)

                    if self._tick_count % 20 == 0:
                        print(
                            f"  [{self._tick_count:4d}] "
                            f"bid={best_bid:.2f} ask={best_ask:.2f} "
                            f"spread={spread_bps:.1f}bps "
                            f"latency={latency:.1f}ms"
                        )

            except Exception as e:
                self._errors.append(str(e))

            await asyncio.sleep(0.5)

        await adapter.disconnect()
        return self._build_report(success=True)

    def _build_report(self, success: bool) -> dict:
        """Build verification report."""
        report = {
            "exchange_id": self._exchange_id,
            "symbol": self._symbol,
            "sandbox": self._sandbox,
            "success": success,
            "duration_seconds": self._duration,
            "tick_count": self._tick_count,
            "error_count": len(self._errors),
        }

        if self._latencies:
            report["latency_ms"] = {
                "mean": round(mean(self._latencies), 1),
                "median": round(median(self._latencies), 1),
                "p95": round(sorted(self._latencies)[int(len(self._latencies) * 0.95)], 1),
                "min": round(min(self._latencies), 1),
                "max": round(max(self._latencies), 1),
            }

        if self._spreads:
            report["spread_bps"] = {
                "mean": round(mean(self._spreads), 2),
                "median": round(median(self._spreads), 2),
                "min": round(min(self._spreads), 2),
                "max": round(max(self._spreads), 2),
            }

        if self._prices:
            report["price"] = {
                "first": round(self._prices[0], 2),
                "last": round(self._prices[-1], 2),
                "min": round(min(self._prices), 2),
                "max": round(max(self._prices), 2),
            }

        # Health assessment
        health_score = 1.0
        if len(self._errors) > self._tick_count * 0.1:
            health_score -= 0.3
        if self._latencies and mean(self._latencies) > 1000:
            health_score -= 0.2
        if self._tick_count < 5:
            health_score -= 0.5
        report["health_score"] = max(0.0, round(health_score, 2))

        return report

    def print_report(self, report: dict) -> None:
        """Print human-readable verification report."""
        print(f"\n{'=' * 60}")
        print(f"Sandbox Verification Report: {report['exchange_id']}")
        print(f"{'=' * 60}")
        print(f"  Symbol:      {report['symbol']}")
        print(f"  Sandbox:     {report['sandbox']}")
        print(f"  Success:     {report['success']}")
        print(f"  Duration:    {report['duration_seconds']}s")
        print(f"  Ticks:       {report['tick_count']}")
        print(f"  Errors:      {report['error_count']}")

        if "latency_ms" in report:
            lat = report["latency_ms"]
            print(f"\n  Latency (ms):")
            print(f"    Mean:   {lat['mean']}")
            print(f"    Median: {lat['median']}")
            print(f"    P95:    {lat['p95']}")
            print(f"    Min:    {lat['min']}")
            print(f"    Max:    {lat['max']}")

        if "spread_bps" in report:
            sp = report["spread_bps"]
            print(f"\n  Spread (bps):")
            print(f"    Mean:   {sp['mean']}")
            print(f"    Median: {sp['median']}")
            print(f"    Min:    {sp['min']}")
            print(f"    Max:    {sp['max']}")

        if "price" in report:
            pr = report["price"]
            print(f"\n  Price:")
            print(f"    First:  ${pr['first']}")
            print(f"    Last:   ${pr['last']}")
            print(f"    Range:  ${pr['min']} - ${pr['max']}")

        hs = report.get("health_score", 0)
        status = "HEALTHY" if hs > 0.9 else ("DEGRADED" if hs > 0.5 else "UNHEALTHY")
        print(f"\n  Health Score: {hs} [{status}]")
        print(f"{'=' * 60}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="LEVIATHAN Sandbox Verification",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--exchange", default="binance", help="Exchange ID")
    parser.add_argument("--symbol", default="BTC/USDT", help="Trading symbol")
    parser.add_argument("--duration", type=int, default=30, help="Duration (seconds)")
    parser.add_argument("--live", action="store_true", help="Use mainnet (not testnet)")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    verifier = SandboxVerifier(
        exchange_id=args.exchange,
        symbol=args.symbol,
        duration=args.duration,
        sandbox=not args.live,
    )

    def handle_sigint(sig, frame):
        verifier._running = False
        print("\nStopping verification...")

    signal.signal(signal.SIGINT, handle_sigint)

    report = asyncio.run(verifier.run())
    verifier.print_report(report)

    sys.exit(0 if report.get("health_score", 0) > 0.5 else 1)


if __name__ == "__main__":
    main()
