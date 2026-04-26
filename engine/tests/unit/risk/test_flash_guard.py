"""Tests for FlashGuard — rapid price movement detection."""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from src.risk.flash_guard import FlashGuard, FlashEvent


class TestFlashGuardBasic:
    """Single-exchange tests use min_exchanges_for_trigger=1 (legacy behavior).
    Cross-exchange confirmation tests live in TestFlashGuardCrossExchange below.
    """

    def test_no_trigger_on_small_move(self) -> None:
        fg = FlashGuard(threshold_pct=3.0, window_seconds=300, min_exchanges_for_trigger=1)
        fg.record_price("BTC/USDT", "binance", 50000)
        triggered = fg.record_price("BTC/USDT", "binance", 50500)  # 1%
        assert not triggered
        assert not fg.is_triggered

    def test_trigger_on_large_move(self) -> None:
        fg = FlashGuard(threshold_pct=3.0, window_seconds=300, min_exchanges_for_trigger=1)
        fg.record_price("BTC/USDT", "binance", 50000)
        triggered = fg.record_price("BTC/USDT", "binance", 52000)  # 4%
        assert triggered
        assert fg.is_triggered
        assert fg.last_event is not None
        assert fg.last_event.symbol == "BTC/USDT"
        assert fg.last_event.price_change_pct >= 3.0

    def test_check_allowed_when_not_triggered(self) -> None:
        fg = FlashGuard()
        allowed, reason = fg.check_allowed()
        assert allowed
        assert reason == ""

    def test_check_blocked_when_triggered(self) -> None:
        fg = FlashGuard(threshold_pct=3.0, window_seconds=300, min_exchanges_for_trigger=1)
        fg.record_price("BTC/USDT", "binance", 50000)
        fg.record_price("BTC/USDT", "binance", 53000)  # 6%
        allowed, reason = fg.check_allowed()
        assert not allowed
        assert "Flash Guard active" in reason

    def test_auto_release_after_cooldown(self) -> None:
        fg = FlashGuard(threshold_pct=3.0, window_seconds=300, cooldown_seconds=1, min_exchanges_for_trigger=1)
        fg.record_price("BTC/USDT", "binance", 50000)
        fg.record_price("BTC/USDT", "binance", 53000)
        assert fg.is_triggered
        # Simulate cooldown elapsed
        fg._trigger_time = time.monotonic() - 2
        assert not fg.is_triggered

    def test_manual_reset(self) -> None:
        fg = FlashGuard(threshold_pct=3.0, window_seconds=300, min_exchanges_for_trigger=1)
        fg.record_price("BTC/USDT", "binance", 50000)
        fg.record_price("BTC/USDT", "binance", 53000)
        assert fg.is_triggered
        fg.reset()
        assert not fg.is_triggered

    def test_different_symbols_independent(self) -> None:
        fg = FlashGuard(threshold_pct=3.0, window_seconds=300, min_exchanges_for_trigger=1)
        fg.record_price("BTC/USDT", "binance", 50000)
        fg.record_price("ETH/USDT", "binance", 3000)
        triggered = fg.record_price("ETH/USDT", "binance", 3200)  # 6.7%
        assert triggered
        # BTC didn't trigger independently
        assert fg.last_event.symbol == "ETH/USDT"


class TestFlashGuardCrossExchange:
    """Cross-exchange confirmation gate (2026-04-26):
    Single-exchange flash (e.g. Upbit BTC -8% while Binance/OKX unchanged) is
    treated as local stale data. Halt requires 2+ exchanges confirming flash
    for the same symbol within _CROSS_EXCHANGE_WINDOW_S.
    """

    def test_single_exchange_does_not_trigger(self) -> None:
        """Default min_exchanges_for_trigger=2 — single exchange is suppressed."""
        fg = FlashGuard(threshold_pct=3.0, window_seconds=300)  # default min=2
        fg.record_price("BTC/USDT", "upbit", 50000)
        triggered = fg.record_price("BTC/USDT", "upbit", 53000)  # 6% solo
        assert not triggered
        assert not fg.is_triggered
        assert fg._suppressed_count == 1

    def test_two_exchanges_confirm_trigger(self) -> None:
        """When 2 exchanges flash within cross-exchange window, halt triggers."""
        fg = FlashGuard(threshold_pct=3.0, window_seconds=300)  # default min=2
        # Exchange A: 6% move
        fg.record_price("BTC/USDT", "binance", 50000)
        triggered_a = fg.record_price("BTC/USDT", "binance", 53000)
        assert not triggered_a  # only 1 exchange so far
        # Exchange B: also 6% move within cross-exchange window
        fg.record_price("BTC/USDT", "okx", 50000)
        triggered_b = fg.record_price("BTC/USDT", "okx", 53000)
        assert triggered_b  # confirmed by 2 exchanges
        assert fg.is_triggered

    def test_suppressed_count_increments_on_solo(self) -> None:
        fg = FlashGuard(threshold_pct=3.0, window_seconds=300)
        fg.record_price("BTC/USDT", "upbit", 50000)
        fg.record_price("BTC/USDT", "upbit", 53000)  # solo
        assert fg._suppressed_count == 1
        fg.record_price("ETH/USDT", "coinone", 3000)
        fg.record_price("ETH/USDT", "coinone", 3200)  # solo (different symbol)
        assert fg._suppressed_count == 2

    def test_window_pruning(self) -> None:
        fg = FlashGuard(threshold_pct=3.0, window_seconds=5)
        # Record old price
        fg.record_price("BTC/USDT", "binance", 50000)
        # Simulate time passing beyond window
        key = ("BTC/USDT", "binance")
        fg._price_history[key][0] = (time.monotonic() - 10, 50000)
        # New price after window should not compare against pruned old price
        triggered = fg.record_price("BTC/USDT", "binance", 53000)
        assert not triggered  # old entry pruned, no comparison baseline

    def test_event_history_bounded(self) -> None:
        fg = FlashGuard(threshold_pct=1.0, window_seconds=300)
        for i in range(150):
            fg.record_price("BTC/USDT", "binance", 50000 + i * 1000)
        assert len(fg._events) <= 100  # maxlen=100
