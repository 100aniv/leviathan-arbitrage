"""Signal generator — friction-aware pipeline (Amendment 3A).

Pipeline:
    OrderBook → Price Hub → Raw Signal → Friction Filter (all costs)
    → Max Rollback Cost Gate → Net Signal → Dedup → Redis Streams
"""
from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)


@dataclass
class SignalConfig:
    strategy_id: str = "cross_exchange_spot"
    min_edge: Decimal = Decimal("0.0001")     # minimum net profit as fraction of notional (1 bps)
    max_spread_pct: Decimal = Decimal("0.05") # max gross spread as fraction (5%) — reject data anomalies
    cooldown_seconds: float = 1.0              # dedup suppression window
    max_rollback_cost_usd: Decimal = Decimal("50")
    default_adv: Decimal = Decimal("1000")
    default_sigma: Decimal = Decimal("0.001")


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
    ) -> None:
        self._hub = price_hub
        self._calc = cost_calculator
        self._config = config or SignalConfig()
        self._event_bus = event_bus
        self._last_signal: dict[str, float] = {}  # dedup_key -> last emit timestamp

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
        trade_size: Decimal = Decimal("1"),
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

        # Friction filter
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
            )
        except Exception as exc:
            logger.warning("Friction calculation failed for %s: %s", symbol, exc)
            return None

        notional = buy_price * trade_size
        net_edge = friction.net_profit / notional if notional > 0 else Decimal("0")

        # Min edge gate
        if net_edge < self._config.min_edge:
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
