"""RealDataSignalProducer — stateless signal evaluation extracted from ShadowMode.

Extracts the inline _evaluate_* logic from shadow.py into a reusable,
pure input/output class with no ShadowMode dependency.

Signals are produced by calling MultiStrategySignalProducer methods;
triangular detection is delegated to TriangularScanner.

Data flow:
    on_orderbook_update(exchange_id, symbol, book, all_books, futures_books)
        → _evaluate_triangular  (TriangularScanner)
        → _evaluate_spot_futures
        → _evaluate_futures_futures
        → list[Signal]

    on_funding_rates_updated(rates, books)
        → _evaluate_funding_rate_arb
        → list[Signal]
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Optional

from src.core.models import Signal
from src.core.multi_signal import MultiStrategySignalProducer
from src.core.order_book import OrderBook
from src.core.triangular_scanner import TriangularScanner

logger = logging.getLogger(__name__)

# Type aliases (matches shadow.py internal structure)
# symbol → exchange_id → OrderBook
_Books = dict[str, dict[str, OrderBook]]
# exchange_id → symbol → funding_rate
_Rates = dict[str, dict[str, float]]


class RealDataSignalProducer:
    """
    Evaluates arbitrage signals from real orderbook and funding rate data.

    Stateless with respect to orderbook data — all_books and futures_books
    are passed in on each call.  The embedded TriangularScanner maintains
    its own per-exchange cache for incremental updates.

    Parameters
    ----------
    multi_signal_producer : MultiStrategySignalProducer
        Used to create and publish Signal objects.
    triangular_scanner : TriangularScanner
        Bellman-Ford cycle detector; updated on every orderbook event.
    futures_exchanges : set[str] | None
        Exchange IDs that are futures (excluded from spot-side lookups).
    """

    def __init__(
        self,
        multi_signal_producer: MultiStrategySignalProducer,
        triangular_scanner: TriangularScanner,
        futures_exchanges: Optional[set[str]] = None,
    ) -> None:
        self._producer = multi_signal_producer
        self._scanner = triangular_scanner
        self._futures_exchanges: set[str] = futures_exchanges or {"binance_futures", "okx_futures", "bybit_futures"}

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def on_orderbook_update(
        self,
        exchange_id: str,
        symbol: str,
        book: OrderBook,
        all_books: _Books,
        futures_books: _Books,
    ) -> list[Signal]:
        """Evaluate all relevant strategies on a new orderbook update.

        Returns a (possibly empty) list of Signal objects produced.
        """
        signals: list[Signal] = []

        # Triangular arb (single exchange)
        signals.extend(
            await self._evaluate_triangular(exchange_id, symbol, book)
        )

        # Spot-futures basis (disabled for Korean exchanges — stale data)
        if exchange_id not in ("upbit", "bithumb", "coinone"):
            signals.extend(
                await self._evaluate_spot_futures(
                    exchange_id, symbol, all_books, futures_books
                )
            )

        # Futures-futures spread
        signals.extend(
            await self._evaluate_futures_futures(symbol, futures_books)
        )

        return signals

    async def on_funding_rates_updated(
        self,
        rates: _Rates,
        books: _Books,
    ) -> list[Signal]:
        """Evaluate funding rate arbitrage on a fresh rate snapshot.

        Parameters
        ----------
        rates : dict[exchange_id][symbol] → float
        books : dict[symbol][exchange_id] → OrderBook  (spot books for price reference)
        """
        return await self._evaluate_funding_rate_arb(rates, books)

    # ------------------------------------------------------------------
    # Internal evaluators (extracted verbatim from shadow.py)
    # ------------------------------------------------------------------

    async def _evaluate_triangular(
        self,
        exchange_id: str,
        symbol: str,
        book: OrderBook,
    ) -> list[Signal]:
        """Detect triangular arb on *exchange_id* via TriangularScanner.

        The scanner maintains its own per-exchange cache; we pass the
        latest book and collect any cycles it returns.
        """
        signals: list[Signal] = []
        cycles = self._scanner.on_orderbook_update(exchange_id, symbol, book)

        for cycle in cycles:
            signal = await self._producer.produce_triangular_signal(
                exchange_id=cycle.exchange_id,
                path=cycle.path,
                pairs=cycle.pairs,
                sides=cycle.sides,
                prices=cycle.prices,
                profit_pct=cycle.profit_pct,
            )
            if signal is not None:
                logger.info(
                    "real_signal_producer.triangular_signal",
                    extra={
                        "exchange": exchange_id,
                        "profit_bps": f"{float(cycle.profit_pct) * 10000:.1f}",
                    },
                )
                signals.append(signal)

        return signals

    async def _evaluate_spot_futures(
        self,
        exchange_id: str,
        symbol: str,
        all_books: _Books,
        futures_books: _Books,
    ) -> list[Signal]:
        """Spot-futures basis trade: compare spot price vs futures price.

        Exact logic extracted from shadow.py _evaluate_spot_futures().
        """
        signals: list[Signal] = []

        spot_books = all_books.get(symbol, {})
        fut_books = futures_books.get(symbol, {})

        if not spot_books or not fut_books:
            return signals

        for spot_ex, spot_book in spot_books.items():
            if spot_ex in self._futures_exchanges:
                continue  # skip futures exchange entries in spot books

            for fut_ex, fut_book in fut_books.items():
                spot_ask = spot_book.best_ask()
                fut_bid = fut_book.best_bid()
                spot_bid = spot_book.best_bid()
                fut_ask = fut_book.best_ask()

                if any(v is None for v in [spot_ask, fut_bid, spot_bid, fut_ask]):
                    continue

                # If futures > spot: buy spot, sell futures
                if float(fut_bid) > float(spot_ask):
                    spot_base = spot_ex.replace("binance_futures", "binance")
                    signal = await self._producer.produce_spot_futures_signal(
                        exchange_id=spot_base,
                        spot_symbol=symbol,
                        futures_symbol=f"{symbol}:USDT",
                        spot_price=Decimal(str(spot_ask)),
                        futures_price=Decimal(str(fut_bid)),
                        funding_rate=0.0,
                    )
                    if signal is not None:
                        logger.info(
                            "real_signal_producer.spot_futures_signal",
                            extra={"symbol": symbol, "spot_ex": spot_ex, "fut_ex": fut_ex},
                        )
                        signals.append(signal)

        return signals

    async def _evaluate_futures_futures(
        self,
        symbol: str,
        futures_books: _Books,
    ) -> list[Signal]:
        """Futures-futures spread: compare futures prices across exchanges.

        Exact logic extracted from shadow.py _evaluate_futures_futures().
        """
        signals: list[Signal] = []

        fut_books = futures_books.get(symbol, {})
        if len(fut_books) < 2:
            return signals

        exchanges = sorted(fut_books.keys())
        for i in range(len(exchanges)):
            for j in range(i + 1, len(exchanges)):
                ex_a, ex_b = exchanges[i], exchanges[j]
                book_a = fut_books[ex_a]
                book_b = fut_books[ex_b]

                bid_a = book_a.best_bid()
                ask_b = book_b.best_ask()
                bid_b = book_b.best_bid()
                ask_a = book_a.best_ask()

                if any(v is None for v in [bid_a, ask_b, bid_b, ask_a]):
                    continue

                # ex_a bid > ex_b ask → buy on ex_b, sell on ex_a
                if float(bid_a) > float(ask_b):
                    signal = await self._producer.produce_futures_futures_signal(
                        symbol=symbol,
                        buy_exchange=ex_b,
                        sell_exchange=ex_a,
                        buy_price=Decimal(str(ask_b)),
                        sell_price=Decimal(str(bid_a)),
                    )
                    if signal is not None:
                        logger.info(
                            "real_signal_producer.futures_futures_signal",
                            extra={"symbol": symbol, "buy_ex": ex_b, "sell_ex": ex_a},
                        )
                        signals.append(signal)

                # Reverse: ex_b bid > ex_a ask
                if float(bid_b) > float(ask_a):
                    signal = await self._producer.produce_futures_futures_signal(
                        symbol=symbol,
                        buy_exchange=ex_a,
                        sell_exchange=ex_b,
                        buy_price=Decimal(str(ask_a)),
                        sell_price=Decimal(str(bid_b)),
                    )
                    if signal is not None:
                        logger.info(
                            "real_signal_producer.futures_futures_signal",
                            extra={"symbol": symbol, "buy_ex": ex_a, "sell_ex": ex_b},
                        )
                        signals.append(signal)

        return signals

    async def _evaluate_funding_rate_arb(
        self,
        rates: _Rates,
        books: _Books,
    ) -> list[Signal]:
        """Compare funding rates across exchanges and generate arb signals.

        Exact logic extracted from shadow.py _evaluate_funding_rate_arb().
        """
        signals: list[Signal] = []

        # Collect all rates per symbol
        symbol_rates: dict[str, list[tuple[str, float]]] = {}
        for ex_id, sym_rates in rates.items():
            for sym, rate in sym_rates.items():
                symbol_rates.setdefault(sym, []).append((ex_id, rate))

        for symbol, rate_list in symbol_rates.items():
            if len(rate_list) < 2:
                continue

            rate_list.sort(key=lambda x: x[1])
            low_ex, low_rate = rate_list[0]
            high_ex, high_rate = rate_list[-1]

            diff = high_rate - low_rate
            if diff <= 0:
                continue

            # Reference price from any available spot book
            sym_books = books.get(symbol, {})
            if not sym_books:
                continue
            ref_book = next(iter(sym_books.values()))
            ref_bid = ref_book.best_bid()
            if ref_bid is None or ref_bid <= 0:
                continue

            signal = await self._producer.produce_funding_rate_signal(
                symbol=symbol,
                high_rate_exchange=high_ex,
                low_rate_exchange=low_ex,
                high_rate=high_rate,
                low_rate=low_rate,
                price=Decimal(str(ref_bid)),
            )
            if signal is not None:
                logger.info(
                    "real_signal_producer.funding_rate_signal",
                    extra={
                        "symbol": symbol,
                        "diff_bps": f"{diff * 10000:.1f}",
                        "high_ex": high_ex,
                        "low_ex": low_ex,
                    },
                )
                signals.append(signal)

        return signals
