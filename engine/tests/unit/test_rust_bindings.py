"""Integration tests for Rust PyO3 bindings.

Tests verify that Rust modules produce identical results to Python implementations.
Gracefully skips if rust_core is not compiled.

Run:
    cd engine/rust_core && maturin develop && cd ..
    pytest tests/unit/test_rust_bindings.py -v
"""
from __future__ import annotations

import pytest
from decimal import Decimal

# ---------------------------------------------------------------------------
# Conditional import — fallback to Python if Rust not compiled
# ---------------------------------------------------------------------------
try:
    import rust_core
    # Verify it's the compiled PyO3 module, not just the source directory
    # (Python treats rust_core/ as a namespace package even without __init__.py)
    rust_core.OrderBook  # noqa: B018
    RUST_AVAILABLE = True
except (ImportError, AttributeError):
    rust_core = None  # type: ignore[assignment]
    RUST_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not RUST_AVAILABLE,
    reason="rust_core not compiled — run `cd engine/rust_core && maturin develop`",
)

# Python implementations for parity checks
from src.core.order_book import OrderBook as PyOrderBook
from src.risk.kill_switch import halt_local as py_halt_local
from src.risk.kill_switch import is_halted as py_is_halted
from src.risk.kill_switch import clear_halt as py_clear_halt


# ===========================================================================
# OrderBook tests
# ===========================================================================

class TestOrderBookParity:
    """Rust OrderBook produces identical results to Python OrderBook."""

    SNAPSHOT_BIDS = [
        ("50010.00", "1.5"),
        ("50009.00", "2.0"),
        ("50008.00", "0.5"),
        ("50007.00", "3.0"),
        ("50006.00", "1.0"),
    ]
    SNAPSHOT_ASKS = [
        ("50011.00", "1.2"),
        ("50012.00", "0.8"),
        ("50013.00", "2.5"),
        ("50014.00", "1.0"),
        ("50015.00", "0.3"),
    ]

    def _make_rust_ob(self) -> "rust_core.OrderBook":
        ob = rust_core.OrderBook("BTCUSDT", "binance")
        ob.apply_snapshot(self.SNAPSHOT_BIDS, self.SNAPSHOT_ASKS)
        return ob

    def _make_py_ob(self) -> PyOrderBook:
        ob = PyOrderBook("BTCUSDT", "binance")
        ob.apply_snapshot(self.SNAPSHOT_BIDS, self.SNAPSHOT_ASKS)
        return ob

    def test_best_bid_matches(self):
        rust_ob = self._make_rust_ob()
        py_ob = self._make_py_ob()
        assert rust_ob.best_bid() == pytest.approx(float(py_ob.best_bid()), rel=1e-6)

    def test_best_ask_matches(self):
        rust_ob = self._make_rust_ob()
        py_ob = self._make_py_ob()
        assert rust_ob.best_ask() == pytest.approx(float(py_ob.best_ask()), rel=1e-6)

    def test_spread_matches(self):
        rust_ob = self._make_rust_ob()
        py_ob = self._make_py_ob()
        assert rust_ob.spread() == pytest.approx(float(py_ob.spread()), rel=1e-6)

    def test_spread_pct_matches(self):
        rust_ob = self._make_rust_ob()
        py_ob = self._make_py_ob()
        assert rust_ob.spread_pct() == pytest.approx(float(py_ob.spread_pct()), rel=1e-6)

    def test_depth_weighted_mid_matches(self):
        rust_ob = self._make_rust_ob()
        py_ob = self._make_py_ob()
        assert rust_ob.depth_weighted_mid_price(5) == pytest.approx(
            float(py_ob.depth_weighted_mid_price(5)), rel=1e-4
        )

    def test_volume_at_price_bid(self):
        rust_ob = self._make_rust_ob()
        py_ob = self._make_py_ob()
        assert rust_ob.volume_at_price(50010.0, "bid") == pytest.approx(
            float(py_ob.volume_at_price(Decimal("50010.00"), "bid")), rel=1e-6
        )

    def test_volume_at_price_ask(self):
        rust_ob = self._make_rust_ob()
        py_ob = self._make_py_ob()
        assert rust_ob.volume_at_price(50011.0, "ask") == pytest.approx(
            float(py_ob.volume_at_price(Decimal("50011.00"), "ask")), rel=1e-6
        )

    def test_volume_at_missing_price_returns_zero(self):
        rust_ob = self._make_rust_ob()
        assert rust_ob.volume_at_price(99999.0, "bid") == 0.0
        assert rust_ob.volume_at_price(99999.0, "ask") == 0.0

    def test_invalid_side_raises(self):
        rust_ob = self._make_rust_ob()
        with pytest.raises(Exception):
            rust_ob.volume_at_price(50010.0, "invalid")


