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
        → _evaluate_statistical_arb
        → _evaluate_latency_arb
        → list[Signal]

    on_funding_rates_updated(rates, books)
        → _evaluate_funding_rate_arb
        → list[Signal]
"""
from __future__ import annotations

import logging
import math
import os
import time
from collections import defaultdict, deque
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
        latency_tracker: Any = None,
        stale_detector: Any = None,
    ) -> None:
        self._producer = multi_signal_producer
        self._scanner = triangular_scanner
        self._futures_exchanges: set[str] = futures_exchanges or {"binance_futures", "okx_futures", "bybit_futures"}
        self._latency_tracker = latency_tracker
        self._stale_detector = stale_detector
        # US-181: Rolling spread history for statistical arb z-score computation
        # Key: (symbol, exchange_a, exchange_b) → deque of log-price spreads
        self._spread_history: dict[tuple[str, str, str], deque[float]] = defaultdict(
            lambda: deque(maxlen=200)
        )
        self._stat_arb_cooldown: dict[tuple[str, str, str], float] = {}
        self._stat_arb_z_threshold = float(os.environ.get("STAT_ARB_Z_THRESHOLD", "8.0"))
        self._stat_arb_cooldown_s = float(os.environ.get("STAT_ARB_COOLDOWN_S", "300"))
        self._stat_arb_min_history = int(os.environ.get("STAT_ARB_MIN_HISTORY", "200"))
        self._stat_arb_korean = {"upbit", "bithumb", "coinone"}  # skip stale data

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

        # Statistical arb (US-181)
        signals.extend(
            await self._evaluate_statistical_arb(exchange_id, symbol, all_books)
        )

        # Latency arb (US-182)
        signals.extend(
            await self._evaluate_latency_arb(exchange_id, symbol, all_books)
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

        _korean = {"upbit", "bithumb", "coinone"}
        for spot_ex, spot_book in spot_books.items():
            if spot_ex in self._futures_exchanges:
                continue  # skip futures exchange entries in spot books
            if spot_ex in _korean:
                continue  # Korean stale orderbook data → fake basis spreads

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
        US-184: stale_detector cross-validation + 500bps outlier filter.
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

                # US-184: stale data cross-validation
                if self._stale_detector is not None:
                    other_books = {k: v for k, v in fut_books.items() if k != ex_a}
                    if not self._stale_detector.check_cross_exchange(ex_a, symbol, book_a, {symbol: other_books}):
                        continue

                # ex_a bid > ex_b ask → buy on ex_b, sell on ex_a
                if float(bid_a) > float(ask_b):
                    # US-184: 500bps outlier filter
                    spread_bps = (float(bid_a) - float(ask_b)) / float(ask_b) * 10000
                    if spread_bps > 500:
                        continue
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
                    # US-184: 500bps outlier filter
                    spread_bps = (float(bid_b) - float(ask_a)) / float(ask_a) * 10000
                    if spread_bps > 500:
                        continue
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

    async def _evaluate_statistical_arb(
        self,
        exchange_id: str,
        symbol: str,
        all_books: _Books,
    ) -> list[Signal]:
        """Evaluate statistical arbitrage via rolling z-score on log-price spread (US-181).

        Accumulates spread history per (symbol, ex_a, ex_b) pair. Only emits a
        signal when the z-score exceeds threshold AND min_history samples exist
        AND the per-pair cooldown has elapsed.
        """
        signals: list[Signal] = []
        sym_books = all_books.get(symbol, {})
        my_book = sym_books.get(exchange_id)
        if not my_book:
            return signals
        my_bid = my_book.best_bid()
        my_ask = my_book.best_ask()
        if my_bid is None or my_ask is None:
            return signals
        my_mid = (float(my_bid) + float(my_ask)) / 2
        if my_mid <= 0:
            return signals

        # Skip Korean exchanges (stale orderbook data → unreliable z-score)
        if exchange_id in self._stat_arb_korean:
            return signals

        now = time.monotonic()
        for other_ex, other_book in sym_books.items():
            if other_ex == exchange_id:
                continue
            if other_ex in self._stat_arb_korean:
                continue
            other_bid = other_book.best_bid()
            other_ask = other_book.best_ask()
            if other_bid is None or other_ask is None:
                continue
            other_mid = (float(other_bid) + float(other_ask)) / 2
            if other_mid <= 0:
                continue

            # Canonical pair key (sorted to avoid duplicates)
            pair_key = (symbol, *sorted([exchange_id, other_ex]))

            # Accumulate log-price spread
            log_spread = math.log(my_mid / other_mid)
            history = self._spread_history[pair_key]
            history.append(log_spread)

            # Need minimum history for meaningful z-score
            if len(history) < self._stat_arb_min_history:
                continue

            # Compute rolling z-score
            mean_spread = sum(history) / len(history)
            variance = sum((s - mean_spread) ** 2 for s in history) / len(history)
            std_spread = math.sqrt(variance) if variance > 0 else 1e-10
            z_score = (log_spread - mean_spread) / std_spread

            # Only emit when |z| exceeds threshold
            if abs(z_score) < self._stat_arb_z_threshold:
                continue

            # Per-pair cooldown
            last_emit = self._stat_arb_cooldown.get(pair_key, 0.0)
            if now - last_emit < self._stat_arb_cooldown_s:
                continue

            # z > 0 means my_mid is overpriced vs other → sell mine, buy other
            if z_score > 0:
                buy_ex, sell_ex = other_ex, exchange_id
                buy_price = Decimal(str(other_mid))
                sell_price = Decimal(str(my_mid))
            else:
                buy_ex, sell_ex = exchange_id, other_ex
                buy_price = Decimal(str(my_mid))
                sell_price = Decimal(str(other_mid))

            sig = await self._producer.produce_statistical_arb_signal(
                symbol=symbol,
                buy_exchange=buy_ex,
                sell_exchange=sell_ex,
                buy_price=buy_price,
                sell_price=sell_price,
                z_score=abs(z_score),
            )
            if sig is not None:
                self._stat_arb_cooldown[pair_key] = now
                logger.info(
                    "real_signal_producer.statistical_arb_signal",
                    extra={"symbol": symbol, "buy_ex": buy_ex, "sell_ex": sell_ex,
                           "z_score": f"{z_score:.2f}", "history_len": len(history)},
                )
                signals.append(sig)
        return signals

    async def _evaluate_latency_arb(
        self,
        exchange_id: str,
        symbol: str,
        all_books: _Books,
    ) -> list[Signal]:
        """Evaluate latency arbitrage using LatencyTracker lead-lag pairs (US-182)."""
        signals: list[Signal] = []
        if self._latency_tracker is None:
            return signals

        pairs = self._latency_tracker.lead_lag_pairs(threshold_ms=5.0)
        if not pairs:
            return signals

        sym_books = all_books.get(symbol, {})

        for fast_ex, slow_ex in pairs:
            fast_book = sym_books.get(fast_ex)
            slow_book = sym_books.get(slow_ex)
            if not fast_book or not slow_book:
                continue
            fast_bid = fast_book.best_bid()
            fast_ask = fast_book.best_ask()
            slow_bid = slow_book.best_bid()
            slow_ask = slow_book.best_ask()
            if any(v is None for v in [fast_bid, fast_ask, slow_bid, slow_ask]):
                continue
            # Apply stale detector if available
            if self._stale_detector is not None:
                other_books = {k: v for k, v in sym_books.items() if k != slow_ex}
                if not self._stale_detector.check_cross_exchange(slow_ex, symbol, slow_book, {symbol: other_books}):
                    continue
            fast_mid = (float(fast_bid) + float(fast_ask)) / 2
            slow_mid = (float(slow_bid) + float(slow_ask)) / 2
            if fast_mid <= 0 or slow_mid <= 0:
                continue
            # Derive latency diff from EMA values
            fast_info = self._latency_tracker.get_latency_info(fast_ex)
            slow_info = self._latency_tracker.get_latency_info(slow_ex)
            latency_diff_ms = (slow_info.ema_ms - fast_info.ema_ms) if (fast_info and slow_info) else 5.0
            sig = await self._producer.produce_latency_arb_signal(
                symbol=symbol,
                fast_exchange=fast_ex,
                slow_exchange=slow_ex,
                fast_price=Decimal(str(fast_mid)),
                slow_price=Decimal(str(slow_mid)),
                latency_diff_ms=latency_diff_ms,
            )
            if sig is not None:
                logger.info(
                    "real_signal_producer.latency_arb_signal",
                    extra={"symbol": symbol, "fast_ex": fast_ex, "slow_ex": slow_ex,
                           "latency_diff_ms": f"{latency_diff_ms:.1f}"},
                )
                signals.append(sig)
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
