"""Signal generator — friction-aware pipeline (Amendment 3A).

Pipeline:
    OrderBook → Price Hub → Raw Signal → Friction Filter (all costs)
    → Max Rollback Cost Gate → Net Signal → Dedup → Redis Streams
"""
from __future__ import annotations

import logging
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
    default_adv: Decimal = Decimal("1000")
    default_sigma: Decimal = Decimal("0.001")
    min_price_usd: Decimal = Decimal("0.10")
    max_book_age_seconds: float = 30.0  # reject orderbooks not updated within this window
    min_delta_update_count: int = 3    # STALE_MIN_DELTA_UPDATES: min deltas since snapshot for delta exchanges


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
    ) -> None:
        self._hub = price_hub
        self._calc = cost_calculator
        self._config = config or SignalConfig()
        self._event_bus = event_bus
        self._stale_detector = stale_detector
        self._last_signal: dict[str, float] = {}  # dedup_key -> last emit timestamp
        self._regime_detector = regime_detector  # US-084
        self._ml_scorer = ml_scorer  # US-094

    def _dedup_key(self, buy_ex: str, sell_ex: str, symbol: str) -> str:
        return f"{symbol}:{buy_ex}:{sell_ex}"

    def _is_duplicate(self, key: str) -> bool:
        last = self._last_signal.get(key)
        if last is None:
            return False
        return (time.time() - last) < self._config.cooldown_seconds

    def _mark_emitted(self, key: str) -> None:
        self._last_signal[key] = time.time()

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

        # Raw spread gate
        if sell_price <= buy_price:
            return None

        # Min price gate — filter out penny coins (e.g. QKC at $0.003)
        if buy_price < self._config.min_price_usd:
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
                adv=self._config.default_adv,
                sigma=self._config.default_sigma,
                transfer_coin=transfer_coin,
            )
        except Exception as exc:
            logger.warning("Friction calculation failed for %s: %s", symbol, exc)
            return None

        notional = buy_price * trade_size
        net_edge = friction.net_profit / notional if notional > 0 else Decimal("0")

        # Min edge gate — US-084: regime-adaptive threshold
        effective_min_edge = self._config.min_edge
        if self._regime_detector is not None:
            from src.tuning.regime_detector import REGIME_MIN_EDGE
            regime = self._regime_detector.current_regime
            effective_min_edge = REGIME_MIN_EDGE.get(regime, self._config.min_edge)
        if net_edge < effective_min_edge:
            return None

        # Max rollback cost gate
        if friction.rollback_cost_expected > self._config.max_rollback_cost_usd:
            return None

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
            confidence=float(min(net_edge * 100, Decimal("1"))),
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
