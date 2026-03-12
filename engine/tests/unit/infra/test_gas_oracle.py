"""Tests for engine/src/infra/dex/gas_oracle.py — US-086.

Covers:
  - Chain enum (6 members, named values)
  - GasPrice dataclass fields + is_stale property
  - FALLBACK_GAS / SWAP_GAS_UNITS / NATIVE_PRICE_USD constants
  - GasOracle: init, supported_chains, get_gas_price (cache hit/miss/no-rpc/rpc-error),
    get_cached, get_estimated_swap_cost
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.infra.dex.gas_oracle import (
    Chain,
    GasOracle,
    GasPrice,
    FALLBACK_GAS,
    SWAP_GAS_UNITS,
    NATIVE_PRICE_USD,
)


# ---------------------------------------------------------------------------
# 1-2. Chain enum
# ---------------------------------------------------------------------------


class TestChainEnum:
    def test_ethereum_polygon_solana_exist(self):
        """ETHEREUM, POLYGON, SOLANA are defined in Chain enum."""
        assert Chain.ETHEREUM is not None
        assert Chain.POLYGON is not None
        assert Chain.SOLANA is not None

    def test_total_six_chains(self):
        """Chain enum contains exactly 6 members."""
        assert len(Chain) == 6


# ---------------------------------------------------------------------------
# 3-5. GasPrice dataclass
# ---------------------------------------------------------------------------


class TestGasPrice:
    def test_default_construction_stores_fields(self):
        """GasPrice stores chain, gas_price_gwei, and estimated_cost_usd."""
        gp = GasPrice(
            chain=Chain.ETHEREUM,
            gas_price_gwei=30.0,
            estimated_cost_usd=15.0,
        )
        assert gp.chain == Chain.ETHEREUM
        assert gp.gas_price_gwei == 30.0
        assert gp.estimated_cost_usd == 15.0

    def test_is_stale_returns_true_when_timestamp_is_zero(self):
        """is_stale is True when timestamp=0 (epoch origin — always old)."""
        gp = GasPrice(
            chain=Chain.ETHEREUM,
            gas_price_gwei=30.0,
            estimated_cost_usd=15.0,
            timestamp=0,
        )
        assert gp.is_stale is True

    def test_is_stale_returns_false_when_timestamp_is_now(self):
        """is_stale is False when timestamp equals current time."""
        gp = GasPrice(
            chain=Chain.ETHEREUM,
            gas_price_gwei=30.0,
            estimated_cost_usd=15.0,
            timestamp=time.time(),
        )
        assert gp.is_stale is False


# ---------------------------------------------------------------------------
# 6-8. FALLBACK_GAS constant
# ---------------------------------------------------------------------------


class TestFallbackGas:
    def test_all_chains_have_fallback(self):
        """Every Chain member has an entry in FALLBACK_GAS."""
        for chain in Chain:
            assert chain in FALLBACK_GAS, f"Missing fallback for {chain}"

    def test_ethereum_fallback_values(self):
        """Ethereum fallback: gas_price_gwei=30.0, estimated_cost_usd=15.0."""
        eth_fb = FALLBACK_GAS[Chain.ETHEREUM]
        assert eth_fb.gas_price_gwei == pytest.approx(30.0)
        assert eth_fb.estimated_cost_usd == pytest.approx(15.0)

    def test_solana_fallback_cost_is_minimal(self):
        """Solana fallback: estimated_cost_usd=0.01 (cheap compute fees)."""
        sol_fb = FALLBACK_GAS[Chain.SOLANA]
        assert sol_fb.estimated_cost_usd == pytest.approx(0.01)


# ---------------------------------------------------------------------------
# 9-11. GasOracle basic instantiation
# ---------------------------------------------------------------------------


class TestGasOracleInit:
    def test_default_construction_requires_no_arguments(self):
        """GasOracle() can be constructed without passing rpc_urls."""
        oracle = GasOracle()
        assert oracle is not None

    def test_supported_chains_returns_six_chains(self):
        """supported_chains lists all 6 Chain members."""
        oracle = GasOracle()
        chains = oracle.supported_chains
        assert len(chains) == 6
        for c in Chain:
            assert c in chains

    def test_get_estimated_swap_cost_uses_fallback_when_cache_is_empty(self):
        """get_estimated_swap_cost (sync) returns fallback USD when cache is empty."""
        oracle = GasOracle()
        cost = oracle.get_estimated_swap_cost(Chain.ETHEREUM)
        assert cost == pytest.approx(FALLBACK_GAS[Chain.ETHEREUM].estimated_cost_usd)


# ---------------------------------------------------------------------------
# 12-15. get_gas_price — cache and RPC behaviour
# ---------------------------------------------------------------------------


class TestGetGasPrice:
    async def test_cache_hit_returns_cached_value_within_30_seconds(self):
        """Fresh cache entry (age < CACHE_TTL=30 s) is returned without RPC."""
        oracle = GasOracle()
        fresh = GasPrice(
            chain=Chain.POLYGON,
            gas_price_gwei=100.0,
            estimated_cost_usd=0.05,
            timestamp=time.time(),
        )
        oracle._cache[Chain.POLYGON] = fresh

        result = await oracle.get_gas_price(Chain.POLYGON)
        assert result is fresh

    async def test_expired_cache_falls_back_when_no_rpc_configured(self):
        """Stale cache (60 s old, > CACHE_TTL) causes fallback when no RPC set."""
        oracle = GasOracle()
        stale = GasPrice(
            chain=Chain.ETHEREUM,
            gas_price_gwei=50.0,
            estimated_cost_usd=99.0,
            timestamp=time.time() - 60,
        )
        oracle._cache[Chain.ETHEREUM] = stale

        result = await oracle.get_gas_price(Chain.ETHEREUM)
        # Stale cache + no RPC → fallback conservative values
        assert result.gas_price_gwei == pytest.approx(30.0)
        assert result.estimated_cost_usd == pytest.approx(15.0)

    async def test_no_rpc_configured_returns_fallback(self):
        """When rpc_urls is empty, fallback GasPrice is returned."""
        oracle = GasOracle(rpc_urls={})
        result = await oracle.get_gas_price(Chain.SOLANA)
        assert result.estimated_cost_usd == pytest.approx(0.01)

    async def test_rpc_error_falls_back_to_fallback_values(self):
        """When _fetch_from_rpc raises, fallback is returned instead."""
        oracle = GasOracle(rpc_urls={Chain.ETHEREUM: "http://localhost:8545"})
        with patch.object(
            oracle,
            "_fetch_from_rpc",
            new=AsyncMock(side_effect=RuntimeError("connection refused")),
        ):
            result = await oracle.get_gas_price(Chain.ETHEREUM)

        assert result.estimated_cost_usd == pytest.approx(
            FALLBACK_GAS[Chain.ETHEREUM].estimated_cost_usd
        )


# ---------------------------------------------------------------------------
# 16-17. get_cached
# ---------------------------------------------------------------------------


class TestGetCached:
    def test_returns_none_when_no_cache_entry(self):
        """get_cached returns None when the chain has no cached entry."""
        oracle = GasOracle()
        assert oracle.get_cached(Chain.BASE) is None

    def test_returns_cached_value_after_setting(self):
        """get_cached returns the same GasPrice object placed in the cache."""
        oracle = GasOracle()
        gp = GasPrice(
            chain=Chain.ARBITRUM,
            gas_price_gwei=0.1,
            estimated_cost_usd=0.50,
        )
        oracle._cache[Chain.ARBITRUM] = gp
        assert oracle.get_cached(Chain.ARBITRUM) is gp


# ---------------------------------------------------------------------------
# 18-19. SWAP_GAS_UNITS / NATIVE_PRICE_USD
# ---------------------------------------------------------------------------


class TestConstants:
    def test_ethereum_swap_gas_units_is_150000(self):
        """Ethereum swap requires exactly 150,000 gas units."""
        assert SWAP_GAS_UNITS[Chain.ETHEREUM] == 150_000

    def test_native_price_usd_exists_for_all_chains(self):
        """NATIVE_PRICE_USD has an entry for every Chain member."""
        for chain in Chain:
            assert chain in NATIVE_PRICE_USD, f"Missing native price for {chain}"
