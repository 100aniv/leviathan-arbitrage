"""Signal generator — friction-aware pipeline (Amendment 3A).

Pipeline:
    OrderBook → Price Hub → Raw Signal → Friction Filter (all costs)
    → Max Rollback Cost Gate → Net Signal → Dedup → Redis Streams
"""
from __future__ import annotations

import logging
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from src.core.events import SignalEvent
from src.core.models import Signal
from src.core.order_book import OrderBook
from src.core.price_hub import PriceHub
from src.friction.cost_calculator import CostCalculator
from src.core.stale_detector import StaleOrderbookDetector

logger = logging.getLogger(__name__)


def compute_depth_trade_size(
    buy_depth: Decimal,
    sell_depth: Decimal,
    depth_fraction: Decimal | None = None,
    max_trade: Decimal | None = None,
) -> Decimal:
    """Compute trade size from L1 orderbook depth (SG-5).

    Returns clamped value in [0.001, max_trade].
    """
    frac = depth_fraction or Decimal(os.getenv("SHADOW_DEPTH_FRACTION", "0.10"))
    cap = max_trade or Decimal(os.getenv("SHADOW_MAX_TRADE_SIZE", "10"))
    depth_size = min(buy_depth, sell_depth) * frac
    return max(Decimal("0.001"), min(depth_size, cap))


@dataclass
class SignalConfig:
    strategy_id: str = "cross_exchange_spot"
    min_edge: Decimal = Decimal("0.0001")     # minimum net profit as fraction of notional (1 bps)
    max_spread_pct: Decimal = Decimal("0.05") # max gross spread as fraction (5%) — reject data anomalies
    cooldown_seconds: float = 5.0              # dedup suppression window (5s prevents overtrading on marginal edges)
    max_rollback_cost_usd: Decimal = Decimal("50")
    # US-248: conservative defaults — BTC ADV ~15000, sigma ~0.03; old 1000/0.001 caused ~7.7x slippage underestimate
    default_adv: Decimal = Decimal("10000")
    default_sigma: Decimal = Decimal("0.03")
    min_price_usd: Decimal = Decimal("0.10")
    max_book_age_seconds: float = 30.0  # reject orderbooks not updated within this window
    min_delta_update_count: int = 3    # STALE_MIN_DELTA_UPDATES: min deltas since snapshot for delta exchanges
    min_volume_usd: Decimal = Decimal("0")  # US-162: minimum 24h volume filter (0 = disabled)


