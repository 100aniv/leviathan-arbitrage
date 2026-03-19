"""Tests for US-254: All 6 strategies blocked in CRISIS regime via SignalGenerator.

Verifies:
- CRISIS regime → SignalGenerator.on_orderbook_update() returns None for 6 strategy_ids
- Each strategy type is parametrized independently

Run:
    cd engine && python -m pytest tests/test_regime_all_strategies.py -v --tb=short
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, AsyncMock
import time

from src.core.price_hub import BestPrice

import pytest

from src.core.signal import SignalGenerator, SignalConfig
from src.core.price_hub import PriceHub
from src.core.order_book import OrderBook
from src.friction.cost_calculator import CostCalculator
from src.tuning.regime_detector import MarketRegime


STRATEGY_IDS = [
    "cross_exchange",
    "spot_futures",
    "futures_futures",
    "triangular",
    "funding_rate",
    "statistical_arb",
]


def _make_crisis_regime() -> MagicMock:
    mock = MagicMock()
    mock.current_regime = MarketRegime.CRISIS
    return mock


def _make_signal_gen(strategy_id: str) -> SignalGenerator:
    from src.friction.cost_calculator import FrictionCost

    config = SignalConfig(
        strategy_id=strategy_id,
        min_edge=Decimal("0.0001"),
    )
    hub = MagicMock(spec=PriceHub)
    calc = MagicMock(spec=CostCalculator)

    # Return a FrictionCost with small net_profit so net_edge < CRISIS threshold (15 bps)
    # CRISIS min_edge = 0.0015 = 15 bps; notional = 50000 → max allowed net_profit = 75
    # We use gross_spread=2 → net_profit ≈ 1 → net_edge ≈ 0.00002 < 0.0015 → BLOCKED
    mock_friction = FrictionCost(
        fee_buy=Decimal("0.10"),
        fee_sell=Decimal("0.10"),
        slippage_buy=Decimal("0.05"),
        slippage_sell=Decimal("0.05"),
        network_cost=Decimal("0.50"),
        funding_cost=Decimal("0"),
        opportunity_cost=Decimal("0"),
        rollback_cost_expected=Decimal("0.25"),
        gross_spread=Decimal("2"),  # small spread → net_edge below CRISIS 15bps threshold
    )
    calc.calculate.return_value = mock_friction

    return SignalGenerator(
        price_hub=hub,
        cost_calculator=calc,
        config=config,
        regime_detector=_make_crisis_regime(),
    )


def _make_orderbook(symbol: str, exchange: str, bid: float, ask: float) -> OrderBook:
    book = OrderBook(symbol=symbol, exchange=exchange)
    book.bids = {Decimal(str(bid)): Decimal("10")}
    book.asks = {Decimal(str(ask)): Decimal("10")}
    book.last_update_time = time.monotonic()
    book.update_count = 5
    return book


class TestRegimeCrisisAllStrategies:
    """US-254: 6개 전략 모두 CRISIS regime에서 차단."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("strategy_id", STRATEGY_IDS)
    async def test_crisis_blocks_signal_for_strategy(self, strategy_id: str):
        """CRISIS regime → on_orderbook_update returns None for {strategy_id}."""
        sg = _make_signal_gen(strategy_id)

        # Setup hub to return valid BestPrice from two different exchanges
        best_bid_price = BestPrice(price=Decimal("50100"), exchange="okx", qty=Decimal("10"))  # type: ignore[call-arg]
        best_ask_price = BestPrice(price=Decimal("50000"), exchange="binance", qty=Decimal("10"))  # type: ignore[call-arg]
        sg._hub.best_bid.return_value = best_bid_price
        sg._hub.best_ask.return_value = best_ask_price

        buy_book = MagicMock()
        buy_book.bids = {Decimal("50000"): Decimal("10")}
        buy_book.asks = {Decimal("50000.1"): Decimal("10")}
        buy_book.best_ask.return_value = Decimal("50000.1")
        buy_book.best_bid.return_value = Decimal("50000")
        buy_book.exchange = "binance"
        buy_book.symbol = "BTC/USDT"
        buy_book.last_update_time = time.monotonic()
        buy_book.update_count = 5
        buy_book.volume_24h_usd = None

        sell_book = MagicMock()
        sell_book.bids = {Decimal("50100"): Decimal("10")}
        sell_book.asks = {Decimal("50100.1"): Decimal("10")}
        sell_book.best_bid.return_value = Decimal("50100")
        sell_book.best_ask.return_value = Decimal("50100.1")
        sell_book.exchange = "okx"
        sell_book.symbol = "BTC/USDT"
        sell_book.last_update_time = time.monotonic()
        sell_book.update_count = 5
        sell_book.volume_24h_usd = None

        books = {
            "binance": buy_book,
            "okx": sell_book,
        }

        result = await sg.on_orderbook_update(buy_book, books)

        # CRISIS regime → signal must be None
        assert result is None, (
            f"Strategy {strategy_id!r} should be blocked in CRISIS regime, got {result}"
        )
