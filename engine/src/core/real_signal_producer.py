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
import statistics
import time
from collections import defaultdict, deque
from decimal import Decimal
from typing import Any, Optional

from src.core.config import get_settings
from src.core.models import Signal
from src.core.multi_signal import MultiStrategySignalProducer
from src.core.order_book import OrderBook
from src.core.triangular_scanner import TriangularScanner
from src.strategies.statistical_arb import _KalmanHedgeRatio

logger = logging.getLogger(__name__)

# Type aliases (matches shadow.py internal structure)
# symbol → exchange_id → OrderBook
_Books = dict[str, dict[str, OrderBook]]
# exchange_id → symbol → funding_rate
_Rates = dict[str, dict[str, float]]

# Cross-asset pairs evaluated on the SAME exchange (US-188)
CROSS_ASSET_PAIRS: list[tuple[str, str]] = [
    ("BTC/USDT", "ETH/USDT"),
    ("ETH/USDT", "SOL/USDT"),
    ("BTC/USDT", "SOL/USDT"),
]


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
        regime_detector: Any = None,
    ) -> None:
        self._producer = multi_signal_producer
        self._scanner = triangular_scanner
        self._futures_exchanges: set[str] = futures_exchanges or {"binance_futures", "okx_futures", "bybit_futures"}
        self._latency_tracker = latency_tracker
        self._stale_detector = stale_detector
        self._regime_detector = regime_detector
        # US-188: Rolling spread history for cross-asset stat arb z-score computation
        # Key: (symbolA, symbolB, exchange) → deque of log-price spreads
        self._spread_history: dict[tuple[str, str, str], deque[float]] = defaultdict(
            lambda: deque(maxlen=200)
        )
        # Per-pair Kalman filters for dynamic hedge ratio estimation
        self._stat_arb_kalman: dict[tuple[str, str, str], _KalmanHedgeRatio] = {}
        self._stat_arb_cooldown: dict[tuple[str, str, str], float] = {}
        from src.core.config_loader import get_config
        self._stat_arb_z_threshold = float(get_config("strategy_filters.stat_arb_z_threshold", default=2.5))
        self._stat_arb_cooldown_s = float(get_config("strategy_filters.stat_arb_cooldown_s", default=300))
        self._stat_arb_min_history = int(get_config("strategy_filters.stat_arb_min_history", default=120))
        self._stat_arb_korean = {"upbit", "bithumb", "coinone"}  # skip stale data
        # Bug 1-A: cache latest funding rates so _evaluate_spot_futures can use them
        self._latest_rates: _Rates = {}
        # US-230: rolling spread history for outlier filter
        # Key: (symbol, min(exA, exB), max(exA, exB)) → deque of spread_bps
        self._rolling_spread: dict[tuple[str, str, str], deque[float]] = defaultdict(
            lambda: deque(maxlen=200)
        )
        self._spread_filter_min_samples: int = 20
        self._spread_filter_multiplier: float = 3.0
        self._spread_ts_max_diff_s: float = 0.300  # 300ms timestamp cross-check
        # Rate-limit futures_spread_outlier logs: key=(symbol,ex_a,ex_b) → last_log_time
        self._outlier_log_cooldown: dict[tuple[str, str, str], float] = {}
        # S10: Warmup guard — skip signals for first 5 seconds after startup
        # Disabled in test mode to avoid breaking integration tests
        self._first_update_mono: float = 0.0
        _env = os.environ.get("ENGINE_ENV", "dev")  # os.environ直접: lru_cache monkeypatch 예외
        self._warmup_seconds: float = 5.0 if _env not in ("test",) else 0.0
        # S10 fix: per-exchange last-update timestamps for reconnect stale guard
        self._exchange_last_update: dict[str, float] = {}
        _stale_threshold = get_settings().operational.exchange_stale_threshold_s
        self._exchange_stale_threshold: float = _stale_threshold if _env != "test" else 9999.0

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
        S10: Skips signals during first 5 seconds (orderbook cold-start warmup).
        """
        signals: list[Signal] = []

        # S10: Warmup guard — skip all signals for first 5 seconds
        now_mono = time.monotonic()
        if self._first_update_mono == 0.0:
            self._first_update_mono = now_mono
        if (now_mono - self._first_update_mono) < self._warmup_seconds:
            return signals

        # S10 fix: track per-exchange last update time for reconnect stale guard
        self._exchange_last_update[exchange_id] = now_mono

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
        # Bug 1-A: cache rates so _evaluate_spot_futures can reference them
        self._latest_rates = rates
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

                # US-229: Orderbook freshness guard for spot-futures
                _sf_age_spot = time.monotonic() - spot_book.last_update_time if getattr(spot_book, "last_update_time", 0) > 0 else 999.0
                _sf_age_fut = time.monotonic() - fut_book.last_update_time if getattr(fut_book, "last_update_time", 0) > 0 else 999.0
                if _sf_age_spot > 3.0 or _sf_age_fut > 3.0:
                    continue

                # If futures > spot: buy spot, sell futures
                if float(fut_bid) > float(spot_ask):
                    # US-229: min basis filter — skip trivially small spreads
                    _sf_basis_bps = (float(fut_bid) - float(spot_ask)) / float(spot_ask) * 10000
                    _sf_min_bps = get_settings().operational.spot_futures_min_basis_bps
                    if _sf_basis_bps < _sf_min_bps:
                        continue
                    # US-230: rolling median spread outlier filter
                    _sf_key = (symbol, spot_ex, fut_ex)
                    _sf_history = self._rolling_spread[_sf_key]
                    _sf_history.append(_sf_basis_bps)
                    # timestamp cross-check: skip if books updated > 300ms apart
                    _sf_ts_spot = getattr(spot_book, "last_update_time", 0)
                    _sf_ts_fut = getattr(fut_book, "last_update_time", 0)
                    if _sf_ts_spot > 0 and _sf_ts_fut > 0:
                        if abs(_sf_ts_spot - _sf_ts_fut) > self._spread_ts_max_diff_s:
                            continue
                    if len(_sf_history) >= self._spread_filter_min_samples:
                        _sf_median = statistics.median(_sf_history)
                        if _sf_median > 0 and _sf_basis_bps > self._spread_filter_multiplier * _sf_median:
                            continue
                    spot_base = spot_ex.replace("binance_futures", "binance")
                    # Bug 1-A: use cached funding rate from latest snapshot
                    _funding_rate = self._latest_rates.get(fut_ex, {}).get(symbol, 0.0)
                    signal = await self._producer.produce_spot_futures_signal(
                        exchange_id=spot_base,
                        spot_symbol=symbol,
                        futures_symbol=f"{symbol}:USDT",
                        spot_price=Decimal(str(spot_ask)),
                        futures_price=Decimal(str(fut_bid)),
                        funding_rate=_funding_rate,
                    )
                    if signal is not None:
                        if signal.metadata is None:
                            signal.metadata = {}
                        signal.metadata["direction"] = "contango"
                        logger.info(
                            "real_signal_producer.spot_futures_signal",
                            extra={"symbol": symbol, "spot_ex": spot_ex, "fut_ex": fut_ex, "direction": "contango"},
                        )
                        signals.append(signal)

                # US-238: Backwardation path — spot > futures → sell spot, buy futures
                if float(spot_bid) > float(fut_ask):
                    _sf_basis_bps_back = (float(spot_bid) - float(fut_ask)) / float(fut_ask) * 10000
                    _sf_min_bps = get_settings().operational.spot_futures_min_basis_bps
                    if _sf_basis_bps_back < _sf_min_bps:
                        continue
                    # Rolling median spread outlier filter
                    _sf_key_back = (symbol, fut_ex, spot_ex)
                    _sf_history_back = self._rolling_spread[_sf_key_back]
                    _sf_history_back.append(_sf_basis_bps_back)
                    # Timestamp cross-check
                    _sf_ts_spot = getattr(spot_book, "last_update_time", 0)
                    _sf_ts_fut = getattr(fut_book, "last_update_time", 0)
                    if _sf_ts_spot > 0 and _sf_ts_fut > 0:
                        if abs(_sf_ts_spot - _sf_ts_fut) > self._spread_ts_max_diff_s:
                            continue
                    if len(_sf_history_back) >= self._spread_filter_min_samples:
                        _sf_median_back = statistics.median(_sf_history_back)
                        if _sf_median_back > 0 and _sf_basis_bps_back > self._spread_filter_multiplier * _sf_median_back:
                            continue
                    spot_base = spot_ex.replace("binance_futures", "binance")
                    _funding_rate = self._latest_rates.get(fut_ex, {}).get(symbol, 0.0)
                    signal = await self._producer.produce_spot_futures_signal(
                        exchange_id=spot_base,
                        spot_symbol=symbol,
                        futures_symbol=f"{symbol}:USDT",
                        spot_price=Decimal(str(spot_bid)),
                        futures_price=Decimal(str(fut_ask)),
                        funding_rate=_funding_rate,
                    )
                    if signal is not None:
                        if signal.metadata is None:
                            signal.metadata = {}
                        signal.metadata["direction"] = "backwardation"
                        logger.info(
                            "real_signal_producer.spot_futures_signal",
                            extra={"symbol": symbol, "spot_ex": spot_ex, "fut_ex": fut_ex, "direction": "backwardation"},
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

                # S10 fix: skip if either exchange recently reconnected (stale data guard)
                # Only applied once both exchanges have been seen (avoids blocking cold-start)
                now = time.monotonic()
                last_a = self._exchange_last_update.get(ex_a)
                last_b = self._exchange_last_update.get(ex_b)
                if last_a is not None and last_b is not None:
                    if (now - last_a) > self._exchange_stale_threshold or (now - last_b) > self._exchange_stale_threshold:
                        continue

                # US-184 + S10: stale data cross-validation (BOTH exchanges)
                if self._stale_detector is not None:
                    other_books_a = {k: v for k, v in fut_books.items() if k != ex_a}
                    if not self._stale_detector.check_cross_exchange(ex_a, symbol, book_a, {symbol: other_books_a}):
                        continue
                    other_books_b = {k: v for k, v in fut_books.items() if k != ex_b}
                    if not self._stale_detector.check_cross_exchange(ex_b, symbol, book_b, {symbol: other_books_b}):
                        continue

                # S10/US-221: Orderbook freshness guard — skip if book updated > 3s ago
                now_mono = time.monotonic()
                age_a = now_mono - book_a.last_update_time if book_a.last_update_time > 0 else 999.0
                age_b = now_mono - book_b.last_update_time if book_b.last_update_time > 0 else 999.0
                if age_a > 3.0 or age_b > 3.0:
                    continue

                # ex_a bid > ex_b ask → buy on ex_b, sell on ex_a
                if float(bid_a) > float(ask_b):
                    # US-184 + S10 + US-225: spread outlier filter
                    spread_bps = (float(bid_a) - float(ask_b)) / float(ask_b) * 10000
                    if spread_bps > 100:
                        _olk1 = (symbol, ex_b, ex_a)
                        _now1 = time.monotonic()
                        if _now1 - self._outlier_log_cooldown.get(_olk1, 0.0) > 60.0:
                            logger.warning(
                                "real_signal_producer.futures_spread_outlier",
                                extra={
                                    "symbol": symbol,
                                    "buy_ex": ex_b,
                                    "sell_ex": ex_a,
                                    "spread_bps": round(spread_bps, 1),
                                },
                            )
                            self._outlier_log_cooldown[_olk1] = _now1
                        if spread_bps > 200 and self._stale_detector is not None:
                            self._stale_detector.add_blacklist(ex_a, symbol, ttl_s=60.0)
                            self._stale_detector.add_blacklist(ex_b, symbol, ttl_s=60.0)
                        continue
                    # US-230: rolling median spread outlier filter
                    _ff_key = (symbol, min(ex_a, ex_b), max(ex_a, ex_b))
                    _ff_history = self._rolling_spread[_ff_key]
                    _ff_history.append(spread_bps)
                    if book_a.last_update_time > 0 and book_b.last_update_time > 0:
                        if abs(book_a.last_update_time - book_b.last_update_time) > self._spread_ts_max_diff_s:
                            continue
                    if len(_ff_history) >= self._spread_filter_min_samples:
                        _ff_median = statistics.median(_ff_history)
                        if _ff_median > 0 and spread_bps > self._spread_filter_multiplier * _ff_median:
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
                    # US-184 + S10 + US-225: spread outlier filter
                    spread_bps = (float(bid_b) - float(ask_a)) / float(ask_a) * 10000
                    if spread_bps > 100:
                        _olk2 = (symbol, ex_a, ex_b)
                        _now2 = time.monotonic()
                        if _now2 - self._outlier_log_cooldown.get(_olk2, 0.0) > 60.0:
                            logger.warning(
                                "real_signal_producer.futures_spread_outlier",
                                extra={
                                    "symbol": symbol,
                                    "buy_ex": ex_a,
                                    "sell_ex": ex_b,
                                    "spread_bps": round(spread_bps, 1),
                                },
                            )
                            self._outlier_log_cooldown[_olk2] = _now2
                        if spread_bps > 200 and self._stale_detector is not None:
                            self._stale_detector.add_blacklist(ex_a, symbol, ttl_s=60.0)
                            self._stale_detector.add_blacklist(ex_b, symbol, ttl_s=60.0)
                        continue
                    # US-230: rolling median spread outlier filter
                    _ff_key = (symbol, min(ex_a, ex_b), max(ex_a, ex_b))
                    _ff_history = self._rolling_spread[_ff_key]
                    _ff_history.append(spread_bps)
                    if book_a.last_update_time > 0 and book_b.last_update_time > 0:
                        if abs(book_a.last_update_time - book_b.last_update_time) > self._spread_ts_max_diff_s:
                            continue
                    if len(_ff_history) >= self._spread_filter_min_samples:
                        _ff_median = statistics.median(_ff_history)
                        if _ff_median > 0 and spread_bps > self._spread_filter_multiplier * _ff_median:
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
        """Evaluate cross-asset statistical arbitrage on the SAME exchange (US-188).

        For each configured pair (symbolA, symbolB), checks if both symbols have
        live orderbooks on exchange_id, then computes Kalman-filtered log-spread:
            spread = log(midA) - beta * log(midB)

        Emits a signal when z-score exceeds threshold AND min_history samples exist
        AND the per-pair cooldown has elapsed. Korean exchanges are excluded.
        """
        signals: list[Signal] = []

        # Korean exchanges have stale orderbook data — skip entirely
        if exchange_id in self._stat_arb_korean:
            return signals

        now = time.monotonic()

        for sym_a, sym_b in CROSS_ASSET_PAIRS:
            # Only evaluate when the incoming update is for one of the pair's symbols
            if symbol not in (sym_a, sym_b):
                continue

            # Look up both books on the SAME exchange
            book_a = all_books.get(sym_a, {}).get(exchange_id)
            book_b = all_books.get(sym_b, {}).get(exchange_id)
            if book_a is None or book_b is None:
                continue

            bid_a, ask_a = book_a.best_bid(), book_a.best_ask()
            bid_b, ask_b = book_b.best_bid(), book_b.best_ask()
            if None in (bid_a, ask_a, bid_b, ask_b):
                continue

            mid_a = (float(bid_a) + float(ask_a)) / 2
            mid_b = (float(bid_b) + float(ask_b)) / 2
            if mid_a <= 0 or mid_b <= 0:
                continue

            pair_key = (sym_a, sym_b, exchange_id)

            # Get or create Kalman filter for this pair
            if pair_key not in self._stat_arb_kalman:
                self._stat_arb_kalman[pair_key] = _KalmanHedgeRatio(
                    process_noise=1e-4,
                    observation_noise=5e-3,
                )
            kalman = self._stat_arb_kalman[pair_key]

            log_a = math.log(mid_a)
            log_b = math.log(mid_b)
            beta = kalman.update(log_b, log_a)
            spread = log_a - beta * log_b

            history = self._spread_history[pair_key]
            history.append(spread)

            if len(history) < self._stat_arb_min_history:
                continue

            # Compute rolling z-score
            mean_spread = sum(history) / len(history)
            variance = sum((s - mean_spread) ** 2 for s in history) / len(history)
            std_spread = math.sqrt(variance) if variance > 0 else 1e-10
            z_score = (spread - mean_spread) / std_spread

            if abs(z_score) < self._stat_arb_z_threshold:
                continue

            # Per-pair cooldown
            last_emit = self._stat_arb_cooldown.get(pair_key, 0.0)
            if now - last_emit < self._stat_arb_cooldown_s:
                continue

            # z > 0: symA overpriced vs symB → sell symA (sell_price=midA), buy symB (buy_price=midB)
            # z < 0: symA underpriced vs symB → buy symA (buy_price=midA), sell symB (sell_price=midB)
            if z_score > 0:
                buy_price = Decimal(str(mid_b))
                sell_price = Decimal(str(mid_a))
            else:
                buy_price = Decimal(str(mid_a))
                sell_price = Decimal(str(mid_b))

            sig = await self._producer.produce_statistical_arb_signal(
                symbol=sym_a,
                buy_exchange=exchange_id,
                sell_exchange=exchange_id,
                buy_price=buy_price,
                sell_price=sell_price,
                z_score=abs(z_score),
                symbol2=sym_b,
            )
            if sig is not None:
                # US-240: Mark as cross-asset signal for shadow.py routing
                if sig.metadata is None:
                    sig.metadata = {}
                sig.metadata["cross_asset"] = True
                self._stat_arb_cooldown[pair_key] = now
                logger.info(
                    "real_signal_producer.statistical_arb_signal",
                    extra={
                        "sym_a": sym_a, "sym_b": sym_b, "exchange": exchange_id,
                        "z_score": f"{z_score:.2f}", "beta": f"{beta:.4f}",
                        "history_len": len(history),
                        "cross_asset": True,
                    },
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
            # US-229: min funding rate diff filter (default 5 bps = 0.0005)
            from src.core.config_loader import get_config
            _fr_min_diff = float(get_config("strategy_filters.funding_min_diff_bps", default=5)) / 10000
            if diff < _fr_min_diff:
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
