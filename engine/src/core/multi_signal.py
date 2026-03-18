"""Multi-strategy signal producer.

Extends the base SignalGenerator to produce signals for ALL 8 strategy types:
  1. CrossExchange       — cross-exchange price discrepancy (existing)
  2. SpotFutures         — spot vs futures basis on same exchange
  3. FuturesFutures      — futures price diff across exchanges
  4. Triangular          — 3-pair cycle on single exchange
  5. FundingRate         — funding rate differential across exchanges
  6. StatisticalArb      — z-score based (uses cross-exchange signals)
  7. CexDex             — CEX vs DEX price (requires DEX adapter)
  8. LatencyArb          — latency-based (uses cross-exchange signals + latency data)

Strategies 1, 3, 6, 8 all consume cross-exchange signals (routed via StrategyManager).
Strategies 2, 4, 5 need dedicated signal producers (this module).
Strategy 7 (CexDex) needs a DEX adapter (not yet implemented).
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from src.core.events import SignalEvent
from src.core.models import Signal
from src.core.order_book import OrderBook

logger = logging.getLogger(__name__)


@dataclass
class MultiSignalConfig:
    """Configuration for multi-strategy signal production."""
    # Spot-Futures
    spot_futures_min_basis_bps: Decimal = Decimal("3")
    spot_futures_symbols: list[str] = field(default_factory=lambda: ["BTC/USDT"])
    futures_suffix: str = ":USDT"

    # Funding Rate
    funding_rate_poll_interval: float = 60.0  # seconds
    funding_rate_min_diff_bps: Decimal = Decimal("10")  # Must exceed round-trip friction (fees ~8bps)

    # Triangular
    triangular_paths: list[list[str]] = field(default_factory=lambda: [
        ["USDT", "BTC", "ETH"],
    ])
    triangular_min_profit_bps: Decimal = Decimal("3")

    # Latency
    latency_record_interval: float = 1.0  # how often to record latency samples

    # Trade sizing: fixed USD notional for all multi-strategy signals
    # volume = notional / price (e.g., $500 / $90,000 BTC = 0.0056 BTC)
    default_notional_usd: Decimal = Decimal("500")


class MultiStrategySignalProducer:
    """
    Produces signals for multiple strategy types from market data.

    Runs as a background task alongside the main SignalGenerator.
    Publishes signals to the same Redis Streams channel for StrategyManager routing.
    """

    SIGNAL_STREAM = "leviathan:signals"

    def __init__(
        self,
        event_bus: Any,
        config: MultiSignalConfig | None = None,
        latency_tracker: Any | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._config = config or MultiSignalConfig()
        self._latency_tracker = latency_tracker
        self._running = False

        # Caches for signal computation
        self._orderbooks: dict[str, dict[str, OrderBook]] = {}  # exchange -> symbol -> book
        self._funding_rates: dict[str, dict[str, float]] = {}   # exchange -> symbol -> rate
        self._last_signal: dict[str, float] = {}  # dedup
        self._exchange_timestamps: dict[str, float] = {}  # for latency tracking

    def _volume_from_price(self, price: Decimal) -> Decimal:
        """Compute trade volume from price using fixed USD notional.

        Returns notional / price, e.g. $500 / $90,000 = 0.00556 BTC.
        Ensures minimum volume of 0.0001 to avoid dust orders.
        """
        if price <= 0:
            return Decimal("0.0001")
        vol = self._config.default_notional_usd / price
        return max(vol, Decimal("0.0001"))

    def on_orderbook(
        self,
        exchange_id: str,
        symbol: str,
        book: OrderBook,
    ) -> None:
        """Record orderbook update and track latency."""
        if exchange_id not in self._orderbooks:
            self._orderbooks[exchange_id] = {}
        self._orderbooks[exchange_id][symbol] = book

        # Track latency for LatencyArb
        now = time.time()
        if self._latency_tracker is not None:
            prev = self._exchange_timestamps.get(exchange_id, now)
            latency_ms = (now - prev) * 1000
            if latency_ms > 0 and latency_ms < 10000:  # sanity check
                self._latency_tracker.record_latency(exchange_id, latency_ms)
        self._exchange_timestamps[exchange_id] = now

    async def on_orderbook_update(
        self,
        exchange_id: str,
        symbol: str,
        book: OrderBook,
    ) -> None:
        """Alias for on_orderbook(). Matches SignalGenerator's method name for symmetry."""
        self.on_orderbook(exchange_id, symbol, book)

    async def produce_spot_futures_signal(
        self,
        exchange_id: str,
        spot_symbol: str,
        futures_symbol: str,
        spot_price: Decimal,
        futures_price: Decimal,
        funding_rate: float = 0.0,
    ) -> Optional[Signal]:
        """Generate spot-futures basis signal if basis exceeds threshold."""
        if spot_price <= 0 or futures_price <= 0:
            return None

        basis = futures_price - spot_price
        basis_bps = (basis / spot_price) * Decimal("10000")

        if abs(basis_bps) < self._config.spot_futures_min_basis_bps:
            return None

        # Dedup
        key = f"sf:{exchange_id}:{spot_symbol}"
        if self._is_duplicate(key, cooldown=2.0):
            return None
        self._mark_emitted(key)

        signal = Signal(
            strategy_id="spot_futures_basis",
            symbol=spot_symbol,
            buy_exchange=exchange_id,
            sell_exchange=exchange_id,
            buy_price=min(spot_price, futures_price),
            sell_price=max(spot_price, futures_price),
            spread_pct=abs(basis) / spot_price,
            confidence=min(1.0, float(abs(basis_bps)) / 100.0),
            volume=self._volume_from_price(spot_price),
            timestamp=datetime.now(timezone.utc),
            metadata={
                "basis_bps": str(basis_bps),
                "spot_symbol": spot_symbol,
                "futures_symbol": futures_symbol,
                "funding_rate": str(funding_rate),
            },
        )
        await self._publish(signal)
        return signal

    async def produce_funding_rate_signal(
        self,
        symbol: str,
        high_rate_exchange: str,
        low_rate_exchange: str,
        high_rate: float,
        low_rate: float,
        price: Decimal,
    ) -> Optional[Signal]:
        """Generate funding rate arbitrage signal."""
        diff = high_rate - low_rate
        diff_bps = Decimal(str(diff)) * Decimal("10000")

        if diff_bps < self._config.funding_rate_min_diff_bps:
            return None

        key = f"fr:{symbol}:{high_rate_exchange}:{low_rate_exchange}"
        if self._is_duplicate(key, cooldown=30.0):
            return None
        self._mark_emitted(key)

        signal = Signal(
            strategy_id="funding_rate_arb",
            symbol=symbol,
            buy_exchange=low_rate_exchange,
            sell_exchange=high_rate_exchange,
            buy_price=price,
            sell_price=price,
            spread_pct=Decimal(str(diff)),
            confidence=min(1.0, float(diff_bps) / 50.0),
            volume=self._volume_from_price(price),
            timestamp=datetime.now(timezone.utc),
            metadata={
                "funding_rate_sell": str(high_rate),
                "funding_rate_buy": str(low_rate),
                "funding_diff_bps": str(diff_bps),
            },
        )
        await self._publish(signal)
        return signal

    async def produce_triangular_signal(
        self,
        exchange_id: str,
        path: list[str],
        pairs: list[str],
        sides: list[str],
        prices: list[Decimal],
        profit_pct: Decimal,
    ) -> Optional[Signal]:
        """Generate triangular arbitrage signal."""
        profit_bps = profit_pct * Decimal("10000")
        if profit_bps < self._config.triangular_min_profit_bps:
            return None

        key = f"tri:{exchange_id}:{'-'.join(path)}"
        if self._is_duplicate(key, cooldown=1.0):
            return None
        self._mark_emitted(key)

        signal = Signal(
            strategy_id="triangular",
            symbol=pairs[0],
            buy_exchange=exchange_id,
            sell_exchange=exchange_id,
            buy_price=prices[0],
            sell_price=prices[0] * (Decimal("1") + profit_pct),
            spread_pct=profit_pct,
            confidence=min(1.0, float(profit_bps) / 50.0),
            volume=self._volume_from_price(prices[0]),
            timestamp=datetime.now(timezone.utc),
            metadata={
                "path": path,
                "pairs": pairs,
                "sides": sides,
                "prices": [str(p) for p in prices],
                "exchange_id": exchange_id,
            },
        )
        await self._publish(signal)
        return signal

    async def produce_statistical_arb_signal(
        self,
        symbol: str,
        buy_exchange: str,
        sell_exchange: str,
        buy_price: Decimal,
        sell_price: Decimal,
        z_score: float,
        symbol2: Optional[str] = None,
    ) -> Optional[Signal]:
        """Generate statistical arbitrage signal based on z-score deviation.

        For cross-asset pairs (US-188), symbol2 holds the second asset symbol and
        buy_exchange == sell_exchange (same exchange, different symbols).
        """
        if buy_price <= 0 or sell_price <= 0:
            return None

        spread = sell_price - buy_price
        spread_pct = spread / buy_price if buy_price > 0 else Decimal("0")

        key = f"sa:{symbol}:{buy_exchange}:{sell_exchange}:{symbol2 or ''}"
        if self._is_duplicate(key, cooldown=30.0):
            return None
        self._mark_emitted(key)

        metadata: dict = {
            "z_score": str(z_score),
            "spread_pct": str(spread_pct),
        }
        if symbol2 is not None:
            metadata["symbol2"] = symbol2

        signal = Signal(
            strategy_id="statistical_arb_zscore",
            symbol=symbol,
            buy_exchange=buy_exchange,
            sell_exchange=sell_exchange,
            buy_price=buy_price,
            sell_price=sell_price,
            spread_pct=abs(spread_pct),
            confidence=min(1.0, abs(z_score) / 4.0),
            volume=self._volume_from_price(buy_price),
            timestamp=datetime.now(timezone.utc),
            metadata=metadata,
        )
        await self._publish(signal)
        return signal

    async def produce_latency_arb_signal(
        self,
        symbol: str,
        fast_exchange: str,
        slow_exchange: str,
        fast_price: Decimal,
        slow_price: Decimal,
        latency_diff_ms: float,
    ) -> Optional[Signal]:
        """Generate latency arbitrage signal when exchange update delay is significant."""
        if fast_price <= 0 or slow_price <= 0:
            return None

        spread = fast_price - slow_price
        spread_pct = abs(spread) / slow_price if slow_price > 0 else Decimal("0")

        key = f"la:{symbol}:{fast_exchange}:{slow_exchange}"
        if self._is_duplicate(key, cooldown=10.0):
            return None
        self._mark_emitted(key)

        # Buy on slow (stale price), sell on fast (fresh price) if fast > slow
        if fast_price > slow_price:
            buy_ex, sell_ex = slow_exchange, fast_exchange
            buy_p, sell_p = slow_price, fast_price
        else:
            buy_ex, sell_ex = fast_exchange, slow_exchange
            buy_p, sell_p = fast_price, slow_price

        signal = Signal(
            strategy_id="latency_arb",
            symbol=symbol,
            buy_exchange=buy_ex,
            sell_exchange=sell_ex,
            buy_price=buy_p,
            sell_price=sell_p,
            spread_pct=spread_pct,
            confidence=min(1.0, latency_diff_ms / 500.0),
            volume=self._volume_from_price(buy_p),
            timestamp=datetime.now(timezone.utc),
            metadata={
                "latency_diff_ms": str(latency_diff_ms),
                "fast_exchange": fast_exchange,
                "slow_exchange": slow_exchange,
            },
        )
        await self._publish(signal)
        return signal

    async def produce_futures_futures_signal(
        self,
        symbol: str,
        buy_exchange: str,
        sell_exchange: str,
        buy_price: Decimal,
        sell_price: Decimal,
    ) -> Optional[Signal]:
        """Generate futures-futures spread signal across exchanges."""
        if buy_price <= 0 or sell_price <= 0:
            return None

        spread = sell_price - buy_price
        spread_bps = (spread / buy_price) * Decimal("10000") if buy_price > 0 else Decimal("0")

        if abs(spread_bps) < Decimal("10"):
            return None

        key = f"ff:{symbol}:{buy_exchange}:{sell_exchange}"
        if self._is_duplicate(key, cooldown=2.0):
            return None
        self._mark_emitted(key)

        signal = Signal(
            strategy_id="futures_futures_spread",
            symbol=symbol,
            buy_exchange=buy_exchange,
            sell_exchange=sell_exchange,
            buy_price=buy_price,
            sell_price=sell_price,
            spread_pct=abs(spread) / buy_price if buy_price > 0 else Decimal("0"),
            confidence=min(1.0, float(abs(spread_bps)) / 100.0),
            volume=self._volume_from_price(buy_price),
            timestamp=datetime.now(timezone.utc),
            metadata={
                "spread_bps": str(spread_bps),
                "buy_futures_exchange": buy_exchange,
                "sell_futures_exchange": sell_exchange,
            },
        )
        await self._publish(signal)
        return signal

    def _is_duplicate(self, key: str, cooldown: float = 1.0) -> bool:
        last = self._last_signal.get(key)
        if last is None:
            return False
        return (time.time() - last) < cooldown

    def _mark_emitted(self, key: str) -> None:
        self._last_signal[key] = time.time()

    async def _publish(self, signal: Signal) -> None:
        """Publish signal to Redis Streams."""
        if self._event_bus is None:
            return
        event = SignalEvent(signal=signal, source=signal.strategy_id)
        try:
            await self._event_bus.publish(
                self.SIGNAL_STREAM,
                event.model_dump(mode="json"),
            )
        except Exception as exc:
            logger.error("Failed to publish multi-signal: %s", exc)


