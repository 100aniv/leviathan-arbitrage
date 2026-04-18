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
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from src.core.config import get_settings
from src.core.exchanges import KRW_EXCHANGES, FUTURES_TO_SPOT
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

# KRW/USDT 환율 (하드코딩 기본값 — 동적 갱신은 _KRW_TO_USDT_RATE 인스턴스 변수로 오버라이드 가능)
_DEFAULT_KRW_TO_USDT_RATE = Decimal("0.000714")  # ~1400 KRW/USDT

def _normalize_price_to_usdt(price: Decimal, exchange: str, symbol: str,
                               krw_rate: Decimal = _DEFAULT_KRW_TO_USDT_RATE) -> Decimal:
    """KRW 거래소의 KRW 가격을 USDT 단위로 정규화한다.

    cross-exchange 비교 시에만 사용 — PnL 계산에는 원본 가격을 사용해야 한다.
    """
    if symbol.endswith("/KRW") and exchange in KRW_EXCHANGES:
        return price * krw_rate
    return price


def _normalize_symbol(symbol: str) -> str:
    """BTC/KRW → BTC/USDT 로 정규화한다 (cross-exchange 심볼 매칭용)."""
    if symbol.endswith("/KRW"):
        return symbol[:-3] + "USDT"
    return symbol