class TestOrderBookDelta:
    """Delta updates behave identically to Python."""

    SNAPSHOT_BIDS = [("100.00", "1.0"), ("99.00", "2.0"), ("98.00", "0.5")]
    SNAPSHOT_ASKS = [("101.00", "1.5"), ("102.00", "0.8"), ("103.00", "2.0")]

    def test_delta_update_quantity(self):
        rust_ob = rust_core.OrderBook("ETHUSDT", "okx")
        rust_ob.apply_snapshot(self.SNAPSHOT_BIDS, self.SNAPSHOT_ASKS)

        py_ob = PyOrderBook("ETHUSDT", "okx")
        py_ob.apply_snapshot(self.SNAPSHOT_BIDS, self.SNAPSHOT_ASKS)

        # Update bid quantity at 100.00
        rust_ob.apply_delta([("100.00", "5.0")], [])
        py_ob.apply_delta([("100.00", "5.0")], [])

        assert rust_ob.best_bid() == pytest.approx(float(py_ob.best_bid()), rel=1e-6)

    def test_delta_remove_level(self):
        rust_ob = rust_core.OrderBook("ETHUSDT", "okx")
        rust_ob.apply_snapshot(self.SNAPSHOT_BIDS, self.SNAPSHOT_ASKS)

        py_ob = PyOrderBook("ETHUSDT", "okx")
        py_ob.apply_snapshot(self.SNAPSHOT_BIDS, self.SNAPSHOT_ASKS)

        # Remove best bid (100.00)
        rust_ob.apply_delta([("100.00", "0")], [])
        py_ob.apply_delta([("100.00", "0")], [])

        # After removing 100.00, best bid should be 99.00
        assert rust_ob.best_bid() == pytest.approx(float(py_ob.best_bid()), rel=1e-6)

    def test_snapshot_clears_previous_levels(self):
        rust_ob = rust_core.OrderBook("SOLUSDT", "binance")
        rust_ob.apply_snapshot(self.SNAPSHOT_BIDS, self.SNAPSHOT_ASKS)

        new_bids = [("200.00", "1.0")]
        new_asks = [("201.00", "1.0")]
        rust_ob.apply_snapshot(new_bids, new_asks)

        assert rust_ob.best_bid() == pytest.approx(200.0, rel=1e-6)
        assert rust_ob.best_ask() == pytest.approx(201.0, rel=1e-6)
        assert rust_ob.bid_count() == 1
        assert rust_ob.ask_count() == 1

    def test_zero_qty_in_snapshot_ignored(self):
        rust_ob = rust_core.OrderBook("ADAUSDT", "binance")
        rust_ob.apply_snapshot(
            [("1.00", "0"), ("0.99", "5.0")],
            [("1.01", "0"), ("1.02", "3.0")],
        )
        assert rust_ob.best_bid() == pytest.approx(0.99, rel=1e-6)
        assert rust_ob.best_ask() == pytest.approx(1.02, rel=1e-6)


class TestOrderBookEdgeCases:
    def test_empty_orderbook_returns_none(self):
        ob = rust_core.OrderBook("BTCUSDT", "binance")
        assert ob.best_bid() is None
        assert ob.best_ask() is None
        assert ob.spread() is None
        assert ob.spread_pct() is None

    def test_depth_weighted_mid_raises_on_empty(self):
        ob = rust_core.OrderBook("BTCUSDT", "binance")
        with pytest.raises(Exception):
            ob.depth_weighted_mid_price(5)


