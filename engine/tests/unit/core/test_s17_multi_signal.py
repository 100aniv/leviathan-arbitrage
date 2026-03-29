"""Tests for MultiSignalProducer funding normalization — US-269."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.multi_signal import MultiStrategySignalProducer, SETTLEMENT_HOURS


def _make_producer() -> MultiStrategySignalProducer:
    """Return a minimal MultiStrategySignalProducer with mocked publish."""
    producer = MultiStrategySignalProducer.__new__(MultiStrategySignalProducer)
    # Minimal init — replicate only the fields used by update_funding_rate
    producer._funding_rates: dict = {}
    producer._config = MagicMock()
    producer._config.funding_scanner_exchanges = ["binance", "bybit", "okx"]
    producer._config.funding_rate_min_diff_bps = Decimal("5")
    producer._config.enable_multi_funding_scanner = True
    producer._last_signal = {}
    producer._publish = AsyncMock(return_value=None)
    producer._volume_from_price = MagicMock(return_value=Decimal("0.1"))
    return producer


def test_settlement_normalization():
    """8H settlement → rate_8h equals raw rate (factor = 8/8 = 1)."""
    producer = _make_producer()
    exchange = "binance"
    # Confirm binance uses 8H settlement (default)
    assert SETTLEMENT_HOURS.get(exchange, 8.0) == 8.0

    raw_rate = 0.001
    producer.update_funding_rate(exchange, "BTC/USDT", raw_rate)

    stored = producer._funding_rates[exchange]["BTC/USDT"]
    assert abs(stored - raw_rate) < 1e-12, f"Expected {raw_rate}, got {stored}"


def test_settlement_normalization_non_8h():
    """4H settlement exchange → rate_8h = rate * (8/4) = rate * 2."""
    producer = _make_producer()
    # Inject a synthetic 4H exchange into SETTLEMENT_HOURS for this test
    import src.core.multi_signal as ms_module
    original = ms_module.SETTLEMENT_HOURS.copy()
    try:
        ms_module.SETTLEMENT_HOURS["test_4h_exchange"] = 4.0
        raw_rate = 0.001
        producer.update_funding_rate("test_4h_exchange", "BTC/USDT", raw_rate)
        stored = producer._funding_rates["test_4h_exchange"]["BTC/USDT"]
        expected = raw_rate * (8.0 / 4.0)
        assert abs(stored - expected) < 1e-12
    finally:
        ms_module.SETTLEMENT_HOURS.clear()
        ms_module.SETTLEMENT_HOURS.update(original)


@pytest.mark.asyncio
async def test_multi_funding_max_min_pair():
    """3 exchanges with different rates → max and min exchange pair selected."""
    producer = _make_producer()
    producer._config.funding_scanner_exchanges = ["binance", "bybit", "okx"]

    # binance=high, okx=low, bybit=mid
    producer._funding_rates = {
        "binance": {"BTC/USDT": 0.005},
        "bybit": {"BTC/USDT": 0.002},
        "okx": {"BTC/USDT": -0.001},
    }

    captured_calls: list[dict] = []

    async def fake_produce_funding_signal(symbol, high_rate_exchange, low_rate_exchange, high_rate, low_rate, price):
        captured_calls.append({
            "high": high_rate_exchange,
            "low": low_rate_exchange,
        })
        return None

    producer.produce_funding_rate_signal = fake_produce_funding_signal

    await producer.produce_multi_funding_signal("BTC/USDT", Decimal("50000"))

    assert len(captured_calls) == 1
    assert captured_calls[0]["high"] == "binance"
    assert captured_calls[0]["low"] == "okx"


@pytest.mark.asyncio
async def test_multi_funding_insufficient_exchanges():
    """Only 1 exchange with rate data → cannot form a pair → returns None."""
    producer = _make_producer()
    producer._config.funding_scanner_exchanges = ["binance", "bybit", "okx"]

    # Only binance has data
    producer._funding_rates = {
        "binance": {"BTC/USDT": 0.003},
    }

    result = await producer.produce_multi_funding_signal("BTC/USDT", Decimal("50000"))
    assert result is None
