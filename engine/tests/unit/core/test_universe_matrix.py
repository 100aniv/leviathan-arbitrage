"""Unit tests for UniverseMatrix (Path-B Day-2 boot-time validator, BUG-225 class).

These tests use lightweight stub adapters — no network, no real exchange
clients. The goal is to verify the matrix correctly enumerates valid
(strategy, symbol, leg) tuples and rejects invalid ones with clear
reason-code counters.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from src.core.universe_matrix import UniverseEntry, UniverseMatrix


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class StubAdapter:
    """Minimal adapter stub honoring the supports_symbol + get_min_notional contract."""

    def __init__(
        self,
        exchange_id: str,
        market_type: str = "spot",
        listed: set[str] | None = None,
        min_notional: Decimal = Decimal("5"),
    ) -> None:
        self.exchange_id = exchange_id
        self._market_type = market_type
        self._listed = listed if listed is not None else {"BTC/USDT", "ETH/USDT"}
        self._min_notional = min_notional
        # Expose a symbol list so UniverseMatrix picks them up at build time.
        self.symbols = set(self._listed)

    def supports_symbol(self, symbol: str) -> bool:
        return symbol in self._listed

    async def get_min_notional(self, symbol: str) -> Decimal:
        return self._min_notional


class StubStrategy:
    """Minimal strategy stub — the matrix only reads STRATEGY_TYPE + strategy_id."""

    def __init__(self, strategy_id: str, strategy_type: str) -> None:
        self.strategy_id = strategy_id
        self.STRATEGY_TYPE = strategy_type


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_empty_matrix_from_empty_registry():
    """Empty exchange set and empty strategies → zero entries."""
    matrix = UniverseMatrix()
    await matrix.build(exchange_registry={}, strategy_registry=[])
    assert matrix.size() == 0
    assert matrix.dump_report() == "universe_matrix report"


@pytest.mark.asyncio
async def test_build_correct_entries_for_spot_pair():
    """Two spot exchanges both listing BTC/USDT → 1 cross_exchange entry per symbol."""
    binance = StubAdapter("binance", listed={"BTC/USDT", "ETH/USDT"})
    bitget = StubAdapter("bitget", listed={"BTC/USDT", "ETH/USDT"})
    registry = {"binance": binance, "bitget": bitget}
    strategy = StubStrategy("cross_exchange_v1", "cross_exchange_spot")

    matrix = UniverseMatrix()
    await matrix.build(exchange_registry=registry, strategy_registry=[strategy])

    # BTC/USDT + ETH/USDT × 1 canonical (binance, bitget) pair
    assert matrix.size() == 2
    assert matrix.has_entry("cross_exchange_v1", "BTC/USDT", "binance", "bitget")
    assert matrix.has_entry("cross_exchange_v1", "ETH/USDT", "binance", "bitget")
    # mirror pair must NOT be stored — canonicalized
    assert not matrix.has_entry("cross_exchange_v1", "BTC/USDT", "bitget", "binance")


@pytest.mark.asyncio
async def test_rejects_aave_usdt_on_upbit_symbol_not_listed():
    """Upbit doesn't list AAVE/USDT even though AAVE/KRW exists."""
    binance = StubAdapter("binance", listed={"AAVE/USDT"})
    upbit = StubAdapter("upbit", listed={"AAVE/KRW"})  # AAVE/USDT missing
    registry = {"binance": binance, "upbit": upbit}
    strategy = StubStrategy("cross_exchange_v1", "cross_exchange_spot")

    matrix = UniverseMatrix()
    await matrix.build(exchange_registry=registry, strategy_registry=[strategy])

    # No AAVE/USDT pair possible — only one leg listed
    assert not matrix.has_entry("cross_exchange_v1", "AAVE/USDT", "binance", "upbit")
    # rejection counter for upbit leg must be populated
    report = matrix.dump_report()
    assert "upbit_spot_listing_miss" in report


@pytest.mark.asyncio
async def test_rejects_cross_exchange_when_one_leg_unlisted():
    """Only one of two legs lists the symbol → no entry."""
    binance = StubAdapter("binance", listed={"AAVE/USDT"})
    bithumb = StubAdapter("bithumb", listed={"BTC/USDT"})  # no AAVE
    registry = {"binance": binance, "bithumb": bithumb}
    strategy = StubStrategy("cross_exchange_v1", "cross_exchange_spot")

    matrix = UniverseMatrix()
    await matrix.build(exchange_registry=registry, strategy_registry=[strategy])

    assert not matrix.has_entry("cross_exchange_v1", "AAVE/USDT", "binance", "bithumb")
    # BTC/USDT is listed on bithumb but not on binance — also rejected
    assert not matrix.has_entry("cross_exchange_v1", "BTC/USDT", "binance", "bithumb")


@pytest.mark.asyncio
async def test_has_entry_returns_true_for_known_valid_pair():
    binance = StubAdapter("binance", listed={"ETH/USDT"})
    bitget = StubAdapter("bitget", listed={"ETH/USDT"})
    registry = {"binance": binance, "bitget": bitget}
    strategy = StubStrategy("ce_v1", "cross_exchange_spot")

    matrix = UniverseMatrix()
    await matrix.build(exchange_registry=registry, strategy_registry=[strategy])

    assert matrix.has_entry("ce_v1", "ETH/USDT", "binance", "bitget") is True
    assert matrix.has_entry("ce_v1", "ETH/USDT", "binance", "bybit") is False


