"""Tests for MarketStats 24h rolling ADV aggregator — Path-B v2 Day 10."""
from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from src.core.market_stats import MarketStats, TradeEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ev(
    exchange: str,
    symbol: str,
    price: str,
    qty: str,
    ts_ms: int,
) -> TradeEvent:
    return TradeEvent(
        exchange=exchange,
        symbol=symbol,
        price=Decimal(price),
        qty=Decimal(qty),
        ts_ms=ts_ms,
    )


# ---------------------------------------------------------------------------
# 1. Empty tracker returns zero
# ---------------------------------------------------------------------------


def test_empty_returns_zero() -> None:
    ms = MarketStats()
    assert ms.get_adv_usd("binance", "BTC/USDT") == Decimal("0")


# ---------------------------------------------------------------------------
# 2. Cumulative volume after many trades
# ---------------------------------------------------------------------------


def test_cumulative_volume_usd() -> None:
    ms = MarketStats()
    now_ms = 1_700_000_000_000  # fixed anchor
    # 100 trades @ price=50000, qty=0.01 → each trade = $500, total = $50_000
    for i in range(100):
        asyncio.run(
            ms.on_trade(
                _ev("binance", "BTC/USDT", "50000", "0.01", now_ms - i * 1_000)
            )
        )
    total = ms.get_adv_usd("binance", "BTC/USDT")
    assert total == Decimal("50000.00")


# ---------------------------------------------------------------------------
# 3. Rolling window drops trades older than 24h
# ---------------------------------------------------------------------------


def test_rolling_window_evicts_old() -> None:
    ms = MarketStats()
    # Trade 1: 30h ago → outside 24h window
    # Trade 2: 1h ago → inside window
    old_ms = 1_700_000_000_000 - 30 * 3_600_000  # 30h ago
    recent_ms = 1_700_000_000_000 - 1 * 3_600_000  # 1h ago
    asyncio.run(ms.on_trade(_ev("binance", "ETH/USDT", "2000", "1.0", old_ms)))
    asyncio.run(ms.on_trade(_ev("binance", "ETH/USDT", "2000", "0.5", recent_ms)))
    # Force eviction using the more-recent trade as "now"
    # get_adv_usd runs sweep; older trade should be gone
    total = ms.get_adv_usd("binance", "ETH/USDT")
    # Only the 0.5 ETH @ 2000 = $1000 should remain
    assert total == Decimal("1000.00")


# ---------------------------------------------------------------------------
# 4. Per-symbol + per-exchange isolation
# ---------------------------------------------------------------------------


def test_pair_isolation() -> None:
    ms = MarketStats()
    now_ms = 1_700_000_000_000
    asyncio.run(ms.on_trade(_ev("binance", "BTC/USDT", "50000", "0.1", now_ms)))
    asyncio.run(ms.on_trade(_ev("bybit", "BTC/USDT", "50000", "0.2", now_ms)))
    asyncio.run(ms.on_trade(_ev("binance", "ETH/USDT", "2000", "1.0", now_ms)))

    assert ms.get_adv_usd("binance", "BTC/USDT") == Decimal("5000.0")
    assert ms.get_adv_usd("bybit", "BTC/USDT") == Decimal("10000.0")
    assert ms.get_adv_usd("binance", "ETH/USDT") == Decimal("2000.0")
    # Unknown pair returns zero
    assert ms.get_adv_usd("okx", "BTC/USDT") == Decimal("0")


# ---------------------------------------------------------------------------
# 5. Warmup <15min → is_warm False → signal.py uses proxy fallback
# ---------------------------------------------------------------------------


def test_warmup_gating() -> None:
    ms = MarketStats(start_ts_ms=1_700_000_000_000)
    # No trades yet → not warm regardless of start time
    assert ms.is_warm("binance", "BTC/USDT") is False

    # One trade inside the first 15min → still not warm because the *pair*
    # has only been observed for one instant.
    asyncio.run(
        ms.on_trade(_ev("binance", "BTC/USDT", "50000", "0.1", 1_700_000_060_000))
    )
    assert ms.is_warm("binance", "BTC/USDT") is False

    # First trade at t=0s, second at t=16min → span exceeds WARMUP_MS → warm.
    asyncio.run(
        ms.on_trade(
            _ev(
                "binance",
                "BTC/USDT",
                "50000",
                "0.1",
                1_700_000_000_000 + 16 * 60_000,
            )
        )
    )
    assert ms.is_warm("binance", "BTC/USDT") is True


# ---------------------------------------------------------------------------
# 6. Thread-safe concurrent inserts produce deterministic aggregate
# ---------------------------------------------------------------------------


def test_concurrent_inserts_deterministic() -> None:
    ms = MarketStats()
    now_ms = 1_700_000_000_000

    async def run() -> None:
        tasks = [
            ms.on_trade(
                _ev("binance", "XRP/USDT", "0.50", "1000", now_ms - i * 1_000)
            )
            for i in range(40)
        ]
        await asyncio.gather(*tasks)

    asyncio.run(run())
    # 40 trades × 1000 XRP × $0.50 = $20_000
    assert ms.get_adv_usd("binance", "XRP/USDT") == Decimal("20000.00")


# ---------------------------------------------------------------------------
# Extra: stats_summary returns structured dict
# ---------------------------------------------------------------------------


def test_stats_summary_structure() -> None:
    ms = MarketStats()
    now_ms = 1_700_000_000_000
    asyncio.run(ms.on_trade(_ev("binance", "BTC/USDT", "50000", "0.1", now_ms)))
    summary = ms.stats_summary()
    assert isinstance(summary, dict)
    assert "pairs" in summary
    assert ("binance", "BTC/USDT") in summary["pairs"] or (
        "binance:BTC/USDT" in summary["pairs"]
    )
