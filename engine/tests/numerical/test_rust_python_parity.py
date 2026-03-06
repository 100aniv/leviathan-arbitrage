"""Rust vs Python numerical parity tests.

Validates that Rust f64/FixedDecimal operations produce results within
documented tolerance (0.1 bps) of Python Decimal operations across
the full price range ($0.0001 — $200,000).

These tests skip gracefully when rust_core is not compiled.

Run:
    cd engine/rust_core && maturin develop && cd ..
    pytest tests/numerical/test_rust_python_parity.py -v

Architecture under test:
- Python OrderBook: src.core.order_book.OrderBook — uses decimal.Decimal
- Rust OrderBook:   rust_core.OrderBook (PyO3 binding) — uses f64 or i64 fixed-point
- Rust Signal:      rust_core.compute_spread_pct, rust_core.best_bid_ask_across
- Rust Kill Switch: rust_core.halt_local, rust_core.is_halted, rust_core.clear_halt
"""
from __future__ import annotations

import math
from decimal import Decimal
from typing import Optional

import pytest

# ---------------------------------------------------------------------------
# Module-level skip — entire file is skipped when rust_core is not compiled.
# pytest.importorskip raises Skipped (not ImportError), so the whole module
# is collected as "skipped" rather than "error".
# ---------------------------------------------------------------------------
rust_core = pytest.importorskip(
    "rust_core",
    reason="rust_core not compiled — run `cd engine/rust_core && maturin develop`",
)

# Verify the compiled PyO3 module is present, not just the source directory
# (Python treats rust_core/ as a namespace package without __init__.py).
if not hasattr(rust_core, "OrderBook"):
    pytest.skip(
        "rust_core exists as a directory but is not compiled — "
        "run `cd engine/rust_core && maturin develop`",
        allow_module_level=True,
    )

from src.core.order_book import OrderBook as PyOrderBook  # noqa: E402


# ===========================================================================
# Constants & shared test data
# ===========================================================================

#: Tolerance expressed in basis points (1 bps = 0.01%).  0.1 bps = 0.001%.
MAX_TOLERANCE_BPS = 0.1

#: Relative tolerance as a plain fraction for pytest.approx / math comparisons.
#: 0.1 bps == 1e-5 relative.
MAX_TOLERANCE_REL = MAX_TOLERANCE_BPS / 10_000  # 1e-5

#: Price-range fixtures: (label, best_bid, best_ask, tick_size)
#  Each row represents a realistic L2 book centred around a price level.
PRICE_RANGES = [
    ("micro_cap",   "0.00015",    "0.00016",    "0.000005"),
    ("mid_cap",     "1.4950",     "1.5050",     "0.0010"),
    ("btc_usdt",    "70208.50",   "70209.00",   "0.50"),
    ("high_price",  "199950.00",  "200050.00",  "10.00"),
]

# A denser, parametrized sweep used for the divergence-bound table.
SWEEP_PRICE_RANGES = [
    ("0.0001",  "0.00010000", "0.00010010", "0.00000001"),
    ("0.001",   "0.00100000", "0.00100100", "0.00000010"),
    ("0.01",    "0.01000000", "0.01001000", "0.00000100"),
    ("0.10",    "0.10000000", "0.10010000", "0.00001000"),
    ("1.0",     "1.00000000", "1.00100000", "0.00010000"),
    ("10",      "10.0000000", "10.0100000", "0.0010000"),
    ("100",     "100.000000", "100.010000", "0.001000"),
    ("1000",    "1000.00000", "1000.10000", "0.01000"),
    ("10000",   "10000.0000", "10000.1000", "0.1000"),
    ("70000",   "70000.0000", "70000.5000", "0.5000"),
    ("200000",  "200000.000", "200010.000", "1.000"),
]


# ===========================================================================
# Helpers
# ===========================================================================

def _relative_diff_bps(python_val: float, rust_val: float) -> float:
    """Compute the absolute relative difference in basis points.

    Returns 0.0 when both values are zero (no divergence).
    Raises ValueError when python_val is zero but rust_val is not, because a
    reference value of zero makes the relative metric undefined.

    Args:
        python_val: Reference value from Python Decimal implementation.
        rust_val:   Value from Rust f64 implementation.

    Returns:
        Absolute relative difference expressed in basis points (1 bps = 0.01%).
    """
    if python_val == 0.0 and rust_val == 0.0:
        return 0.0
    if python_val == 0.0:
        raise ValueError(
            f"Reference python_val is 0 but rust_val={rust_val}; "
            "relative divergence is undefined."
        )
    rel = abs(rust_val - python_val) / abs(python_val)
    return rel * 10_000  # convert fraction → bps