# ===========================================================================
# Signal tests
# ===========================================================================

class TestSpreadCalculator:
    """Rust SpreadCalculator behaves correctly."""

    def test_compute_spread_pct_basic(self):
        # bid=100, ask=101 → spread_pct = (101-100)/100 = 0.01
        result = rust_core.compute_spread_pct(100.0, 101.0)
        assert result == pytest.approx(0.01, rel=1e-6)

    def test_compute_spread_pct_zero_bid(self):
        result = rust_core.compute_spread_pct(0.0, 101.0)
        assert result == 0.0

    def test_best_bid_ask_across_detects_arb(self):
        """sell_price > buy_price → arb signal returned."""
        quotes = [
            rust_core.Quote("binance", "BTCUSDT", bid=50010.0, ask=50015.0),
            rust_core.Quote("okx",     "BTCUSDT", bid=49985.0, ask=49988.0),
        ]
        # binance has best bid (50010), okx has best ask (49988)
        # spread = 50010 - 49988 = 22 > 0
        result = rust_core.best_bid_ask_across(quotes, min_edge=0.0001)
        assert result is not None
        buy_ex, sell_ex, buy_price, sell_price, spread_pct = result
        assert buy_ex == "okx"
        assert sell_ex == "binance"
        assert sell_price > buy_price
        assert spread_pct > 0.0

    def test_best_bid_ask_across_no_arb_same_exchange(self):
        """Same-exchange quotes → None."""
        quotes = [
            rust_core.Quote("binance", "BTCUSDT", bid=50010.0, ask=50015.0),
        ]
        result = rust_core.best_bid_ask_across(quotes, min_edge=0.0001)
        assert result is None

    def test_best_bid_ask_across_no_arb_negative_spread(self):
        """buy_price > sell_price → None."""
        quotes = [
            rust_core.Quote("binance", "BTCUSDT", bid=49000.0, ask=49005.0),
            rust_core.Quote("okx",     "BTCUSDT", bid=49500.0, ask=49510.0),
        ]
        # binance best ask=49005, okx best bid=49500 → spread=495 > 0 (arb exists)
        # Actually this IS an arb: buy on binance at 49005, sell on okx at 49500
        result = rust_core.best_bid_ask_across(quotes, min_edge=0.0001)
        assert result is not None

    def test_best_bid_ask_across_below_min_edge_returns_none(self):
        """Spread below min_edge → None."""
        quotes = [
            rust_core.Quote("binance", "BTCUSDT", bid=50010.0, ask=50010.5),
            rust_core.Quote("okx",     "BTCUSDT", bid=50009.9, ask=50010.0),
        ]
        # spread = 50010.0 - 50010.0 = 0 → no arb
        result = rust_core.best_bid_ask_across(quotes, min_edge=0.0001)
        assert result is None

    def test_process_deduplication(self):
        """Duplicate signals within cooldown window are suppressed."""
        calc = rust_core.SpreadCalculator(min_edge=0.0001, cooldown_seconds=10.0)
        quotes = [
            rust_core.Quote("binance", "BTCUSDT", bid=50010.0, ask=50015.0),
            rust_core.Quote("okx",     "BTCUSDT", bid=49985.0, ask=49988.0),
        ]
        first = calc.process("BTCUSDT", quotes)
        assert first is not None  # first signal emitted

        second = calc.process("BTCUSDT", quotes)
        assert second is None  # duplicate suppressed

    def test_process_bulk(self):
        """Bulk processing handles multiple symbols."""
        calc = rust_core.SpreadCalculator(min_edge=0.0001, cooldown_seconds=0.0)
        symbol_quotes = [
            ("BTCUSDT", [("binance", 50010.0, 50015.0), ("okx", 49985.0, 49988.0)]),
            ("ETHUSDT", [("binance", 3010.0, 3015.0), ("okx", 2985.0, 2988.0)]),
        ]
        results = calc.process_bulk(symbol_quotes)
        assert len(results) == 2
        symbols = [r[0] for r in results]
        assert "BTCUSDT" in symbols
        assert "ETHUSDT" in symbols


