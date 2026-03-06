"""Paper trading runner — run the full engine pipeline in paper mode.

Usage:
    python -m src.cli.paper_runner --duration 300 --report
    python -m src.cli.paper_runner --duration 60 --verbose
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import signal
import sys
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from src.core.metrics_collector import MetricsCollector, PerformanceReport
from src.execution.paper_adapter import PaperExchangeAdapter
from src.execution.executor import AtomicExecutor, ExecutionResult, ExecutionStatus
from src.execution.trade_consumer import TradeRequestConsumer
from src.infra.redis.memory_bus import InMemoryEventBus
from src.strategies.base import TradeRequest

logger = logging.getLogger(__name__)


class PaperTradingRunner:
    """Runs the full LEVIATHAN pipeline in paper mode for a specified duration."""

    def __init__(
        self,
        duration_seconds: int = 300,
        initial_capital: float = 70.0,
        spread_injection_rate: float = 0.4,
        spread_injection_bps: int = 50,
        tick_interval: float = 0.05,
        verbose: bool = False,
    ) -> None:
        self._duration = duration_seconds
        self._initial_capital = initial_capital
        self._spread_injection_rate = spread_injection_rate
        self._spread_injection_bps = spread_injection_bps
        self._tick_interval = tick_interval
        self._verbose = verbose
        self._running = False

        # Components
        self._metrics = MetricsCollector(initial_capital=initial_capital)
        self._event_bus = InMemoryEventBus()
        self._trade_log: list[dict] = []

    async def run(self) -> PerformanceReport:
        """Run the full paper trading pipeline."""
        self._running = True

        # Create two paper exchanges with different spread injections
        exchange_a = PaperExchangeAdapter(
            exchange_id="paper_binance",
            initial_capital=Decimal(str(self._initial_capital)),
            spread_injection_rate=self._spread_injection_rate,
            spread_injection_bps=self._spread_injection_bps,
            tick_interval=self._tick_interval,
        )
        exchange_b = PaperExchangeAdapter(
            exchange_id="paper_upbit",
            initial_capital=Decimal(str(self._initial_capital)),
            spread_injection_rate=self._spread_injection_rate,
            spread_injection_bps=self._spread_injection_bps + 15,
            tick_interval=self._tick_interval,
        )

        await exchange_a.connect()
        await exchange_b.connect()

        # Create executor
        adapters = {"paper_binance": exchange_a, "paper_upbit": exchange_b}
        executor = AtomicExecutor(exchanges=adapters)

        # Result callback
        def on_result(trade_req: TradeRequest, result: ExecutionResult) -> None:
            pnl = 0.0
            if result.status == ExecutionStatus.SUCCESS:
                # Estimate PnL from leg fills (LegResult has .trade with price/amount/fee)
                if result.leg1 and result.leg1.trade and result.leg2 and result.leg2.trade:
                    t1 = result.leg1.trade
                    t2 = result.leg2.trade
                    l1_cost = float(t1.price * t1.amount)
                    l2_cost = float(t2.price * t2.amount)
                    if t1.side.value == "buy":
                        pnl = l2_cost - l1_cost
                    else:
                        pnl = l1_cost - l2_cost
                    pnl -= float(t1.fee + t2.fee)

            self._metrics.record_trade(trade_req.strategy_id, pnl)
            self._trade_log.append({
                "timestamp": datetime.utcnow().isoformat(),
                "strategy_id": trade_req.strategy_id,
                "status": result.status.value,
                "pnl": pnl,
            })

            if self._verbose:
                status_str = result.status.value
                print(f"  [{status_str}] {trade_req.strategy_id}: PnL=${pnl:.6f}")

        # Create trade consumer
        consumer = TradeRequestConsumer(
            event_bus=self._event_bus,
            executor=executor,
            on_result=on_result,
        )

        # Start consumer
        await consumer.start()

        # Start orderbook generation and signal pipeline
        signal_tasks = await self._start_signal_pipeline(
            exchange_a, exchange_b, self._event_bus
        )

        print(f"\nPaper trading started (duration: {self._duration}s)")
        print(f"  Exchanges: paper_binance, paper_upbit")
        print(f"  Capital: ${self._initial_capital:.2f} per exchange")
        print(f"  Spread injection: {self._spread_injection_rate * 100:.0f}% @ {self._spread_injection_bps}bps")
        print(f"  Tick interval: {self._tick_interval}s")

        # Run for specified duration
        start = time.time()
        try:
            progress_interval = max(self._duration // 10, 1)
            while self._running and (time.time() - start) < self._duration:
                elapsed = time.time() - start
                remaining = self._duration - elapsed

                # Progress update
                if int(elapsed) > 0 and int(elapsed) % progress_interval == 0:
                    report = self._metrics.get_report()
                    print(
                        f"  [{int(elapsed)}s/{self._duration}s] "
                        f"trades={report.total_trades} "
                        f"PnL=${report.total_pnl:.4f} "
                        f"win={report.win_rate * 100:.0f}%"
                    )

                await asyncio.sleep(min(1.0, remaining))

        except asyncio.CancelledError:
            pass
        finally:
            # Cleanup
            await consumer.stop()
            for task in signal_tasks:
                task.cancel()
            await exchange_a.disconnect()
            await exchange_b.disconnect()

        report = self._metrics.get_report()
        return report

    async def _start_signal_pipeline(
        self,
        exchange_a: PaperExchangeAdapter,
        exchange_b: PaperExchangeAdapter,
        event_bus: InMemoryEventBus,
    ) -> list[asyncio.Task]:
        """Start a simplified signal pipeline that generates trade requests.

        Compares orderbooks between exchanges and publishes TradeRequests
        when spread exceeds threshold.
        """
        from src.core.models import OrderSide, OrderType

        tasks: list[asyncio.Task] = []
        latest_books: dict[str, dict] = {}

        def _on_orderbook_a(ob):
            latest_books["paper_binance"] = ob

        def _on_orderbook_b(ob):
            latest_books["paper_upbit"] = ob

        await exchange_a.subscribe_orderbook("BTC/USDT", _on_orderbook_a)
        await exchange_b.subscribe_orderbook("BTC/USDT", _on_orderbook_b)

        async def _arb_scanner():
            """Scan for arbitrage opportunities between exchanges."""
            min_spread_bps = 10  # Minimum spread to trigger trade
            while self._running:
                try:
                    if "paper_binance" in latest_books and "paper_upbit" in latest_books:
                        ob_a = latest_books["paper_binance"]
                        ob_b = latest_books["paper_upbit"]

                        # Get best bid/ask
                        if ob_a.bids and ob_a.asks and ob_b.bids and ob_b.asks:
                            a_bid = ob_a.bids[0].price
                            a_ask = ob_a.asks[0].price
                            b_bid = ob_b.bids[0].price
                            b_ask = ob_b.asks[0].price

                            # Check A buy / B sell spread
                            if b_bid > a_ask:
                                spread_bps = float((b_bid - a_ask) / a_ask * 10000)
                                if spread_bps > min_spread_bps:
                                    size = min(
                                        ob_a.asks[0].amount,
                                        ob_b.bids[0].amount,
                                        Decimal("0.001"),
                                    )
                                    trade_req = {
                                        "strategy_id": "cross_exchange_arb",
                                        "legs": [
                                            {
                                                "exchange_id": "paper_binance",
                                                "symbol": "BTC/USDT",
                                                "side": "buy",
                                                "order_type": "limit",
                                                "price": str(a_ask),
                                                "size": str(size),
                                            },
                                            {
                                                "exchange_id": "paper_upbit",
                                                "symbol": "BTC/USDT",
                                                "side": "sell",
                                                "order_type": "limit",
                                                "price": str(b_bid),
                                                "size": str(size),
                                            },
                                        ],
                                        "expected_edge": str(Decimal(str(spread_bps)) * Decimal("0.0001")),
                                        "urgency": "normal",
                                    }
                                    await event_bus.publish(
                                        "leviathan:trade_requests",
                                        trade_req,
                                    )

                            # Check B buy / A sell spread
                            if a_bid > b_ask:
                                spread_bps = float((a_bid - b_ask) / b_ask * 10000)
                                if spread_bps > min_spread_bps:
                                    size = min(
                                        ob_b.asks[0].amount,
                                        ob_a.bids[0].amount,
                                        Decimal("0.001"),
                                    )
                                    trade_req = {
                                        "strategy_id": "cross_exchange_arb",
                                        "legs": [
                                            {
                                                "exchange_id": "paper_upbit",
                                                "symbol": "BTC/USDT",
                                                "side": "buy",
                                                "order_type": "limit",
                                                "price": str(b_ask),
                                                "size": str(size),
                                            },
                                            {
                                                "exchange_id": "paper_binance",
                                                "symbol": "BTC/USDT",
                                                "side": "sell",
                                                "order_type": "limit",
                                                "price": str(a_bid),
                                                "size": str(size),
                                            },
                                        ],
                                        "expected_edge": str(Decimal(str(spread_bps)) * Decimal("0.0001")),
                                        "urgency": "normal",
                                    }
                                    await event_bus.publish(
                                        "leviathan:trade_requests",
                                        trade_req,
                                    )

                    await asyncio.sleep(self._tick_interval * 2)
                except asyncio.CancelledError:
                    break
                except Exception:
                    logger.exception("Arb scanner error")
                    await asyncio.sleep(0.5)

        task = asyncio.create_task(_arb_scanner())
        tasks.append(task)
        return tasks

    def save_trade_log(self, path: str | Path) -> None:
        """Save trade log to CSV."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not self._trade_log:
            print("No trades to save.")
            return
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["timestamp", "strategy_id", "status", "pnl"])
            writer.writeheader()
            writer.writerows(self._trade_log)
        print(f"Trade log saved to {path} ({len(self._trade_log)} trades)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="LEVIATHAN Paper Trading Runner",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--duration", type=int, default=60, help="Duration in seconds")
    parser.add_argument("--capital", type=float, default=70.0, help="Initial capital (USDT)")
    parser.add_argument("--injection-rate", type=float, default=0.4, help="Spread injection rate")
    parser.add_argument("--injection-bps", type=int, default=50, help="Spread injection bps")
    parser.add_argument("--tick-interval", type=float, default=0.05, help="Tick interval (seconds)")
    parser.add_argument("--report", action="store_true", help="Print detailed report")
    parser.add_argument("--verbose", action="store_true", help="Print each trade")
    parser.add_argument("--save-log", type=str, default=None, help="Save trade log CSV")
    parser.add_argument("--save-report", type=str, default=None, help="Save report JSON")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    runner = PaperTradingRunner(
        duration_seconds=args.duration,
        initial_capital=args.capital,
        spread_injection_rate=args.injection_rate,
        spread_injection_bps=args.injection_bps,
        tick_interval=args.tick_interval,
        verbose=args.verbose,
    )

    # Handle Ctrl+C
    def handle_sigint(sig, frame):
        runner._running = False
        print("\nStopping paper trading...")

    signal.signal(signal.SIGINT, handle_sigint)

    report = asyncio.run(runner.run())

    if args.report or True:  # Always print summary
        print(report.summary())

    if args.save_log:
        runner.save_trade_log(args.save_log)

    if args.save_report:
        out_path = Path(args.save_report)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(report.to_dict(), f, indent=2)
        print(f"Report saved to {out_path}")

    # Exit with appropriate code
    sys.exit(0 if report.total_trades > 0 else 1)


if __name__ == "__main__":
    main()