def _build_snapshot(
    best_bid: str,
    best_ask: str,
    tick: str,
    levels: int = 5,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Build a synthetic L2 snapshot with `levels` price levels on each side.

    Levels are placed at tick intervals away from the best bid/ask.  Quantities
    decrease with distance from the best level to model a realistic book shape.

    Args:
        best_bid:  Best bid price as a decimal string.
        best_ask:  Best ask price as a decimal string.
        tick:      Price increment between levels as a decimal string.
        levels:    Number of price levels per side.

    Returns:
        Tuple of (bids, asks) each as list of (price_str, qty_str) tuples.
    """
    bid = Decimal(best_bid)
    ask = Decimal(best_ask)
    step = Decimal(tick)

    bids: list[tuple[str, str]] = []
    asks: list[tuple[str, str]] = []

    for i in range(levels):
        price_bid = bid - step * i
        price_ask = ask + step * i
        qty = Decimal("2.0") - Decimal("0.3") * i  # 2.0, 1.7, 1.4, 1.1, 0.8
        qty_str = f"{qty:.8f}"
        bids.append((str(price_bid), qty_str))
        asks.append((str(price_ask), qty_str))

    return bids, asks


def _make_py_ob(
    bids: list[tuple[str, str]],
    asks: list[tuple[str, str]],
    symbol: str = "TESTUSDT",
    exchange: str = "binance",
) -> PyOrderBook:
    """Construct a Python OrderBook from snapshot data."""
    ob = PyOrderBook(symbol=symbol, exchange=exchange)
    ob.apply_snapshot(bids, asks)
    return ob


def _make_rust_ob(
    bids: list[tuple[str, str]],
    asks: list[tuple[str, str]],
    symbol: str = "TESTUSDT",
    exchange: str = "binance",
) -> "rust_core.OrderBook":
    """Construct a Rust OrderBook from snapshot data."""
    ob = rust_core.OrderBook(symbol, exchange)
    ob.apply_snapshot(bids, asks)
    return ob


# ===========================================================================
# Test class 1 — Basic orderbook operations across all price ranges
# ===========================================================================

class TestOrderBookParityBasic:
    """Rust OrderBook basic operations match Python Decimal within 0.1 bps.

    Exercises best_bid(), best_ask(), and spread() at four representative
    price levels covering the full supported range ($0.0001 — $200,000).
    """

    @pytest.mark.parametrize(
        "label,best_bid,best_ask,tick",
        PRICE_RANGES,
        ids=[p[0] for p in PRICE_RANGES],
    )
    def test_best_bid_within_tolerance(
        self, label: str, best_bid: str, best_ask: str, tick: str
    ) -> None:
        """Rust best_bid() matches Python best_bid() within 0.1 bps."""
        bids, asks = _build_snapshot(best_bid, best_ask, tick)
        py_ob = _make_py_ob(bids, asks)
        rust_ob = _make_rust_ob(bids, asks)

        py_val = float(py_ob.best_bid())
        rust_val = rust_ob.best_bid()

        assert rust_val is not None, f"[{label}] Rust best_bid() returned None unexpectedly"
        divergence_bps = _relative_diff_bps(py_val, rust_val)
        assert divergence_bps < MAX_TOLERANCE_BPS, (
            f"[{label}] best_bid divergence {divergence_bps:.4f} bps exceeds "
            f"{MAX_TOLERANCE_BPS} bps limit. "
            f"Python={py_val}, Rust={rust_val}"
        )

    @pytest.mark.parametrize(
        "label,best_bid,best_ask,tick",
        PRICE_RANGES,
        ids=[p[0] for p in PRICE_RANGES],
    )
    def test_best_ask_within_tolerance(
        self, label: str, best_bid: str, best_ask: str, tick: str
    ) -> None:
        """Rust best_ask() matches Python best_ask() within 0.1 bps."""
        bids, asks = _build_snapshot(best_bid, best_ask, tick)
        py_ob = _make_py_ob(bids, asks)
        rust_ob = _make_rust_ob(bids, asks)

        py_val = float(py_ob.best_ask())
        rust_val = rust_ob.best_ask()

        assert rust_val is not None, f"[{label}] Rust best_ask() returned None unexpectedly"
        divergence_bps = _relative_diff_bps(py_val, rust_val)
        assert divergence_bps < MAX_TOLERANCE_BPS, (
            f"[{label}] best_ask divergence {divergence_bps:.4f} bps exceeds "
            f"{MAX_TOLERANCE_BPS} bps limit. "
            f"Python={py_val}, Rust={rust_val}"
        )

    @pytest.mark.parametrize(
        "label,best_bid,best_ask,tick",
        PRICE_RANGES,
        ids=[p[0] for p in PRICE_RANGES],
    )
    def test_spread_within_tolerance(
        self, label: str, best_bid: str, best_ask: str, tick: str
    ) -> None:
        """Rust spread() matches Python spread() within 0.1 bps relative to mid price."""
        bids, asks = _build_snapshot(best_bid, best_ask, tick)
        py_ob = _make_py_ob(bids, asks)
        rust_ob = _make_rust_ob(bids, asks)

        py_spread = float(py_ob.spread())
        rust_spread = rust_ob.spread()

        assert rust_spread is not None, f"[{label}] Rust spread() returned None unexpectedly"

        # Express divergence relative to mid price to avoid issues when spread
        # itself is tiny (e.g., 1-tick spread at $70,000 is $0.50).
        mid_price = (float(py_ob.best_bid()) + float(py_ob.best_ask())) / 2.0
        abs_diff = abs(rust_spread - py_spread)
        divergence_bps = (abs_diff / mid_price) * 10_000

        assert divergence_bps < MAX_TOLERANCE_BPS, (
            f"[{label}] spread divergence {divergence_bps:.4f} bps "
            f"(|{abs_diff}| / mid {mid_price}) exceeds {MAX_TOLERANCE_BPS} bps. "
            f"Python={py_spread}, Rust={rust_spread}"
        )


# ===========================================================================
# Test class 2 — Depth-weighted mid price
# ===========================================================================

class TestOrderBookParityDepthWeightedMid:
    """Rust depth_weighted_mid_price() matches Python within 0.1 bps."""

    @pytest.mark.parametrize(
        "label,best_bid,best_ask,tick",
        PRICE_RANGES,
        ids=[p[0] for p in PRICE_RANGES],
    )
    def test_depth_weighted_mid_price_within_tolerance(
        self, label: str, best_bid: str, best_ask: str, tick: str
    ) -> None:
        """Rust depth_weighted_mid_price(5) matches Python within 0.1 bps."""
        bids, asks = _build_snapshot(best_bid, best_ask, tick, levels=5)
        py_ob = _make_py_ob(bids, asks)
        rust_ob = _make_rust_ob(bids, asks)

        py_mid = float(py_ob.depth_weighted_mid_price(depth=5))
        rust_mid = rust_ob.depth_weighted_mid_price(5)

        divergence_bps = _relative_diff_bps(py_mid, rust_mid)
        assert divergence_bps < MAX_TOLERANCE_BPS, (
            f"[{label}] depth_weighted_mid divergence {divergence_bps:.4f} bps "
            f"exceeds {MAX_TOLERANCE_BPS} bps. "
            f"Python={py_mid:.10g}, Rust={rust_mid:.10g}"
        )

    def test_depth_weighted_mid_single_level_exact(self) -> None:
        """With one level per side, depth_weighted_mid equals arithmetic mid.

        This is a precise sanity check: when each side has exactly one level
        with equal quantity, the VWAP collapses to the price, so
        depth_weighted_mid = (bid + ask) / 2.
        """
        bids = [("100.00", "1.0")]
        asks = [("101.00", "1.0")]
        py_ob = _make_py_ob(bids, asks)
        rust_ob = _make_rust_ob(bids, asks)

        py_mid = float(py_ob.depth_weighted_mid_price(depth=1))
        rust_mid = rust_ob.depth_weighted_mid_price(1)

        # Both should converge to exactly 100.5 — verify round-trip precision.
        assert py_mid == pytest.approx(100.5, rel=1e-9), (
            f"Python mid {py_mid} deviates from expected 100.5"
        )
        assert rust_mid == pytest.approx(100.5, rel=MAX_TOLERANCE_REL), (
            f"Rust mid {rust_mid} deviates from expected 100.5 "
            f"beyond {MAX_TOLERANCE_BPS} bps tolerance"
        )

    @pytest.mark.parametrize("depth", [1, 3, 5], ids=["depth_1", "depth_3", "depth_5"])
    def test_depth_parameter_affects_result_consistently(self, depth: int) -> None:
        """Both implementations return consistent results for the same depth value."""
        bids, asks = _build_snapshot("70208.50", "70209.00", "0.50", levels=5)
        py_ob = _make_py_ob(bids, asks)
        rust_ob = _make_rust_ob(bids, asks)

        py_mid = float(py_ob.depth_weighted_mid_price(depth=depth))
        rust_mid = rust_ob.depth_weighted_mid_price(depth)

        divergence_bps = _relative_diff_bps(py_mid, rust_mid)
        assert divergence_bps < MAX_TOLERANCE_BPS, (
            f"depth={depth}: divergence {divergence_bps:.4f} bps "
            f"exceeds {MAX_TOLERANCE_BPS} bps. "
            f"Python={py_mid:.10g}, Rust={rust_mid:.10g}"
        )


# ===========================================================================
# Test class 3 — Delta update parity
# ===========================================================================

class TestOrderBookParityDeltaUpdates:
    """Rust and Python orderbooks converge to identical state after delta updates."""

    def test_delta_update_quantity_both_sides(self) -> None:
        """After applying matching bid and ask quantity updates, state matches."""
        bids = [("50010.00", "1.5"), ("50009.00", "2.0"), ("50008.00", "0.5")]
        asks = [("50011.00", "1.2"), ("50012.00", "0.8"), ("50013.00", "2.5")]

        py_ob = _make_py_ob(bids, asks)
        rust_ob = _make_rust_ob(bids, asks)

        # Apply identical delta to both
        delta_bids = [("50010.00", "3.0"), ("50007.00", "1.0")]  # update + new level
        delta_asks = [("50011.00", "2.4")]                        # quantity change

        py_ob.apply_delta(delta_bids, delta_asks)
        rust_ob.apply_delta(delta_bids, delta_asks)

        assert rust_ob.best_bid() == pytest.approx(float(py_ob.best_bid()), rel=MAX_TOLERANCE_REL), (
            "best_bid diverged after delta updates"
        )
        assert rust_ob.best_ask() == pytest.approx(float(py_ob.best_ask()), rel=MAX_TOLERANCE_REL), (
            "best_ask diverged after delta updates"
        )

    def test_delta_remove_best_bid_level(self) -> None:
        """Removing the best bid level by setting qty=0 updates best_bid identically."""
        bids = [("100.00", "1.0"), ("99.00", "2.0"), ("98.00", "0.5")]
        asks = [("101.00", "1.5"), ("102.00", "0.8")]

        py_ob = _make_py_ob(bids, asks)
        rust_ob = _make_rust_ob(bids, asks)

        # Remove best bid in both
        py_ob.apply_delta([("100.00", "0")], [])
        rust_ob.apply_delta([("100.00", "0")], [])

        assert rust_ob.best_bid() == pytest.approx(float(py_ob.best_bid()), rel=MAX_TOLERANCE_REL), (
            "best_bid should be 99.00 after removing 100.00 in both implementations"
        )

    def test_delta_remove_best_ask_level(self) -> None:
        """Removing the best ask level by setting qty=0 updates best_ask identically."""
        bids = [("100.00", "1.0")]
        asks = [("101.00", "1.5"), ("102.00", "0.8"), ("103.00", "2.0")]

        py_ob = _make_py_ob(bids, asks)
        rust_ob = _make_rust_ob(bids, asks)

        py_ob.apply_delta([], [("101.00", "0")])
        rust_ob.apply_delta([], [("101.00", "0")])

        assert rust_ob.best_ask() == pytest.approx(float(py_ob.best_ask()), rel=MAX_TOLERANCE_REL), (
            "best_ask should be 102.00 after removing 101.00 in both implementations"
        )

    def test_multiple_sequential_deltas_preserve_parity(self) -> None:
        """Applying 10 sequential delta updates preserves parity throughout."""
        bids, asks = _build_snapshot("70208.50", "70209.00", "0.50", levels=5)

        py_ob = _make_py_ob(bids, asks)
        rust_ob = _make_rust_ob(bids, asks)

        # Sequence of 10 updates that modify quantities at existing levels
        for i in range(1, 11):
            new_qty = f"{1.0 + i * 0.1:.8f}"
            delta_bid = [("70208.50", new_qty)]
            delta_ask = [("70209.00", new_qty)]

            py_ob.apply_delta(delta_bid, delta_ask)
            rust_ob.apply_delta(delta_bid, delta_ask)

        py_bid = float(py_ob.best_bid())
        rust_bid = rust_ob.best_bid()
        divergence_bps = _relative_diff_bps(py_bid, rust_bid)
        assert divergence_bps < MAX_TOLERANCE_BPS, (
            f"best_bid diverged after 10 sequential deltas: "
            f"Python={py_bid}, Rust={rust_bid}, diff={divergence_bps:.4f} bps"
        )

    def test_snapshot_after_deltas_resets_state_identically(self) -> None:
        """Applying a new snapshot after deltas resets both books to identical state."""
        bids, asks = _build_snapshot("50000.00", "50001.00", "1.00")
        py_ob = _make_py_ob(bids, asks)
        rust_ob = _make_rust_ob(bids, asks)

        # Apply some deltas first
        py_ob.apply_delta([("50000.00", "0")], [("50001.00", "5.0")])
        rust_ob.apply_delta([("50000.00", "0")], [("50001.00", "5.0")])

        # Then apply a fresh snapshot
        new_bids = [("60000.00", "1.0"), ("59999.00", "2.0")]
        new_asks = [("60001.00", "1.0"), ("60002.00", "2.0")]
        py_ob.apply_snapshot(new_bids, new_asks)
        rust_ob.apply_snapshot(new_bids, new_asks)

        assert rust_ob.best_bid() == pytest.approx(float(py_ob.best_bid()), rel=MAX_TOLERANCE_REL)
        assert rust_ob.best_ask() == pytest.approx(float(py_ob.best_ask()), rel=MAX_TOLERANCE_REL)


# ===========================================================================
# Test class 4 — Signal / spread-pct parity
# ===========================================================================

class TestSignalSpreadPctParity:
    """Rust compute_spread_pct() matches Python (ask - bid) / bid within 0.1 bps.

    The Python reference is computed using double-precision float arithmetic
    (not Decimal) because compute_spread_pct operates in float domain on both
    sides — the test validates f64 rounding, not Decimal vs float.
    """

    #: (label, bid_str, ask_str)
    SPREAD_PAIRS = [
        ("micro_cap_tight",  "0.00015000",  "0.00015100"),
        ("micro_cap_wide",   "0.00015000",  "0.00016000"),
        ("mid_cap_tight",    "1.4950",       "1.4960"),
        ("mid_cap_wide",     "1.4950",       "1.5050"),
        ("btc_tight",        "70208.50",     "70209.00"),
        ("btc_wide",         "70000.00",     "70070.00"),
        ("high_price_tight", "199950.00",    "200050.00"),
        ("high_price_wide",  "199000.00",    "200000.00"),
    ]

    @pytest.mark.parametrize(
        "label,bid_str,ask_str",
        SPREAD_PAIRS,
        ids=[p[0] for p in SPREAD_PAIRS],
    )
    def test_compute_spread_pct_within_tolerance(
        self, label: str, bid_str: str, ask_str: str
    ) -> None:
        """Rust compute_spread_pct(bid, ask) matches Python (ask-bid)/bid within 0.1 bps."""
        bid = float(bid_str)
        ask = float(ask_str)

        # Python reference: same formula, same float domain
        py_spread_pct = (ask - bid) / bid

        rust_spread_pct = rust_core.compute_spread_pct(bid, ask)

        divergence_bps = _relative_diff_bps(py_spread_pct, rust_spread_pct)
        assert divergence_bps < MAX_TOLERANCE_BPS, (
            f"[{label}] compute_spread_pct divergence {divergence_bps:.4f} bps "
            f"exceeds {MAX_TOLERANCE_BPS} bps. "
            f"bid={bid}, ask={ask}, Python={py_spread_pct:.10g}, Rust={rust_spread_pct:.10g}"
        )

    def test_compute_spread_pct_zero_bid_returns_zero(self) -> None:
        """compute_spread_pct with bid=0 returns 0 (guard against division by zero)."""
        result = rust_core.compute_spread_pct(0.0, 100.0)
        assert result == 0.0, (
            f"Expected 0.0 for zero-bid input, got {result}"
        )

    def test_compute_spread_pct_zero_spread_returns_zero(self) -> None:
        """compute_spread_pct with bid == ask returns 0 (locked market)."""
        result = rust_core.compute_spread_pct(50000.0, 50000.0)
        assert result == pytest.approx(0.0, abs=1e-10), (
            f"Zero-spread market should return ~0, got {result}"
        )

    def test_spread_pct_consistent_with_orderbook_spread_pct(self) -> None:
        """rust_core.compute_spread_pct and rust OrderBook.spread_pct() agree.

        Both code paths compute (ask - bid) / bid.  They should match to within
        floating-point rounding (well under 0.1 bps).
        """
        bids = [("70208.50", "1.5")]
        asks = [("70209.00", "1.2")]

        rust_ob = _make_rust_ob(bids, asks)
        ob_spread_pct = rust_ob.spread_pct()
        signal_spread_pct = rust_core.compute_spread_pct(
            rust_ob.best_bid(), rust_ob.best_ask()
        )

        assert ob_spread_pct is not None
        divergence_bps = _relative_diff_bps(ob_spread_pct, signal_spread_pct)
        assert divergence_bps < MAX_TOLERANCE_BPS, (
            f"OrderBook.spread_pct() ({ob_spread_pct:.10g}) and "
            f"compute_spread_pct() ({signal_spread_pct:.10g}) diverged by "
            f"{divergence_bps:.4f} bps"
        )


# ===========================================================================
# Test class 5 — Kill switch parity
# ===========================================================================

class TestKillSwitchParity:
    """Rust kill-switch module-level functions behave correctly and independently
    from the Python kill switch.

    The Python and Rust halt flags are stored in separate memory spaces
    (Python threading.Event vs Rust AtomicBool).  This test suite verifies:
      1. Rust halt/clear/is_halted semantics are correct.
      2. Python and Rust flags are independent: setting one does not affect
         the other.
    """

    def setup_method(self) -> None:
        """Reset Rust global halt flag before every test to ensure isolation."""
        rust_core.clear_halt()

    def teardown_method(self) -> None:
        """Clean up Rust global halt flag after every test."""
        rust_core.clear_halt()

    def test_initial_rust_state_is_not_halted(self) -> None:
        """Rust is_halted() returns False before any halt_local() call."""
        assert rust_core.is_halted() is False

    def test_rust_halt_local_sets_halted_flag(self) -> None:
        """Rust halt_local() causes is_halted() to return True."""
        rust_core.halt_local()
        assert rust_core.is_halted() is True

    def test_rust_clear_halt_resets_halted_flag(self) -> None:
        """Rust clear_halt() causes is_halted() to return False after a halt."""
        rust_core.halt_local()
        assert rust_core.is_halted() is True  # pre-condition
        rust_core.clear_halt()
        assert rust_core.is_halted() is False

    def test_rust_halt_clear_halt_cycle_is_idempotent(self) -> None:
        """Repeated halt/clear cycles leave the flag in the correct final state."""
        for _ in range(5):
            rust_core.halt_local()
            assert rust_core.is_halted() is True
            rust_core.clear_halt()
            assert rust_core.is_halted() is False

    def test_python_halt_does_not_affect_rust_flag(self) -> None:
        """Setting the Python halt flag does not set the Rust halt flag.

        The Python kill switch uses threading.Event; Rust uses AtomicBool.
        These are independent in-process flags with no shared memory.
        """
        from src.risk.kill_switch import clear_halt as py_clear_halt
        from src.risk.kill_switch import halt_local as py_halt_local
        from src.risk.kill_switch import is_halted as py_is_halted

        # Ensure clean state
        py_clear_halt()
        rust_core.clear_halt()

        # Activate Python halt only
        py_halt_local()
        assert py_is_halted() is True
        assert rust_core.is_halted() is False, (
            "Rust halt flag was set by Python halt_local() — flags are NOT independent"
        )

        # Cleanup
        py_clear_halt()

    def test_rust_halt_does_not_affect_python_flag(self) -> None:
        """Setting the Rust halt flag does not set the Python halt flag."""
        from src.risk.kill_switch import clear_halt as py_clear_halt
        from src.risk.kill_switch import is_halted as py_is_halted

        py_clear_halt()
        rust_core.clear_halt()

        # Activate Rust halt only
        rust_core.halt_local()
        assert rust_core.is_halted() is True
        assert py_is_halted() is False, (
            "Python halt flag was set by Rust halt_local() — flags are NOT independent"
        )

    def test_rust_kill_switch_instance_trigger_sets_global_halt(self) -> None:
        """KillSwitch instance trigger_tier1() sets the global is_halted() flag."""
        ks = rust_core.KillSwitch()
        assert ks.is_halted() is False

        event = ks.trigger_tier1()

        assert event.triggered is True
        assert ks.is_halted() is True
        assert rust_core.is_halted() is True

    def test_rust_kill_switch_reset_clears_global_halt(self) -> None:
        """KillSwitch instance reset() clears both instance and global halt flags."""
        ks = rust_core.KillSwitch()
        ks.trigger_tier1()
        assert rust_core.is_halted() is True  # pre-condition

        ks.reset()

        assert ks.is_halted() is False
        assert rust_core.is_halted() is False

    def test_rust_kill_switch_tier1_latency_under_1ms(self) -> None:
        """KillSwitch.trigger_tier1() records latency < 1ms (hard SLA)."""
        ks = rust_core.KillSwitch()
        event = ks.trigger_tier1()
        assert event.tier1_latency_ms < 1.0, (
            f"Tier1 latency {event.tier1_latency_ms:.4f}ms exceeds 1ms SLA"
        )


# ===========================================================================
# Test class 6 — Full divergence sweep + summary table
# ===========================================================================

class TestDivergenceBounds:
    """Parametrized sweep across the full price range to document max divergence.

    Records the observed divergence for each metric at each price level and
    asserts that all observed values remain within the 0.1 bps tolerance bound.
    A summary table is printed at the end of the session via a session-scoped
    fixture so it appears once in the test output regardless of how many
    parametrized cases run.
    """

    @pytest.mark.parametrize(
        "label,best_bid,best_ask,tick",
        SWEEP_PRICE_RANGES,
        ids=[p[0] for p in SWEEP_PRICE_RANGES],
    )
    def test_best_bid_divergence_within_bounds(
        self,
        label: str,
        best_bid: str,
        best_ask: str,
        tick: str,
        divergence_recorder: "DivergenceRecorder",
    ) -> None:
        """best_bid() divergence stays within 0.1 bps across the full price sweep."""
        bids, asks = _build_snapshot(best_bid, best_ask, tick)
        py_ob = _make_py_ob(bids, asks)
        rust_ob = _make_rust_ob(bids, asks)

        py_val = float(py_ob.best_bid())
        rust_val = rust_ob.best_bid()
        assert rust_val is not None

        diff_bps = _relative_diff_bps(py_val, rust_val)
        divergence_recorder.record("best_bid", label, diff_bps)

        assert diff_bps < MAX_TOLERANCE_BPS, (
            f"[{label}] best_bid divergence {diff_bps:.6f} bps > {MAX_TOLERANCE_BPS} bps"
        )

    @pytest.mark.parametrize(
        "label,best_bid,best_ask,tick",
        SWEEP_PRICE_RANGES,
        ids=[p[0] for p in SWEEP_PRICE_RANGES],
    )
    def test_best_ask_divergence_within_bounds(
        self,
        label: str,
        best_bid: str,
        best_ask: str,
        tick: str,
        divergence_recorder: "DivergenceRecorder",
    ) -> None:
        """best_ask() divergence stays within 0.1 bps across the full price sweep."""
        bids, asks = _build_snapshot(best_bid, best_ask, tick)
        py_ob = _make_py_ob(bids, asks)
        rust_ob = _make_rust_ob(bids, asks)

        py_val = float(py_ob.best_ask())
        rust_val = rust_ob.best_ask()
        assert rust_val is not None

        diff_bps = _relative_diff_bps(py_val, rust_val)
        divergence_recorder.record("best_ask", label, diff_bps)

        assert diff_bps < MAX_TOLERANCE_BPS, (
            f"[{label}] best_ask divergence {diff_bps:.6f} bps > {MAX_TOLERANCE_BPS} bps"
        )

    @pytest.mark.parametrize(
        "label,best_bid,best_ask,tick",
        SWEEP_PRICE_RANGES,
        ids=[p[0] for p in SWEEP_PRICE_RANGES],
    )
    def test_depth_weighted_mid_divergence_within_bounds(
        self,
        label: str,
        best_bid: str,
        best_ask: str,
        tick: str,
        divergence_recorder: "DivergenceRecorder",
    ) -> None:
        """depth_weighted_mid_price(5) divergence stays within 0.1 bps."""
        bids, asks = _build_snapshot(best_bid, best_ask, tick, levels=5)
        py_ob = _make_py_ob(bids, asks)
        rust_ob = _make_rust_ob(bids, asks)

        py_val = float(py_ob.depth_weighted_mid_price(depth=5))
        rust_val = rust_ob.depth_weighted_mid_price(5)

        diff_bps = _relative_diff_bps(py_val, rust_val)
        divergence_recorder.record("depth_mid", label, diff_bps)

        assert diff_bps < MAX_TOLERANCE_BPS, (
            f"[{label}] depth_weighted_mid divergence {diff_bps:.6f} bps > {MAX_TOLERANCE_BPS} bps"
        )

    @pytest.mark.parametrize(
        "label,best_bid,best_ask,tick",
        SWEEP_PRICE_RANGES,
        ids=[p[0] for p in SWEEP_PRICE_RANGES],
    )
    def test_spread_pct_divergence_within_bounds(
        self,
        label: str,
        best_bid: str,
        best_ask: str,
        tick: str,
        divergence_recorder: "DivergenceRecorder",
    ) -> None:
        """spread_pct() divergence stays within 0.1 bps across the full price sweep."""
        bids, asks = _build_snapshot(best_bid, best_ask, tick)
        py_ob = _make_py_ob(bids, asks)
        rust_ob = _make_rust_ob(bids, asks)

        py_pct = py_ob.spread_pct()
        rust_pct = rust_ob.spread_pct()

        assert py_pct is not None
        assert rust_pct is not None

        py_val = float(py_pct)
        diff_bps = _relative_diff_bps(py_val, rust_pct)
        divergence_recorder.record("spread_pct", label, diff_bps)

        assert diff_bps < MAX_TOLERANCE_BPS, (
            f"[{label}] spread_pct divergence {diff_bps:.6f} bps > {MAX_TOLERANCE_BPS} bps"
        )


# ===========================================================================
# Fixtures for divergence recording & summary
# ===========================================================================

class DivergenceRecorder:
    """Accumulates (metric, price_label, divergence_bps) tuples for reporting."""

    def __init__(self) -> None:
        self._records: list[tuple[str, str, float]] = []

    def record(self, metric: str, label: str, diff_bps: float) -> None:
        self._records.append((metric, label, diff_bps))

    def print_summary(self) -> None:
        """Print a formatted divergence summary table to stdout."""
        if not self._records:
            return

        # Group by metric, find max divergence per metric
        from collections import defaultdict
        by_metric: dict[str, list[tuple[str, float]]] = defaultdict(list)
        for metric, label, diff in self._records:
            by_metric[metric].append((label, diff))

        col_w_metric = 18
        col_w_label  = 14
        col_w_max    = 14
        col_w_pass   = 10

        sep = (
            "+" + "-" * col_w_metric
            + "+" + "-" * col_w_label
            + "+" + "-" * col_w_max
            + "+" + "-" * col_w_pass
            + "+"
        )

        print()
        print("=" * len(sep))
        print("  Rust vs Python Numerical Parity — Divergence Summary")
        print(f"  Tolerance: {MAX_TOLERANCE_BPS} bps  |  Date: 2026-03-06")
        print("=" * len(sep))

        header = (
            f"| {'Metric':<{col_w_metric - 2}}"
            f"| {'Price range':<{col_w_label - 2}}"
            f"| {'Max div (bps)':<{col_w_max - 2}}"
            f"| {'Pass?':<{col_w_pass - 2}}|"
        )
        print(sep)
        print(header)
        print(sep)

        for metric in sorted(by_metric.keys()):
            entries = by_metric[metric]
            max_label, max_diff = max(entries, key=lambda x: x[1])
            passed = "YES" if max_diff < MAX_TOLERANCE_BPS else "FAIL"
            row = (
                f"| {metric:<{col_w_metric - 2}}"
                f"| {max_label:<{col_w_label - 2}}"
                f"| {max_diff:<{col_w_max - 2}.6f}"
                f"| {passed:<{col_w_pass - 2}}|"
            )
            print(row)

        print(sep)
        print()


@pytest.fixture(scope="module")
def divergence_recorder() -> "DivergenceRecorder":  # type: ignore[return]
    """Module-scoped recorder that prints the summary table when the module finishes."""
    recorder = DivergenceRecorder()
    yield recorder
    recorder.print_summary()
