"""WS-D2 unit tests — pre-execution toxicity filter.

Exercises the three rejection paths (empty book, imbalance, depth volatility)
plus the happy path where the filter should return ``None``.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.risk.toxicity_filter import ToxicityConfig, ToxicityFilter


def _make_book(
    bids: list[tuple[float, float]] | None = None,
    asks: list[tuple[float, float]] | None = None,
) -> SimpleNamespace:
    bid_map: dict[Decimal, Decimal] = {}
    ask_map: dict[Decimal, Decimal] = {}
    for price, qty in bids or []:
        bid_map[Decimal(str(price))] = Decimal(str(qty))
    for price, qty in asks or []:
        ask_map[Decimal(str(price))] = Decimal(str(qty))
    return SimpleNamespace(bids=bid_map, asks=ask_map, exchange="binance")


class TestToxicityFilter:
    def test_empty_book_rejects(self) -> None:
        """No bids or asks → empty_book rejection."""
        tf = ToxicityFilter()
        reason = tf.check(
            book=_make_book(bids=[], asks=[]),
            exchange="binance", symbol="BTC/USDT", strategy_id="ff",
        )
        assert reason == "empty_book"

    def test_imbalance_rejects_when_over_threshold(self) -> None:
        """Bids dominate 10:1 → imbalance 0.818 > 0.7 → reject."""
        tf = ToxicityFilter()
        book = _make_book(
            bids=[(100, 100)],
            asks=[(101, 10)],
        )
        reason = tf.check(
            book=book, exchange="binance", symbol="BTC/USDT", strategy_id="ff",
        )
        assert reason == "imbalance"

    def test_balanced_book_passes(self) -> None:
        """50/50 book → no rejection on first check."""
        tf = ToxicityFilter()
        book = _make_book(
            bids=[(100, 50), (99.5, 30), (99, 20)],
            asks=[(101, 50), (101.5, 30), (102, 20)],
        )
        reason = tf.check(
            book=book, exchange="binance", symbol="BTC/USDT", strategy_id="ff",
        )
        assert reason is None

    def test_depth_volatility_rejects_when_std_exceeds_multiplier(self) -> None:
        """Feed spiky depth history → std > 3× median → depth_volatility."""
        cfg = ToxicityConfig(min_depth_samples=5, depth_volatility_multiplier=1.0)
        tf = ToxicityFilter(cfg)
        # Seed 4 samples at depth ~2 so imbalance stays 0 (tf.check records depth).
        calm = _make_book(bids=[(100, 1)], asks=[(101, 1)])
        for _ in range(4):
            tf.check(
                book=calm, exchange="binance", symbol="BTC/USDT", strategy_id="ff",
            )
        # 5th sample is a huge spike — std across all 5 should exceed multiplier.
        spiky = _make_book(bids=[(100, 500)], asks=[(101, 500)])
        reason = tf.check(
            book=spiky, exchange="binance", symbol="BTC/USDT", strategy_id="ff",
        )
        assert reason == "depth_volatility"

    def test_cold_start_skips_depth_volatility(self) -> None:
        """Less than min_depth_samples → depth_volatility gate is dormant."""
        cfg = ToxicityConfig(min_depth_samples=100)
        tf = ToxicityFilter(cfg)
        book = _make_book(bids=[(100, 50)], asks=[(101, 50)])
        # Even huge spikes wouldn't trigger until we reach min_depth_samples
        for _ in range(5):
            reason = tf.check(
                book=book, exchange="binance", symbol="BTC/USDT", strategy_id="ff",
            )
            assert reason is None


if __name__ == "__main__":
    pytest.main([__file__, "-x", "--tb=short", "--no-cov"])