class SignalGenerator:
    """
    Generates friction-filtered arbitrage signals from orderbook updates.

    Flow:
        1. Update PriceHub with new orderbook.
        2. Find global best bid (sell exchange) and best ask (buy exchange).
        3. Require different exchanges — same-exchange arb is not valid.
        4. Compute raw gross spread; skip if zero or negative.
        5. Run full friction calculation (fees + slippage + network + rollback).
        6. Apply min_edge gate: net_profit / notional >= min_edge.
        7. Apply max_rollback_cost gate.
        8. Deduplicate: suppress same (symbol, buy_ex, sell_ex) within cooldown window.
        9. Emit Signal and publish to Redis Streams.
    """

    SIGNAL_STREAM = "leviathan:signals"

    def __init__(
        self,
        price_hub: PriceHub,
        cost_calculator: CostCalculator,
        config: SignalConfig | None = None,
        event_bus: Any | None = None,
        stale_detector: StaleOrderbookDetector | None = None,
        regime_detector: Any | None = None,  # US-084
        ml_scorer: Any | None = None,  # US-094: ONNXSignalScorer
        dynamic_sizer: Any | None = None,  # US-130: DynamicSizer
        ml_feature_pipeline: Any | None = None,  # US-253: MLFeaturePipeline
        ml_canary: Any | None = None,  # US-253: MLCanary staged rollout
        adaptive_threshold: Any | None = None,  # US-255: PerStrategyAdaptiveThreshold
    ) -> None:
        self._hub = price_hub
        self._calc = cost_calculator
        self._config = config or SignalConfig()
        self._event_bus = event_bus
        self._stale_detector = stale_detector
        self._last_signal: dict[str, float] = {}  # dedup_key -> last emit timestamp
        self._regime_detector = regime_detector  # US-084
        self._ml_scorer = ml_scorer  # US-094
        self._dynamic_sizer = dynamic_sizer  # US-130
        self._ml_feature_pipeline = ml_feature_pipeline  # US-253
        self._ml_canary = ml_canary  # US-253
        self._adaptive_threshold = adaptive_threshold  # US-255: per-strategy threshold
        self._crisis_start_time: float | None = None  # US-173: CRISIS timeout tracking
        self._price_history: dict[str, list[Decimal]] = {}  # US-248: mid-price cache per symbol

    def _dedup_key(self, buy_ex: str, sell_ex: str, symbol: str) -> str:
        return f"{symbol}:{buy_ex}:{sell_ex}"

    def _is_duplicate(self, key: str) -> bool:
        last = self._last_signal.get(key)
        if last is None:
            return False
        return (time.time() - last) < self._config.cooldown_seconds

    def _mark_emitted(self, key: str) -> None:
        self._last_signal[key] = time.time()

    def _compute_dynamic_adv(self, symbol: str, buy_book: OrderBook, sell_book: OrderBook) -> Decimal:
        """Estimate ADV from top-5 orderbook depth (bid+ask volume sum). US-248."""
        total_depth = Decimal("0")
        for book in (buy_book, sell_book):
            sorted_bids = sorted(book.bids.items(), reverse=True)[:5]
            sorted_asks = sorted(book.asks.items())[:5]
            for _, qty in sorted_bids + sorted_asks:
                total_depth += qty
        return max(total_depth, Decimal("1"))

    def _compute_dynamic_sigma(
        self,
        symbol: str,
        buy_book: OrderBook | None = None,
        sell_book: OrderBook | None = None,
    ) -> Decimal:
        """Estimate sigma from recent mid-price returns std dev. US-248.

        Cold-start fallback: if price history is insufficient, use bid-ask spread
        of the provided orderbooks as a proxy for short-term volatility.
        This avoids over-penalizing signals when no history is available yet.
        """
        prices = self._price_history.get(symbol)
        if prices is not None and len(prices) >= 10:
            returns = [
                (prices[i] - prices[i - 1]) / prices[i - 1]
                for i in range(1, len(prices))
                if prices[i - 1] != 0
            ]
            if len(returns) >= 5:
                mean_r = sum(returns) / len(returns)
                variance = sum((r - mean_r) ** 2 for r in returns) / len(returns)
                sigma = Decimal(str(math.sqrt(float(variance))))
                return max(sigma, Decimal("0.0001"))

        # Cold-start: estimate sigma from orderbook spread as proxy
        if buy_book is not None:
            best_ask = buy_book.best_ask()  # returns Decimal | None
            best_bid = buy_book.best_bid()  # returns Decimal | None
            if best_ask is not None and best_bid is not None and best_ask > 0:
                spread_frac = (best_ask - best_bid) / best_ask
                # Use spread fraction directly as sigma proxy; clamp to [1e-5, 0.05]
                cold_sigma = max(Decimal("0.00001"), min(spread_frac, Decimal("0.05")))
                return cold_sigma

        return self._config.default_sigma

    async def on_orderbook_update(
        self,
        book: OrderBook,
        books: dict[str, OrderBook],
        trade_size: Decimal | None = None,
    ) -> Optional[Signal]:
        """
        Process an orderbook update through the full signal pipeline.

        Args:
            book:       The newly updated orderbook.
            books:      All current orderbooks for this symbol, keyed by exchange.
            trade_size: Order size in base asset units.

        Returns Signal if all gates pass, None otherwise.
        """
        self._hub.update(book)
        symbol = book.symbol

        best_bid = self._hub.best_bid(symbol)
        best_ask = self._hub.best_ask(symbol)

        # Need quotes from at least two exchanges
        if best_bid is None or best_ask is None:
            return None

        # Same-exchange arb is not valid
        if best_bid.exchange == best_ask.exchange:
            return None

        buy_exchange = best_ask.exchange   # buy at lowest ask
        sell_exchange = best_bid.exchange  # sell at highest bid
        buy_price = best_ask.price
        sell_price = best_bid.price

        # US-248: Cache mid price for sigma estimation
        mid_price = (buy_price + sell_price) / 2
        history = self._price_history.setdefault(symbol, [])
        history.append(mid_price)
        if len(history) > 120:  # keep last 120 ticks (~2 min at 1s cadence)
            history.pop(0)

        # Raw spread gate
        if sell_price <= buy_price:
            return None

        # Min price gate — filter out penny coins (e.g. QKC at $0.003)
        if buy_price < self._config.min_price_usd:
            return None

        # US-162: Volume filter — skip low-liquidity symbols
        # Uses buy_book.volume_24h_usd if available; graceful skip if absent
        if self._config.min_volume_usd > Decimal("0"):
            buy_vol = getattr(books.get(buy_exchange), "volume_24h_usd", None)
            sell_vol = getattr(books.get(sell_exchange), "volume_24h_usd", None)
            # Use the lower of the two volumes as the conservative estimate
            if buy_vol is not None and sell_vol is not None:
                min_vol = min(Decimal(str(buy_vol)), Decimal(str(sell_vol)))
                if min_vol < self._config.min_volume_usd:
                    logger.debug(
                        "volume_filter_rejected symbol=%s min_vol=%.2f threshold=%.2f",
                        symbol, float(min_vol), float(self._config.min_volume_usd),
                    )
                    return None

        # Max spread gate — reject data anomalies (e.g. stale/incremental orderbooks)
        raw_spread_frac = (sell_price - buy_price) / buy_price
        if raw_spread_frac > self._config.max_spread_pct:
            logger.debug("spread_anomaly_rejected", symbol=symbol,
                         spread_pct=f"{float(raw_spread_frac)*100:.2f}%",
                         buy_ex=best_ask.exchange, sell_ex=best_bid.exchange)
            return None

        # Need both orderbooks for friction calculation
        buy_book = books.get(buy_exchange)
        sell_book = books.get(sell_exchange)
        if buy_book is None or sell_book is None:
            return None

        # Empty-book guard — book object exists but L2 data not yet received
        # (common during WS warmup or after delta updates empty a side).
        # Silently skip; CEXOrderbookSlippage.predict() would raise ValueError otherwise.
        if buy_book.best_ask() is None or sell_book.best_bid() is None:
            logger.debug(
                "empty_book_skipped symbol=%s buy_ex=%s sell_ex=%s",
                symbol, buy_exchange, sell_exchange,
            )
            return None

        # Staleness gate — reject if either orderbook hasn't updated recently
        now_mono = time.monotonic()
        max_age = self._config.max_book_age_seconds
        if max_age > 0:
            for label, ob in [("buy", buy_book), ("sell", sell_book)]:
                if ob.last_update_time > 0 and (now_mono - ob.last_update_time) > max_age:
                    logger.debug(
                        "stale_orderbook_rejected symbol=%s exchange=%s age=%.1fs",
                        symbol, ob.exchange, now_mono - ob.last_update_time,
                    )
                    return None

        # Blacklist gate — fast reject for known stale pairs (US-066)
        if self._stale_detector is not None:
            for _label, ob in [("buy", buy_book), ("sell", sell_book)]:
                if self._stale_detector.is_blacklisted(ob.exchange, symbol):
                    logger.debug(
                        "blacklisted_rejected symbol=%s exchange=%s", symbol, ob.exchange
                    )
                    return None

        # Delta exchange minimum update count gate — require enough deltas since last snapshot (US-066)
        DELTA_EXCHANGES = {"bithumb"}
        min_count = self._config.min_delta_update_count
        for _label, ob in [("buy", buy_book), ("sell", sell_book)]:
            if ob.exchange in DELTA_EXCHANGES and ob.update_count < min_count:
                logger.debug(
                    "low_update_count_rejected symbol=%s exchange=%s count=%d min=%d",
                    symbol, ob.exchange, ob.update_count, min_count,
                )
                return None

        # Depth-based sizing (SG-5): auto-compute from L1 depth if not specified
        if trade_size is None:
            try:
                buy_depth = buy_book.volume_at_price(best_ask.price, "ask")
                sell_depth = sell_book.volume_at_price(best_bid.price, "bid")
                trade_size = compute_depth_trade_size(buy_depth, sell_depth)
            except Exception:
                trade_size = Decimal("1")

        # Friction filter — use actual base asset for network cost
        transfer_coin = symbol.split("/")[0] if "/" in symbol else "XRP"
        try:
            friction = self._calc.calculate(
                buy_exchange=buy_exchange,
                sell_exchange=sell_exchange,
                buy_book=buy_book,
                sell_book=sell_book,
                size=trade_size,
                buy_price=buy_price,
                sell_price=sell_price,
                adv=self._compute_dynamic_adv(symbol, buy_book, sell_book),
                sigma=self._compute_dynamic_sigma(symbol, buy_book, sell_book),
                transfer_coin=transfer_coin,
            )
        except Exception as exc:
            logger.debug("Friction calculation failed for %s: %s", symbol, exc)
            return None

        notional = buy_price * trade_size
        net_edge = friction.net_profit / notional if notional > 0 else Decimal("0")

        # Min edge gate — US-084 / US-173 / US-255: regime-adaptive + per-strategy threshold
        # US-255: read per-strategy adaptive edge (strategy_type = config.strategy_id)
        if self._adaptive_threshold is not None:
            try:
                strategy_edge_bps = self._adaptive_threshold.get_edge(self._config.strategy_id)
                strategy_edge = Decimal(str(strategy_edge_bps)) / Decimal("10000")
                effective_min_edge = max(self._config.min_edge, strategy_edge)
            except Exception:
                effective_min_edge = self._config.min_edge
        else:
            effective_min_edge = self._config.min_edge
        if self._regime_detector is not None:
            from src.tuning.regime_detector import REGIME_MIN_EDGE, MarketRegime
            regime = self._regime_detector.current_regime
            # US-173: CRISIS timeout — reset to HIGH after 30 minutes
            if regime == MarketRegime.CRISIS:
                now_ts = time.time()
                if self._crisis_start_time is None:
                    self._crisis_start_time = now_ts
                elif now_ts - self._crisis_start_time > 1800:
                    regime = MarketRegime.HIGH
                    self._crisis_start_time = None
            else:
                self._crisis_start_time = None
            regime_edge = REGIME_MIN_EDGE.get(regime, self._config.min_edge)
            effective_min_edge = max(effective_min_edge, regime_edge)
        if net_edge < effective_min_edge:
            return None

        # Max rollback cost gate
        if friction.rollback_cost_expected > self._config.max_rollback_cost_usd:
            return None

        # US-172: ML scorer filter — soft filter (reject with log), confidence update
        ml_score: float = 0.5  # neutral default when scorer unavailable
        if self._ml_scorer and self._ml_scorer.enabled:
            try:
                import numpy as np
                # US-253: Full ML feature pipeline (fallback to 3-feature stub)
                features = None
                if self._ml_feature_pipeline is not None:
                    try:
                        prices = self._price_history.get(symbol, [])
                        if len(prices) >= 10:
                            pf = [float(p) for p in prices[-100:]]
                            returns_arr = np.array(
                                [(pf[i] - pf[i-1]) / pf[i-1] for i in range(1, len(pf))],
                                dtype=np.float64,
                            )
                            spread_arr = np.full(len(returns_arr), float(net_edge), dtype=np.float64)
                            vol_arr = np.full(len(returns_arr), float(trade_size), dtype=np.float64)
                            raw = self._ml_feature_pipeline.extract(
                                returns=returns_arr,
                                spreads=spread_arr,
                                volumes=vol_arr,
                            )
                            n_exp = getattr(self._ml_scorer, "_n_features", 20)
                            padded = np.zeros(n_exp, dtype=np.float32)
                            n = min(len(raw), n_exp)
                            padded[:n] = raw[:n]
                            features = padded.reshape(1, -1)
                    except Exception:
                        pass
                if features is None:
                    features = np.array(
                        [[float(net_edge * 10000), float(trade_size), float(self._config.default_sigma)]],
                        dtype=np.float32,
                    )
                # US-253: MLCanary staged rollout gate
                if self._ml_canary is not None:
                    try:
                        if not self._ml_canary.should_use_ml():
                            features = np.array(
                                [[float(net_edge * 10000), float(trade_size), float(self._config.default_sigma)]],
                                dtype=np.float32,
                            )
                    except Exception:
                        pass
                score = self._ml_scorer.predict_signal(features)
                if not math.isfinite(score):
                    score = 0.5
                if score < self._ml_scorer.score_threshold:
                    logger.debug(
                        "ml_scorer_rejected symbol=%s score=%.3f threshold=%.3f",
                        symbol, score, self._ml_scorer.score_threshold,
                    )
                    return None
                ml_score = score
            except Exception as exc:
                logger.debug("ml_scorer_failed (non-fatal): %s", exc)

        # US-130: Dynamic sizing — adjust trade_size via DynamicSizer if available
        if self._dynamic_sizer is not None:
            try:
                from src.tuning.regime_detector import MarketRegime
                regime = MarketRegime.NORMAL
                if self._regime_detector is not None and hasattr(self._regime_detector, "current_regime"):
                    regime = self._regime_detector.current_regime
                bid_depth_usd = buy_price * buy_book.volume_at_price(best_ask.price, "ask")
                dynamic_size = self._dynamic_sizer.compute_dynamic_size(
                    win_prob=Decimal("0.6"),
                    win_loss_ratio=Decimal("1.5"),
                    price=buy_price,
                    strategy_id=self._config.strategy_id,
                    strategy_used_capital=Decimal("0"),
                    edge_bps=float(net_edge * 10000),
                    regime=regime,
                    bid_depth_usd=bid_depth_usd,
                )
                if dynamic_size > Decimal("0"):
                    # CRITICAL FIX: cap at depth-based size to avoid exceeding verified liquidity
                    trade_size = min(dynamic_size, trade_size) if trade_size > Decimal("0") else dynamic_size
            except Exception as exc:
                logger.debug("DynamicSizer failed (using depth-based size): %s", exc)

        # Deduplication
        dedup_key = self._dedup_key(buy_exchange, sell_exchange, symbol)
        if self._is_duplicate(dedup_key):
            return None
        self._mark_emitted(dedup_key)

        gross_spread = sell_price - buy_price
        gross_spread_pct = gross_spread / buy_price

        signal = Signal(
            strategy_id=self._config.strategy_id,
            symbol=symbol,
            buy_exchange=buy_exchange,
            sell_exchange=sell_exchange,
            buy_price=buy_price,
            sell_price=sell_price,
            spread_pct=gross_spread_pct,
            confidence=float(min(net_edge * 100, Decimal("1"))) * ml_score,
            volume=trade_size,
            timestamp=datetime.now(timezone.utc),
            metadata={
                "net_profit": str(friction.net_profit),
                "net_edge_pct": str(net_edge * 100),
                "fee_total": str(friction.fee_buy + friction.fee_sell),
                "slippage_total": str(friction.slippage_buy + friction.slippage_sell),
            },
        )

        # Publish to Redis Streams
        if self._event_bus is not None:
            event = SignalEvent(signal=signal, source=self._config.strategy_id)
            try:
                await self._event_bus.publish(
                    self.SIGNAL_STREAM,
                    event.model_dump(mode="json"),
                )
            except Exception as exc:
                logger.error("Failed to publish signal to Redis: %s", exc)

        return signal