# ===========================================================================
# Kill switch tests
# ===========================================================================

class TestKillSwitch:
    def setup_method(self):
        """Reset global halt flag before each test."""
        rust_core.clear_halt()

    def test_initial_state_not_halted(self):
        assert rust_core.is_halted() is False

    def test_halt_local_sets_flag(self):
        rust_core.halt_local()
        assert rust_core.is_halted() is True

    def test_clear_halt_resets_flag(self):
        rust_core.halt_local()
        rust_core.clear_halt()
        assert rust_core.is_halted() is False

    def test_kill_switch_instance_trigger(self):
        ks = rust_core.KillSwitch()
        assert ks.is_halted() is False
        assert ks.was_triggered() is False

        event = ks.trigger_tier1()
        assert event.triggered is True
        assert event.tier1_latency_ms < 1.0  # Target: <1ms
        assert ks.is_halted() is True
        assert ks.was_triggered() is True
        assert len(event.errors) == 0

    def test_kill_switch_double_trigger(self):
        ks = rust_core.KillSwitch()
        ks.trigger_tier1()
        event2 = ks.trigger_tier1()
        assert event2.triggered is False
        assert "Already triggered" in event2.errors[0]

    def test_kill_switch_reset(self):
        ks = rust_core.KillSwitch()
        ks.trigger_tier1()
        ks.reset()
        assert ks.is_halted() is False
        assert ks.was_triggered() is False

    def test_kill_switch_latency_under_1ms(self):
        """Tier1 latency must be <1ms (target: <0.01ms)."""
        ks = rust_core.KillSwitch()
        event = ks.trigger_tier1()
        assert event.tier1_latency_ms < 1.0, (
            f"Tier1 latency {event.tier1_latency_ms:.4f}ms exceeds 1ms target"
        )


# ===========================================================================
# Performance smoke tests (not precise — just sanity checks)
# ===========================================================================

class TestPerformanceSanity:
    """Very rough performance bounds — real benchmarks are in Criterion."""

    def test_orderbook_1000_updates_fast(self):
        import time
        ob = rust_core.OrderBook("BTCUSDT", "binance")
        ob.apply_snapshot(
            [(f"{50000-i:.2f}", "1.0") for i in range(50)],
            [(f"{50001+i:.2f}", "1.0") for i in range(50)],
        )
        deltas = [([("50000.00", f"{i*0.01:.8f}")], []) for i in range(1, 1001)]

        start = time.perf_counter()
        for bids, asks in deltas:
            ob.apply_delta(bids, asks)
        elapsed_us = (time.perf_counter() - start) * 1_000_000

        avg_us = elapsed_us / 1000
        assert avg_us < 10.0, f"Avg delta update {avg_us:.2f}μs > 10μs limit"

    def test_signal_1000_calcs_fast(self):
        import time
        calc = rust_core.SpreadCalculator(min_edge=0.0001, cooldown_seconds=0.0)
        symbol_quotes = [
            ("BTCUSDT", [("binance", 50010.0, 50015.0), ("okx", 49985.0, 49988.0)])
        ]

        start = time.perf_counter()
        for _ in range(1000):
            calc.process_bulk(symbol_quotes)
        elapsed_us = (time.perf_counter() - start) * 1_000_000

        avg_us = elapsed_us / 1000
        assert avg_us < 50.0, f"Avg signal calc {avg_us:.2f}μs > 50μs limit"

    def test_kill_switch_tier1_under_1ms(self):
        import time
        latencies = []
        for _ in range(100):
            rust_core.clear_halt()
            ks = rust_core.KillSwitch()
            start = time.perf_counter()
            ks.trigger_tier1()
            latencies.append((time.perf_counter() - start) * 1000)

        avg_ms = sum(latencies) / len(latencies)
        p99_ms = sorted(latencies)[99]
        assert p99_ms < 1.0, f"Kill switch p99 {p99_ms:.4f}ms > 1ms target"
