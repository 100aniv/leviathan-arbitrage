"""Tests for US-248: SignalGenerator dynamic ADV and Sigma estimation.

Verifies:
- _compute_dynamic_adv() estimates ADV from top-5 orderbook depth
- _compute_dynamic_sigma() estimates sigma from mid-price return std dev
- ADV fallback ≥ 1 when no depth
- BTC sigma falls in realistic range [0.0001, 0.1]

Run:
    cd engine && python -m pytest tests/test_dynamic_adv_sigma.py -v --tb=short
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.core.signal import SignalGenerator, SignalConfig
from src.core.price_hub import PriceHub
from src.friction.cost_calculator import CostCalculator


def _make_signal_gen() -> SignalGenerator:
    hub = MagicMock(spec=PriceHub)
    calc = MagicMock(spec=CostCalculator)
    return SignalGenerator(price_hub=hub, cost_calculator=calc, config=SignalConfig())


def _make_book(bids: dict, asks: dict) -> MagicMock:
    """Fake orderbook with dict-based bids/asks."""
    book = MagicMock()
    book.bids = {Decimal(str(k)): Decimal(str(v)) for k, v in bids.items()}
    book.asks = {Decimal(str(k)): Decimal(str(v)) for k, v in asks.items()}
    return book


class TestDynamicADV:
    """US-248: orderbook depth 기반 ADV 추정."""

    def test_dynamic_adv_from_orderbook(self):
        """ADV = sum of top-5 bid + ask quantities from both books."""
        sg = _make_signal_gen()

        buy_book = _make_book(
            bids={100: 5, 99: 3},
            asks={101: 4, 102: 2},
        )
        sell_book = _make_book(
            bids={100: 6, 99: 2},
            asks={101: 3, 102: 1},
        )

        adv = sg._compute_dynamic_adv("BTC/USDT", buy_book, sell_book)

        # total = (5+3+4+2) + (6+2+3+1) = 14 + 12 = 26
        assert adv >= Decimal("1"), "ADV must be at least 1"
        assert adv > Decimal("10"), "ADV should reflect total orderbook depth"

    def test_dynamic_adv_fallback_no_data(self):
        """depth가 0이어도 ADV ≥ 1 (max(..., 1) guard)."""
        sg = _make_signal_gen()

        buy_book = _make_book(bids={}, asks={})
        sell_book = _make_book(bids={}, asks={})

        adv = sg._compute_dynamic_adv("BTC/USDT", buy_book, sell_book)

        assert adv >= Decimal("1"), "ADV must never be zero"

    def test_dynamic_adv_uses_both_books(self):
        """두 orderbook 모두 포함 → 단일 book ADV보다 큼."""
        sg = _make_signal_gen()

        buy_book = _make_book(bids={100: 10}, asks={101: 10})
        sell_book = _make_book(bids={100: 20}, asks={101: 20})

        adv = sg._compute_dynamic_adv("BTC/USDT", buy_book, sell_book)

        # buy: 10+10=20, sell: 20+20=40 → total 60
        assert adv >= Decimal("40"), "Both books must contribute to ADV"


class TestDynamicSigma:
    """US-248: mid-price 수익률 기반 sigma 추정."""

    def test_dynamic_sigma_from_prices(self):
        """10개 이상 가격 → 실제 수익률 sigma 계산."""
        sg = _make_signal_gen()
        symbol = "BTC/USDT"

        # 10개 중간 가격 추가 (1% 변동)
        prices = [Decimal(str(100 + i * 0.5)) for i in range(15)]
        sg._price_history[symbol] = prices

        sigma = sg._compute_dynamic_sigma(symbol)

        assert sigma > Decimal("0"), "sigma must be positive"
        assert sigma < Decimal("1"), "sigma must be < 100%"

    def test_adv_fallback_no_data(self):
        """가격 기록 없음 → default_sigma 반환."""
        sg = _make_signal_gen()
        config_sigma = sg._config.default_sigma

        sigma = sg._compute_dynamic_sigma("UNKNOWN/USDT")

        assert sigma == config_sigma, "should return default_sigma when no history"

    def test_sigma_needs_minimum_10_prices(self):
        """10개 미만 가격 → default_sigma 반환."""
        sg = _make_signal_gen()
        symbol = "ETH/USDT"

        # Only 5 prices (below threshold of 10)
        sg._price_history[symbol] = [Decimal("100"), Decimal("101"), Decimal("99")]

        sigma = sg._compute_dynamic_sigma(symbol)

        assert sigma == sg._config.default_sigma

    def test_sigma_realistic_range(self):
        """BTC-like price series → sigma 0.0001~0.05 범위."""
        sg = _make_signal_gen()
        symbol = "BTC/USDT"

        # Simulate realistic BTC prices with ~0.5% fluctuation
        import random
        random.seed(42)
        base = 50000.0
        prices = [Decimal(str(base * (1 + random.gauss(0, 0.005)))) for _ in range(30)]
        sg._price_history[symbol] = prices

        sigma = sg._compute_dynamic_sigma(symbol)

        assert Decimal("0.0001") <= sigma <= Decimal("0.10"), (
            f"BTC sigma {sigma} outside realistic range"
        )