# Cross-asset pairs evaluated on the SAME exchange (US-188)
CROSS_ASSET_PAIRS: list[tuple[str, str]] = [
    ("BTC/USDT", "ETH/USDT"),
    ("ETH/USDT", "SOL/USDT"),
    ("BTC/USDT", "SOL/USDT"),
    # KRW pairs for Korean exchange backtest (ignored on non-KRW exchanges — book lookup returns None)
    ("BTC/KRW", "ETH/KRW"),
    ("ETH/KRW", "SOL/KRW"),
    ("BTC/KRW", "SOL/KRW"),
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
        backtest_mode: bool = False,
    ) -> None:
        self._producer = multi_signal_producer
        self._scanner = triangular_scanner
        from src.core.exchanges import FUTURES_TO_SPOT
        self._futures_exchanges: set[str] = futures_exchanges or set(FUTURES_TO_SPOT.keys())
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
        # In backtest mode: use lower z-threshold (OHLCV hourly data is smoother than tick data,
        # so z-scores are systematically lower; 1.5 is appropriate for hourly backtest data)
        self._backtest_mode = backtest_mode
        self._stat_arb_z_threshold = float(get_config("strategy_filters.stat_arb_z_threshold", default=2.5))
        if backtest_mode:
            # Cap at 1.5 regardless of config — OHLCV hourly prices have less intraday noise
            # than live tick data, so live-tuned thresholds (2.0+) rarely trigger in backtest
            self._stat_arb_z_threshold = min(self._stat_arb_z_threshold, 1.5)
        self._stat_arb_cooldown_s = float(get_config("strategy_filters.stat_arb_cooldown_s", default=300))
        self._stat_arb_min_history = int(get_config("strategy_filters.stat_arb_min_history", default=120))
        # In backtest mode: skip Korean exchange filter (data is synthetic, not stale)
        # and skip wall-clock cooldowns (simulated time is passed instead)
        self._stat_arb_korean = set(KRW_EXCHANGES)  # skip stale data (live only)
        # Bug 1-A: cache latest funding rates so _evaluate_spot_futures can use them
        self._latest_rates: _Rates = {}
        # US-230: rolling spread history for outlier filter
        # Key: (symbol, min(exA, exB), max(exA, exB)) → deque of spread_bps
        self._rolling_spread: dict[tuple[str, str, str], deque[float]] = defaultdict(
            lambda: deque(maxlen=200)
        )
        self._spread_filter_min_samples: int = 20
        self._spread_filter_multiplier: float = 3.0
        # BUG-44: increased 300ms→1000ms. Bitget books15 is event-driven (not 100ms guaranteed
        # like Binance @depth20@100ms). During quiet markets a symbol may go 300-800ms without
        # a Bitget push while Binance fires every 100ms → all FF signals silently dropped.
        # 1000ms is still well below the 1500ms exchange-stale threshold.
        self._spread_ts_max_diff_s: float = 1.000
        # Rate-limit futures_spread_outlier logs: key=(symbol,ex_a,ex_b) → last_log_time
        self._outlier_log_cooldown: dict[tuple[str, str, str], float] = {}
        # Global throttle: at most 1 outlier log per 5s across all pairs
        self._outlier_global_last_log: float = 0.0
        # BUG-44: rate-limit ts_filter drop logs: key=(symbol,ex_a,ex_b) → last_log_time
        self._ts_filter_log_cooldown: dict[tuple[str, str, str], float] = {}
        self._ts_filter_global_last_log: float = 0.0
        # Periodic FF observability summary (every 60s) — Round29 fix
        self._ff_summary_last_ts: float = 0.0
        self._ff_max_spread_bps: float = -9999.0   # max (bid_a-ask_b)/ask_b*10000 seen
        self._ff_pairs_evaluated: int = 0
        self._ff_stale_dropped: int = 0
        self._ff_freshness_dropped: int = 0
        self._ff_ts_sync_dropped: int = 0         # Round31: ts_diff > 1s filter drops
        self._ff_max_bps_post_fresh: float = -9999.0  # Round31: max spread AFTER freshness
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
        simulated_ts: float | None = None,
    ) -> list[Signal]:
        """Evaluate all relevant strategies on a new orderbook update.

        Returns a (possibly empty) list of Signal objects produced.
        S10: Skips signals during first 5 seconds (orderbook cold-start warmup).

        Parameters
        ----------
        simulated_ts : float | None
            Unix epoch seconds from historical data (backtest mode). When provided,
            wall-clock cooldowns use this instead of time.monotonic() so that
            a 6-month replay in 1s of wall time doesn't block every signal.
        """
        signals: list[Signal] = []

        # S10: Warmup guard — skip all signals for first 5 seconds
        # In backtest mode: skip warmup (data is pre-validated, no cold-start issue)
        now_mono = time.monotonic()
        if not self._backtest_mode:
            if self._first_update_mono == 0.0:
                self._first_update_mono = now_mono
            if (now_mono - self._first_update_mono) < self._warmup_seconds:
                return signals

        # S10 fix: track per-exchange last update time for reconnect stale guard
        self._exchange_last_update[exchange_id] = now_mono

        # Effective time for cooldown checks: simulated time in backtest, wall clock in live
        _now_for_cooldown = simulated_ts if simulated_ts is not None else now_mono

        # Triangular arb (single exchange)
        signals.extend(
            await self._evaluate_triangular(exchange_id, symbol, book)
        )

        # Spot-futures basis (disabled for Korean exchanges in live — stale data)
        if self._backtest_mode or exchange_id not in KRW_EXCHANGES:
            signals.extend(
                await self._evaluate_spot_futures(
                    exchange_id, symbol, all_books, futures_books
                )
            )

        # Futures-futures spread
        signals.extend(
            await self._evaluate_futures_futures(symbol, futures_books)
        )

        # Cross-exchange KRW↔USDT arb (Kimchi premium)
        # BUG-106: removed backtest-only gate — live mode needs KRW signal visibility.
        # Trade execution still requires strategy_id="cross_exchange_v1" enabled in
        # strategy_activation.json + FX oracle wired (currently hardcoded 0.000714).
        if exchange_id in KRW_EXCHANGES and symbol.endswith("/KRW"):
            signals.extend(
                await self._evaluate_cross_exchange_krw(
                    exchange_id, symbol, book, all_books, simulated_ts=simulated_ts
                )
            )

        # Statistical arb (US-181)
        signals.extend(
            await self._evaluate_statistical_arb(
                exchange_id, symbol, all_books, now=_now_for_cooldown
            )
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

        # BUG-103.2: snapshot inner dicts to prevent concurrent collector mutation
        # during iteration (outer shallow-copy in live.py:1002 only protects outer keys).
        # BUG-104: filter fut_books to futures exchanges only — caller passes full
        # {exchange: book} dict, so without filter fut_ex includes spot exchanges,
        # producing invalid spot-spot "basis" signals (belongs to cross_exchange strategy).
        spot_books = dict(all_books.get(symbol, {}))
        fut_books = {
            ex_id: book
            for ex_id, book in futures_books.get(symbol, {}).items()
            if ex_id in self._futures_exchanges
        }

        if not spot_books or not fut_books:
            return signals

        for spot_ex, spot_book in spot_books.items():
            if spot_ex in self._futures_exchanges:
                continue  # skip futures exchange entries in spot books
            if spot_ex in KRW_EXCHANGES:
                continue  # Korean stale orderbook data → fake basis spreads

            for fut_ex, fut_book in fut_books.items():
                spot_ask = spot_book.best_ask()
                fut_bid = fut_book.best_bid()
                spot_bid = spot_book.best_bid()
                fut_ask = fut_book.best_ask()

                if any(v is None for v in [spot_ask, fut_bid, spot_bid, fut_ask]):
                    continue

                # US-229: Orderbook freshness guard for spot-futures
                # (BUG-98 v145: 3s→5s to match FF, support lower-liquidity pair update cadence)
                _sf_age_spot = time.monotonic() - spot_book.last_update_time if getattr(spot_book, "last_update_time", 0) > 0 else 999.0
                _sf_age_fut = time.monotonic() - fut_book.last_update_time if getattr(fut_book, "last_update_time", 0) > 0 else 999.0
                if _sf_age_spot > 5.0 or _sf_age_fut > 5.0:
                    continue

                # If futures > spot: buy spot, sell futures
                if float(fut_bid) > float(spot_ask):
                    # US-229: min basis filter — skip trivially small spreads
                    _sf_basis_bps = (float(fut_bid) - float(spot_ask)) / float(spot_ask) * 10000
                    _sf_min_bps = get_settings().operational.spot_futures_min_basis_bps
                    if _sf_basis_bps < _sf_min_bps:
                        continue
                    # US-230: rolling median spread outlier filter
                    _sf_key = (symbol, min(spot_ex, fut_ex), max(spot_ex, fut_ex), "contango")
                    _sf_history = self._rolling_spread[_sf_key]
                    # timestamp cross-check: skip if books updated > 300ms apart
                    # BUG-66: append AFTER ts_diff filter to avoid polluting median
                    _sf_ts_spot = getattr(spot_book, "last_update_time", 0)
                    _sf_ts_fut = getattr(fut_book, "last_update_time", 0)
                    if _sf_ts_spot > 0 and _sf_ts_fut > 0:
                        if abs(_sf_ts_spot - _sf_ts_fut) > self._spread_ts_max_diff_s:
                            continue
                    _sf_history.append(_sf_basis_bps)
                    if len(_sf_history) >= self._spread_filter_min_samples:
                        _sf_median = statistics.median(_sf_history)
                        if _sf_median > 0 and _sf_basis_bps > self._spread_filter_multiplier * _sf_median:
                            continue
                    spot_base = FUTURES_TO_SPOT.get(spot_ex, spot_ex)
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
                    _sf_basis_bps_back = (float(spot_bid) - float(fut_ask)) / float(spot_ask) * 10000
                    _sf_min_bps = get_settings().operational.spot_futures_min_basis_bps
                    if _sf_basis_bps_back < _sf_min_bps:
                        continue
                    # Rolling median spread outlier filter
                    _sf_key_back = (symbol, min(spot_ex, fut_ex), max(spot_ex, fut_ex), "backwardation")
                    _sf_history_back = self._rolling_spread[_sf_key_back]
                    # Timestamp cross-check (BUG-66: append AFTER ts_diff filter)
                    _sf_ts_spot = getattr(spot_book, "last_update_time", 0)
                    _sf_ts_fut = getattr(fut_book, "last_update_time", 0)
                    if _sf_ts_spot > 0 and _sf_ts_fut > 0:
                        if abs(_sf_ts_spot - _sf_ts_fut) > self._spread_ts_max_diff_s:
                            continue
                    _sf_history_back.append(_sf_basis_bps_back)
                    if len(_sf_history_back) >= self._spread_filter_min_samples:
                        _sf_median_back = statistics.median(_sf_history_back)
                        if _sf_median_back > 0 and _sf_basis_bps_back > self._spread_filter_multiplier * _sf_median_back:
                            continue
                    spot_base = FUTURES_TO_SPOT.get(spot_ex, spot_ex)
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

        # BUG-30: filter to futures-only exchanges — spot exchanges must not be paired
        # as futures-futures (e.g. "binance" vs "binance_futures" is a basis trade, not FF arb)
        # BUG-103.2: snapshot inner dict to prevent concurrent collector mutation
        _all_for_symbol = dict(futures_books.get(symbol, {}))
        fut_books = {
            ex_id: book
            for ex_id, book in _all_for_symbol.items()
            if ex_id in self._futures_exchanges
        }
        if len(fut_books) < 2:
            # Round30 fix: flush summary even on early return so 60s timer always fires
            _avail = sorted(_all_for_symbol.keys())
            # One-shot BTC/USDT diagnostic: log book state to understand missing exchange
            if symbol == "BTC/USDT" and not getattr(self, "_ff_btc_debug_done", False):
                logger.info(
                    "real_signal_producer.ff_btc_debug symbol=BTC/USDT fut_books=%d avail=%s fut_exch=%s",
                    len(fut_books),
                    _avail,
                    sorted(self._futures_exchanges),
                )
                self._ff_btc_debug_done = True
            self._flush_ff_summary(
                signals,
                early_return_reason=f"fut_books={len(fut_books)} all_exch={_avail}",
            )
            return signals

        # Round33: Post-reconnect cooldown — skip FF signals for 3s after any futures
        # exchange reconnects. Prevents artificial spreads from stale orderbook data
        # that survives briefly after a WS disconnect/reconnect cycle.
        try:
            from src.collectors.base_collector import COLLECTOR_LAST_CONNECT
            _now_rc = time.monotonic()
            _RECONNECT_COOLDOWN = 3.0
            for _rc_ex in fut_books:
                _rc_last = COLLECTOR_LAST_CONNECT.get(_rc_ex, 0.0)
                if _rc_last > 0 and (_now_rc - _rc_last) < _RECONNECT_COOLDOWN:
                    self._flush_ff_summary(
                        signals,
                        early_return_reason=f"reconnect_cooldown={_rc_ex} age={round(_now_rc - _rc_last, 1)}s",
                    )
                    return signals
        except Exception:
            pass

        exchanges = sorted(fut_books.keys())
        for i in range(len(exchanges)):
            for j in range(i + 1, len(exchanges)):
                ex_a, ex_b = exchanges[i], exchanges[j]
                if ex_a == ex_b:
                    continue
                book_a = fut_books[ex_a]
                book_b = fut_books[ex_b]

                bid_a = book_a.best_bid()
                ask_b = book_b.best_ask()
                bid_b = book_b.best_bid()
                ask_a = book_a.best_ask()

                if any(v is None for v in [bid_a, ask_b, bid_b, ask_a]):
                    continue

                # Round29 observability: track max spread (positive=inverted, negative=how far)
                self._ff_pairs_evaluated += 1
                _spread_ab = (float(bid_a) - float(ask_b)) / float(ask_b) * 10000
                _spread_ba = (float(bid_b) - float(ask_a)) / float(ask_a) * 10000
                _pair_max = max(_spread_ab, _spread_ba)
                if _pair_max > self._ff_max_spread_bps:
                    self._ff_max_spread_bps = _pair_max

                # S10 fix: skip if either exchange recently reconnected (stale data guard)
                # Only applied once both exchanges have been seen (avoids blocking cold-start)
                now = time.monotonic()
                last_a = self._exchange_last_update.get(ex_a)
                last_b = self._exchange_last_update.get(ex_b)
                if last_a is not None and last_b is not None:
                    if (now - last_a) > self._exchange_stale_threshold or (now - last_b) > self._exchange_stale_threshold:
                        self._ff_stale_dropped += 1
                        continue

                # US-184 + S10: stale data cross-validation (BOTH exchanges)
                if self._stale_detector is not None:
                    other_books_a = {k: v for k, v in fut_books.items() if k != ex_a}
                    if not self._stale_detector.check_cross_exchange(ex_a, symbol, book_a, {symbol: other_books_a}):
                        continue
                    other_books_b = {k: v for k, v in fut_books.items() if k != ex_b}
                    if not self._stale_detector.check_cross_exchange(ex_b, symbol, book_b, {symbol: other_books_b}):
                        continue

                # S10/US-221: Orderbook freshness guard — skip if book updated > 5s ago
                # (v141: 3s→5s 완화. 저유동성 페어 업데이트 주기 수용, fresh_drop 70%→30% 목표)
                now_mono = time.monotonic()
                age_a = now_mono - book_a.last_update_time if book_a.last_update_time > 0 else 999.0
                age_b = now_mono - book_b.last_update_time if book_b.last_update_time > 0 else 999.0
                if age_a > 5.0 or age_b > 5.0:
                    # BUG-44 diagnostic: log when freshness guard drops a pair (rate-limited)
                    _fk = (symbol, min(ex_a, ex_b), max(ex_a, ex_b))
                    if (now_mono - self._ts_filter_log_cooldown.get(_fk, 0.0) > 60.0
                            and now_mono - self._ts_filter_global_last_log > 10.0):
                        logger.debug(
                            "real_signal_producer.ff_freshness_drop",
                            extra={
                                "symbol": symbol, "ex_a": ex_a, "ex_b": ex_b,
                                "age_a_ms": round(age_a * 1000, 0),
                                "age_b_ms": round(age_b * 1000, 0),
                            },
                        )
                        self._ts_filter_log_cooldown[_fk] = now_mono
                        self._ts_filter_global_last_log = now_mono
                    self._ff_freshness_dropped += 1
                    continue

                # Round31: track max spread AFTER freshness check (more accurate than pre-fresh)
                if _pair_max > self._ff_max_bps_post_fresh:
                    self._ff_max_bps_post_fresh = _pair_max

                # ex_a bid > ex_b ask → buy on ex_b, sell on ex_a
                if float(bid_a) > float(ask_b):
                    # US-184 + S10 + US-225: spread outlier filter
                    spread_bps = (float(bid_a) - float(ask_b)) / float(ask_b) * 10000
                    if spread_bps > 100:
                        _olk1 = (symbol, ex_b, ex_a)
                        _now1 = time.monotonic()
                        if (_now1 - self._outlier_log_cooldown.get(_olk1, 0.0) > 300.0
                                and _now1 - self._outlier_global_last_log > 5.0):
                            logger.warning(
                                "real_signal_producer.futures_spread_outlier sym=%s bps=%.1f buy=%s sell=%s",
                                symbol, spread_bps, ex_b, ex_a,
                                extra={
                                    "symbol": symbol,
                                    "buy_ex": ex_b,
                                    "sell_ex": ex_a,
                                    "spread_bps": round(spread_bps, 1),
                                },
                            )
                            self._outlier_log_cooldown[_olk1] = _now1
                            self._outlier_global_last_log = _now1
                        if spread_bps > 200 and self._stale_detector is not None:
                            self._stale_detector.add_blacklist(ex_a, symbol, ttl_s=60.0)
                            self._stale_detector.add_blacklist(ex_b, symbol, ttl_s=60.0)
                        continue
                    # US-230: rolling median spread outlier filter
                    _ff_key = (symbol, min(ex_a, ex_b), max(ex_a, ex_b))
                    _ff_history = self._rolling_spread[_ff_key]
                    if book_a.last_update_time > 0 and book_b.last_update_time > 0:
                        _ts_diff1 = abs(book_a.last_update_time - book_b.last_update_time)
                        if _ts_diff1 > self._spread_ts_max_diff_s:
                            self._ff_ts_sync_dropped += 1  # Round31
                            # BUG-44: log when ts-sync filter drops a signal
                            _tsk1 = (symbol, min(ex_a, ex_b), max(ex_a, ex_b))
                            _now_ts1 = time.monotonic()
                            if (_now_ts1 - self._ts_filter_log_cooldown.get(_tsk1, 0.0) > 60.0
                                    and _now_ts1 - self._ts_filter_global_last_log > 10.0):
                                logger.debug(
                                    "real_signal_producer.ff_ts_filter_drop",
                                    extra={
                                        "symbol": symbol, "ex_a": ex_a, "ex_b": ex_b,
                                        "diff_ms": round(_ts_diff1 * 1000, 1),
                                        "threshold_ms": round(self._spread_ts_max_diff_s * 1000, 0),
                                    },
                                )
                                self._ts_filter_log_cooldown[_tsk1] = _now_ts1
                                self._ts_filter_global_last_log = _now_ts1
                            continue
                    # BUG-66: append AFTER ts_diff filter to avoid polluting median with stale readings
                    _ff_history.append(spread_bps)
                    if len(_ff_history) >= self._spread_filter_min_samples:
                        _ff_median = statistics.median(_ff_history)
                        if _ff_median > 0 and spread_bps > self._spread_filter_multiplier * _ff_median:
                            continue
                    # BUG-23: compute book_age_ms for stale_guard (oldest of two books).
                    # last_update_time is monotonic time (consistent with age_a/age_b checks above).
                    _ff_now1 = time.monotonic()
                    _ff_age_ms1 = 0.0
                    if book_a.last_update_time > 0 and book_b.last_update_time > 0:
                        _ff_age_ms1 = max(0.0, (_ff_now1 - min(book_a.last_update_time, book_b.last_update_time)) * 1000)
                    signal = await self._producer.produce_futures_futures_signal(
                        symbol=symbol,
                        buy_exchange=ex_b,
                        sell_exchange=ex_a,
                        buy_price=Decimal(str(ask_b)),
                        sell_price=Decimal(str(bid_a)),
                        book_age_ms=_ff_age_ms1,
                    )
                    if signal is not None:
                        logger.info(
                            "real_signal_producer.futures_futures_signal",
                            extra={"symbol": symbol, "buy_ex": ex_b, "sell_ex": ex_a},
                        )
                        signals.append(signal)

                # Reverse: ex_b bid > ex_a ask (elif prevents double-signal on crossed book)
                elif float(bid_b) > float(ask_a):
                    # US-184 + S10 + US-225: spread outlier filter
                    spread_bps = (float(bid_b) - float(ask_a)) / float(ask_a) * 10000
                    if spread_bps > 100:
                        _olk2 = (symbol, ex_a, ex_b)
                        _now2 = time.monotonic()
                        if (_now2 - self._outlier_log_cooldown.get(_olk2, 0.0) > 300.0
                                and _now2 - self._outlier_global_last_log > 5.0):
                            logger.warning(
                                "real_signal_producer.futures_spread_outlier sym=%s bps=%.1f buy=%s sell=%s",
                                symbol, spread_bps, ex_a, ex_b,
                                extra={
                                    "symbol": symbol,
                                    "buy_ex": ex_a,
                                    "sell_ex": ex_b,
                                    "spread_bps": round(spread_bps, 1),
                                },
                            )
                            self._outlier_log_cooldown[_olk2] = _now2
                            self._outlier_global_last_log = _now2
                        if spread_bps > 200 and self._stale_detector is not None:
                            self._stale_detector.add_blacklist(ex_a, symbol, ttl_s=60.0)
                            self._stale_detector.add_blacklist(ex_b, symbol, ttl_s=60.0)
                        continue
                    # US-230: rolling median spread outlier filter
                    _ff_key = (symbol, min(ex_a, ex_b), max(ex_a, ex_b))
                    _ff_history = self._rolling_spread[_ff_key]
                    if book_a.last_update_time > 0 and book_b.last_update_time > 0:
                        _ts_diff2 = abs(book_a.last_update_time - book_b.last_update_time)
                        if _ts_diff2 > self._spread_ts_max_diff_s:
                            self._ff_ts_sync_dropped += 1  # Round31
                            # BUG-44: log when ts-sync filter drops a signal
                            _tsk2 = (symbol, min(ex_a, ex_b), max(ex_a, ex_b))
                            _now_ts2 = time.monotonic()
                            if (_now_ts2 - self._ts_filter_log_cooldown.get(_tsk2, 0.0) > 60.0
                                    and _now_ts2 - self._ts_filter_global_last_log > 10.0):
                                logger.debug(
                                    "real_signal_producer.ff_ts_filter_drop",
                                    extra={
                                        "symbol": symbol, "ex_a": ex_a, "ex_b": ex_b,
                                        "diff_ms": round(_ts_diff2 * 1000, 1),
                                        "threshold_ms": round(self._spread_ts_max_diff_s * 1000, 0),
                                    },
                                )
                                self._ts_filter_log_cooldown[_tsk2] = _now_ts2
                                self._ts_filter_global_last_log = _now_ts2
                            continue
                    # BUG-66: append AFTER ts_diff filter to avoid polluting median with stale readings
                    _ff_history.append(spread_bps)
                    if len(_ff_history) >= self._spread_filter_min_samples:
                        _ff_median = statistics.median(_ff_history)
                        if _ff_median > 0 and spread_bps > self._spread_filter_multiplier * _ff_median:
                            continue
                    # BUG-23: compute book_age_ms for stale_guard (oldest of two books).
                    # last_update_time is monotonic time (consistent with age_a/age_b checks above).
                    _ff_now2 = time.monotonic()
                    _ff_age_ms2 = 0.0
                    if book_a.last_update_time > 0 and book_b.last_update_time > 0:
                        _ff_age_ms2 = max(0.0, (_ff_now2 - min(book_a.last_update_time, book_b.last_update_time)) * 1000)
                    signal = await self._producer.produce_futures_futures_signal(
                        symbol=symbol,
                        buy_exchange=ex_a,
                        sell_exchange=ex_b,
                        buy_price=Decimal(str(ask_a)),
                        sell_price=Decimal(str(bid_b)),
                        book_age_ms=_ff_age_ms2,
                    )
                    if signal is not None:
                        logger.info(
                            "real_signal_producer.futures_futures_signal",
                            extra={"symbol": symbol, "buy_ex": ex_a, "sell_ex": ex_b},
                        )
                        signals.append(signal)

        # Round29/30: periodic FF observability summary (every 60s)
        self._flush_ff_summary(signals)

        return signals

    def _flush_ff_summary(
        self,
        signals: list,
        early_return_reason: str | None = None,
    ) -> None:
        """Flush FF observability summary if 60s window has elapsed.

        Called at ALL return points in _evaluate_futures_futures (Round30 fix:
        was only called at normal return, missing early-return cases).
        """
        _now_sum = time.monotonic()
        if _now_sum - self._ff_summary_last_ts >= 60.0:
            _max_bps = round(self._ff_max_spread_bps, 2) if self._ff_max_spread_bps > -9000 else None
            _max_bps_pf = round(self._ff_max_bps_post_fresh, 2) if self._ff_max_bps_post_fresh > -9000 else None
            _early = f" early_return={early_return_reason}" if early_return_reason else ""
            logger.info(
                "real_signal_producer.ff_summary pairs=%d max_bps=%s max_bps_pf=%s stale=%d fresh_drop=%d ts_drop=%d sigs=%d%s",
                self._ff_pairs_evaluated,
                _max_bps,
                _max_bps_pf,
                self._ff_stale_dropped,
                self._ff_freshness_dropped,
                self._ff_ts_sync_dropped,
                len(signals),
                _early,
            )
            self._ff_summary_last_ts = _now_sum
            self._ff_max_spread_bps = -9999.0
            self._ff_max_bps_post_fresh = -9999.0
            self._ff_pairs_evaluated = 0
            self._ff_stale_dropped = 0
            self._ff_freshness_dropped = 0
            self._ff_ts_sync_dropped = 0

    async def _evaluate_statistical_arb(
        self,
        exchange_id: str,
        symbol: str,
        all_books: _Books,
        now: float | None = None,
    ) -> list[Signal]:
        """Evaluate cross-asset statistical arbitrage on the SAME exchange (US-188).

        For each configured pair (symbolA, symbolB), checks if both symbols have
        live orderbooks on exchange_id, then computes Kalman-filtered log-spread:
            spread = log(midA) - beta * log(midB)

        Emits a signal when z-score exceeds threshold AND min_history samples exist
        AND the per-pair cooldown has elapsed.
        Korean exchanges are excluded in live mode; in backtest mode (synthetic data)
        they are included.

        Parameters
        ----------
        now : float | None
            Effective timestamp for cooldown checks. Use simulated time in backtest
            (passed from on_orderbook_update) so wall-clock 300s cooldown doesn't
            block signals when replaying months of data in seconds.
        """
        signals: list[Signal] = []

        # Korean exchanges have stale orderbook data — skip in live mode only
        if not self._backtest_mode and exchange_id in self._stat_arb_korean:
            return signals

        if now is None:
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
                sig.metadata["rsp_validated"] = True
                sig.metadata["rsp_z_score"] = float(z_score)
                sig.metadata["rsp_history_len"] = len(history)
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

        # BUG-103.2: snapshot inner dict (concurrency safety)
        sym_books = dict(all_books.get(symbol, {}))

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
            # Freshness guard: skip if either book is older than 3s
            _age_fast = time.monotonic() - fast_book.last_update_time if getattr(fast_book, "last_update_time", 0) > 0 else 999.0
            _age_slow = time.monotonic() - slow_book.last_update_time if getattr(slow_book, "last_update_time", 0) > 0 else 999.0
            if _age_fast > 3.0 or _age_slow > 3.0:
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

    async def _evaluate_cross_exchange_krw(
        self,
        krw_exchange: str,
        krw_symbol: str,
        krw_book: "OrderBook",
        all_books: "_Books",
        simulated_ts: float | None = None,
    ) -> list[Signal]:
        """KRW↔USDT cross-exchange 차익 평가 (backtest 전용, K-BT-10~12).

        BTC/KRW (Upbit/Bithumb/Coinone) 가격을 USDT로 정규화한 뒤
        BTC/USDT (Binance/Bybit/OKX 등) 오더북과 비교한다.

        KRW 가격 정규화: price_usdt = price_krw * _DEFAULT_KRW_TO_USDT_RATE
        신호 생성은 produce_cross_exchange_signal() 대신
        produce_triangular_signal()처럼 직접 Signal 을 구성한다 — cross_exchange
        전용 producer 메서드가 없으므로 StatisticalArbSignal 포맷을 재사용.
        """
        signals: list[Signal] = []

        usdt_symbol = _normalize_symbol(krw_symbol)  # BTC/KRW → BTC/USDT

        # KRW 오더북의 USDT 환산 가격
        krw_bid = krw_book.best_bid()
        krw_ask = krw_book.best_ask()
        if krw_bid is None or krw_ask is None:
            return signals

        krw_bid_usdt = _normalize_price_to_usdt(krw_bid, krw_exchange, krw_symbol)
        krw_ask_usdt = _normalize_price_to_usdt(krw_ask, krw_exchange, krw_symbol)

        # USDT 거래소 오더북 조회
        # BUG-103.2: snapshot inner dict (concurrency safety)
        usdt_books = dict(all_books.get(usdt_symbol, {}))
        for usdt_exchange, usdt_book in usdt_books.items():
            if usdt_exchange in KRW_EXCHANGES:
                continue  # USDT 거래소만 비교

            usdt_bid = usdt_book.best_bid()
            usdt_ask = usdt_book.best_ask()
            if usdt_bid is None or usdt_ask is None:
                continue

            # 방향 1: KRW 거래소가 더 비쌈 → KRW 거래소에서 팔고 USDT 거래소에서 삼
            if float(krw_bid_usdt) > float(usdt_ask):
                spread_bps = (float(krw_bid_usdt) - float(usdt_ask)) / float(usdt_ask) * 10000
                # 비합리적 스프레드 필터 (100bps 이상은 환율 오차로 간주)
                if spread_bps <= 0 or spread_bps > 500:
                    continue
                _ce_key = (usdt_symbol, usdt_exchange, krw_exchange)
                _ce_hist = self._rolling_spread[_ce_key]
                _ce_hist.append(spread_bps)
                if len(_ce_hist) >= self._spread_filter_min_samples:
                    _med = statistics.median(_ce_hist)
                    if _med > 0 and spread_bps > self._spread_filter_multiplier * _med:
                        continue
                spread_pct = Decimal(str(spread_bps / 10000))
                sig = Signal(
                    strategy_id="cross_exchange_v1",
                    symbol=usdt_symbol,
                    buy_exchange=usdt_exchange,
                    sell_exchange=krw_exchange,
                    buy_price=usdt_ask,
                    sell_price=krw_bid_usdt,  # USDT 환산가 (비교용)
                    spread_pct=spread_pct,
                    confidence=min(1.0, spread_bps / 50.0),
                    volume=usdt_ask * Decimal("0.001"),
                    timestamp=datetime.now(timezone.utc),
                    metadata={
                        "krw_normalized": True,
                        "krw_rate": str(_DEFAULT_KRW_TO_USDT_RATE),
                        "direction": "sell_krw",
                        "krw_exchange": krw_exchange,
                        "krw_symbol": krw_symbol,
                    },
                )
                logger.info(
                    "real_signal_producer.cross_krw_signal",
                    extra={
                        "usdt_ex": usdt_exchange, "krw_ex": krw_exchange,
                        "symbol": usdt_symbol, "spread_bps": f"{spread_bps:.1f}",
                        "direction": "sell_krw",
                    },
                )
                signals.append(sig)

            # 방향 2: USDT 거래소가 더 비쌈 → USDT 거래소에서 팔고 KRW 거래소에서 삼
            if float(usdt_bid) > float(krw_ask_usdt):
                spread_bps = (float(usdt_bid) - float(krw_ask_usdt)) / float(krw_ask_usdt) * 10000
                if spread_bps <= 0 or spread_bps > 500:
                    continue
                _ce_key2 = (usdt_symbol, krw_exchange, usdt_exchange)
                _ce_hist2 = self._rolling_spread[_ce_key2]
                _ce_hist2.append(spread_bps)
                if len(_ce_hist2) >= self._spread_filter_min_samples:
                    _med2 = statistics.median(_ce_hist2)
                    if _med2 > 0 and spread_bps > self._spread_filter_multiplier * _med2:
                        continue
                spread_pct2 = Decimal(str(spread_bps / 10000))
                sig = Signal(
                    strategy_id="cross_exchange_v1",
                    symbol=usdt_symbol,
                    buy_exchange=krw_exchange,
                    sell_exchange=usdt_exchange,
                    buy_price=krw_ask_usdt,  # USDT 환산가 (비교용)
                    sell_price=usdt_bid,
                    spread_pct=spread_pct2,
                    confidence=min(1.0, spread_bps / 50.0),
                    volume=usdt_bid * Decimal("0.001"),
                    timestamp=datetime.now(timezone.utc),
                    metadata={
                        "krw_normalized": True,
                        "krw_rate": str(_DEFAULT_KRW_TO_USDT_RATE),
                        "direction": "buy_krw",
                        "krw_exchange": krw_exchange,
                        "krw_symbol": krw_symbol,
                    },
                )
                logger.info(
                    "real_signal_producer.cross_krw_signal",
                    extra={
                        "usdt_ex": usdt_exchange, "krw_ex": krw_exchange,
                        "symbol": usdt_symbol, "spread_bps": f"{spread_bps:.1f}",
                        "direction": "buy_krw",
                    },
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

        _fr_pass_diff = 0
        _fr_no_book = 0
        _fr_low_diff = 0
        _fr_single_ex = 0
        for symbol, rate_list in symbol_rates.items():
            if len(rate_list) < 2:
                _fr_single_ex += 1
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
                _fr_low_diff += 1
                continue

            _fr_pass_diff += 1
            # Reference price from any available USDT spot book
            # BUG-01: exclude KRW exchanges (prices off by ~1400x vs USDT)
            # BUG-103.2: snapshot inner dict (concurrency safety)
            sym_books = dict(books.get(symbol, {}))
            if not sym_books:
                _fr_no_book += 1
                continue
            ref_book = None
            for _ex_id, _book in sym_books.items():
                if _ex_id not in KRW_EXCHANGES:
                    ref_book = _book
                    break
            if ref_book is None:
                ref_book = next(iter(sym_books.values()))  # fallback: best effort
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

        logger.info(
            "real_signal_producer.fr_arb_summary total_symbols=%d single_ex=%d low_diff=%d pass_diff=%d no_book=%d signals=%d",
            len(symbol_rates), _fr_single_ex, _fr_low_diff, _fr_pass_diff, _fr_no_book, len(signals),
        )
        return signals