class PaperSignalSimulator:
    """
    Simulates market conditions that produce signals for ALL strategy types.
    Used in paper trading mode to test the full signal→strategy→execution pipeline.

    Injects synthetic market events:
      - Spot-futures basis spreads
      - Funding rate differentials
      - Triangular arbitrage opportunities
    """

    def __init__(
        self,
        producer: MultiStrategySignalProducer,
        exchanges: list[str],
        symbols: list[str],
        injection_rate: float = 0.05,
    ) -> None:
        self._producer = producer
        self._exchanges = exchanges
        self._symbols = symbols
        self._injection_rate = injection_rate
        self._running = False
        self._base_prices: dict[str, Decimal] = {}

    async def start(self) -> None:
        self._running = True
        # Initialize base prices
        for symbol in self._symbols:
            if "BTC" in symbol:
                self._base_prices[symbol] = Decimal("65000")
            elif "ETH" in symbol:
                self._base_prices[symbol] = Decimal("3500")
            elif "SOL" in symbol:
                self._base_prices[symbol] = Decimal("145")
            else:
                self._base_prices[symbol] = Decimal("100")

    async def stop(self) -> None:
        self._running = False

    async def tick(self) -> list[Signal]:
        """Generate one tick of synthetic signals. Returns signals produced."""
        if not self._running:
            return []

        signals: list[Signal] = []

        for symbol in self._symbols:
            base_price = self._base_prices.get(symbol, Decimal("100"))
            # Random walk the base price
            drift = Decimal(str(random.gauss(0, 0.0001)))
            self._base_prices[symbol] = base_price * (Decimal("1") + drift)

            # Spot-Futures signal (randomly inject basis spread)
            if random.random() < self._injection_rate and len(self._exchanges) >= 1:
                exchange = random.choice(self._exchanges)
                basis_bps = Decimal(str(random.uniform(15, 50)))
                futures_price = base_price * (Decimal("1") + basis_bps / Decimal("10000"))
                funding_rate = random.uniform(-0.001, 0.003)
                sig = await self._producer.produce_spot_futures_signal(
                    exchange_id=exchange,
                    spot_symbol=symbol,
                    futures_symbol=f"{symbol}:USDT",
                    spot_price=base_price,
                    futures_price=futures_price,
                    funding_rate=funding_rate,
                )
                if sig:
                    signals.append(sig)

            # Funding Rate signal
            if random.random() < self._injection_rate * 0.3 and len(self._exchanges) >= 2:
                ex_a, ex_b = random.sample(self._exchanges, 2)
                high_rate = random.uniform(0.001, 0.005)
                low_rate = random.uniform(-0.001, 0.0005)
                sig = await self._producer.produce_funding_rate_signal(
                    symbol=symbol,
                    high_rate_exchange=ex_a,
                    low_rate_exchange=ex_b,
                    high_rate=high_rate,
                    low_rate=low_rate,
                    price=base_price,
                )
                if sig:
                    signals.append(sig)

            # Triangular signal
            if random.random() < self._injection_rate * 0.2 and len(self._exchanges) >= 1:
                exchange = random.choice(self._exchanges)
                # Simulate USDT→BTC→ETH→USDT triangle
                btc_price = self._base_prices.get("BTC/USDT", Decimal("65000"))
                eth_price = self._base_prices.get("ETH/USDT", Decimal("3500"))
                eth_btc = eth_price / btc_price if btc_price > 0 else Decimal("0.054")

                # Inject small profit opportunity
                profit_pct = Decimal(str(random.uniform(0.001, 0.005)))
                sig = await self._producer.produce_triangular_signal(
                    exchange_id=exchange,
                    path=["USDT", "BTC", "ETH"],
                    pairs=["BTC/USDT", "ETH/BTC", "ETH/USDT"],
                    sides=["buy", "buy", "sell"],
                    prices=[btc_price, eth_btc, eth_price],
                    profit_pct=profit_pct,
                )
                if sig:
                    signals.append(sig)

        return signals