@pytest.mark.asyncio
async def test_get_entry_returns_min_notional_in_usd():
    binance = StubAdapter("binance", listed={"BTC/USDT"}, min_notional=Decimal("20"))
    bitget = StubAdapter("bitget", listed={"BTC/USDT"}, min_notional=Decimal("6"))
    registry = {"binance": binance, "bitget": bitget}
    strategy = StubStrategy("ce_v1", "cross_exchange_spot")

    matrix = UniverseMatrix()
    await matrix.build(exchange_registry=registry, strategy_registry=[strategy])

    entry = matrix.get_entry("ce_v1", "BTC/USDT", "binance", "bitget")
    assert isinstance(entry, UniverseEntry)
    assert entry.min_notional_usd_a == Decimal("20")
    assert entry.min_notional_usd_b == Decimal("6")
    assert entry.required_market_type_a == "spot"
    assert entry.required_market_type_b == "spot"


@pytest.mark.asyncio
async def test_dump_report_format_includes_counts_by_reason():
    binance = StubAdapter("binance", listed={"BTC/USDT", "AAVE/USDT"})
    upbit = StubAdapter("upbit", listed={"BTC/USDT"})  # AAVE/USDT missing
    registry = {"binance": binance, "upbit": upbit}
    strategy = StubStrategy("ce_v1", "cross_exchange_spot")

    matrix = UniverseMatrix()
    await matrix.build(exchange_registry=registry, strategy_registry=[strategy])

    report = matrix.dump_report()
    assert "ce_v1" in report
    assert "valid pairs" in report
    assert "rejected" in report
    assert "listing_miss" in report  # reason code present


@pytest.mark.asyncio
async def test_futures_futures_separates_from_spot():
    """Futures strategies only pair with futures exchanges."""
    binance_spot = StubAdapter("binance", market_type="spot", listed={"BTC/USDT"})
    binance_fut = StubAdapter("binance_futures", market_type="futures", listed={"BTC/USDT"})
    bitget_fut = StubAdapter("bitget_futures", market_type="futures", listed={"BTC/USDT"})
    registry = {
        "binance": binance_spot,
        "binance_futures": binance_fut,
        "bitget_futures": bitget_fut,
    }
    strategy = StubStrategy("ff_v1", "futures_futures")

    matrix = UniverseMatrix()
    await matrix.build(exchange_registry=registry, strategy_registry=[strategy])

    # Should be {(binance_futures, bitget_futures)} — spot exchange excluded
    assert matrix.has_entry("ff_v1", "BTC/USDT", "binance_futures", "bitget_futures")
    assert not matrix.has_entry("ff_v1", "BTC/USDT", "binance", "binance_futures")
    assert not matrix.has_entry("ff_v1", "BTC/USDT", "binance", "bitget_futures")


@pytest.mark.asyncio
async def test_spot_futures_mixes_market_types():
    """spot_futures needs one spot + one futures exchange."""
    binance_spot = StubAdapter("binance", market_type="spot", listed={"BTC/USDT"})
    binance_fut = StubAdapter("binance_futures", market_type="futures", listed={"BTC/USDT"})
    registry = {"binance": binance_spot, "binance_futures": binance_fut}
    strategy = StubStrategy("sf_v1", "spot_futures_basis")

    matrix = UniverseMatrix()
    await matrix.build(exchange_registry=registry, strategy_registry=[strategy])

    entry = matrix.get_entry("sf_v1", "BTC/USDT", "binance", "binance_futures")
    assert entry is not None
    assert entry.required_market_type_a == "spot"
    assert entry.required_market_type_b == "futures"


@pytest.mark.asyncio
async def test_triangular_stores_single_leg_entries():
    """Triangular is intra-exchange → leg_b is None."""
    binance = StubAdapter("binance", listed={"BTC/USDT", "ETH/USDT"})
    registry = {"binance": binance}
    strategy = StubStrategy("tri_v1", "triangular")

    matrix = UniverseMatrix()
    await matrix.build(exchange_registry=registry, strategy_registry=[strategy])

    assert matrix.has_entry("tri_v1", "BTC/USDT", "binance", None)
    entry = matrix.get_entry("tri_v1", "BTC/USDT", "binance", None)
    assert entry is not None
    assert entry.leg_b_exchange is None
    assert entry.required_market_type_b is None


@pytest.mark.asyncio
async def test_rebuild_is_noop_after_first_build():
    """Matrix is immutable for the process lifetime."""
    binance = StubAdapter("binance", listed={"BTC/USDT"})
    bitget = StubAdapter("bitget", listed={"BTC/USDT"})
    registry = {"binance": binance, "bitget": bitget}
    strategy = StubStrategy("ce_v1", "cross_exchange_spot")

    matrix = UniverseMatrix()
    await matrix.build(exchange_registry=registry, strategy_registry=[strategy])
    first_size = matrix.size()

    # Second build must not duplicate or alter entries
    await matrix.build(exchange_registry=registry, strategy_registry=[strategy])
    assert matrix.size() == first_size


@pytest.mark.asyncio
async def test_unknown_strategy_type_is_skipped_quietly():
    """Unknown STRATEGY_TYPE must not crash or emit entries."""
    binance = StubAdapter("binance", listed={"BTC/USDT"})
    registry = {"binance": binance}
    strategy = StubStrategy("mystery_v1", "does_not_exist")

    matrix = UniverseMatrix()
    await matrix.build(exchange_registry=registry, strategy_registry=[strategy])

    assert matrix.size() == 0
    assert not matrix.has_entry("mystery_v1", "BTC/USDT", "binance", None)
