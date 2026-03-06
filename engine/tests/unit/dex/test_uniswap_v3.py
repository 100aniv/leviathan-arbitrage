"""Tests for UniswapV3Adapter.

Uses mocked Web3 contract calls to verify:
  - Spot price calculation from sqrtPriceX96
  - Gas estimation
  - Virtual reserve computation
  - DEXAdapter Protocol compliance
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.infra.dex.uniswap_v3 import UniswapV3Adapter, UniswapV3Config
from src.strategies.cex_dex import DEXAdapter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# WETH/USDC pool: token0=WETH (18 dec), token1=USDC (6 dec)
# sqrtPriceX96 for price ~3000 USDC/WETH:
#   sqrt(3000 * 10^(6-18)) * 2^96 = sqrt(3000 * 1e-12) * 2^96
#   = sqrt(3e-9) * 2^96 ≈ 5.477e-5 * 7.923e28 ≈ 4.340e24
SQRT_PRICE_X96_3000 = 4_340_530_284_089_024_907_748_352


@pytest.fixture
def config() -> UniswapV3Config:
    return UniswapV3Config(
        rpc_url="http://localhost:8545",
        pool_address="0x8ad599c3A0ff1De082011EFDDc58f1908eb6e6D8",
        token_in_symbol="WETH",
        token_out_symbol="USDC",
        eth_price_usd=Decimal("3000"),
        gas_limit=200_000,
    )


def _make_mock_adapter(config: UniswapV3Config) -> UniswapV3Adapter:
    """Create adapter with mocked Web3 internals."""
    adapter = UniswapV3Adapter(config)

    # Mock pool contract functions
    pool_mock = MagicMock()

    # token0() -> WETH address
    token0_fn = MagicMock()
    token0_fn.call = AsyncMock(return_value="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2")
    pool_mock.functions.token0.return_value = token0_fn

    # token1() -> USDC address
    token1_fn = MagicMock()
    token1_fn.call = AsyncMock(return_value="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48")
    pool_mock.functions.token1.return_value = token1_fn

    # fee() -> 3000 (0.30%)
    fee_fn = MagicMock()
    fee_fn.call = AsyncMock(return_value=3000)
    pool_mock.functions.fee.return_value = fee_fn

    # slot0() -> (sqrtPriceX96, tick, ...)
    slot0_fn = MagicMock()
    slot0_fn.call = AsyncMock(return_value=[SQRT_PRICE_X96_3000, 0, 0, 0, 0, 0, True])
    pool_mock.functions.slot0.return_value = slot0_fn

    # liquidity() -> 10^18 (1 ETH worth of liquidity)
    liquidity_fn = MagicMock()
    liquidity_fn.call = AsyncMock(return_value=10**18)
    pool_mock.functions.liquidity.return_value = liquidity_fn

    adapter._pool = pool_mock

    # Mock token decimal lookups
    async def mock_decimals_call(addr: str) -> int:
        weth = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        return 18 if addr.lower() == weth.lower() else 6

    # Patch the contract creation for ERC20 decimals
    original_w3 = adapter._w3

    def mock_contract(address, abi):
        decimals_fn = MagicMock()
        weth_addr = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        dec = 18 if address.lower() == weth_addr.lower() else 6
        decimals_fn.call = AsyncMock(return_value=dec)
        contract = MagicMock()
        contract.functions.decimals.return_value = decimals_fn
        return contract

    original_w3.eth.contract = mock_contract

    return adapter


# ---------------------------------------------------------------------------
# Protocol Compliance
# ---------------------------------------------------------------------------


class TestDEXAdapterProtocol:
    """Verify UniswapV3Adapter satisfies DEXAdapter Protocol."""

    def test_is_dex_adapter(self, config: UniswapV3Config) -> None:
        adapter = _make_mock_adapter(config)
        assert isinstance(adapter, DEXAdapter)

    def test_pool_address(self, config: UniswapV3Config) -> None:
        adapter = _make_mock_adapter(config)
        assert adapter.pool_address == config.pool_address

    def test_dex_id(self, config: UniswapV3Config) -> None:
        adapter = _make_mock_adapter(config)
        assert adapter.dex_id == "uniswap_v3"


# ---------------------------------------------------------------------------
# Price Calculation
# ---------------------------------------------------------------------------


class TestGetPoolPrice:
    """Test spot price calculation from sqrtPriceX96."""

    async def test_price_approximately_3000(self, config: UniswapV3Config) -> None:
        adapter = _make_mock_adapter(config)
        price = await adapter.get_pool_price("WETH", "USDC")
        # sqrtPriceX96 was set for ~3000 USDC/WETH
        # Allow 10% tolerance due to integer rounding of sqrtPriceX96
        assert Decimal("2700") < price < Decimal("3300"), f"Expected ~3000, got {price}"

    async def test_zero_sqrt_price_returns_zero(self, config: UniswapV3Config) -> None:
        adapter = _make_mock_adapter(config)
        # Override slot0 to return 0
        slot0_fn = MagicMock()
        slot0_fn.call = AsyncMock(return_value=[0, 0, 0, 0, 0, 0, True])
        adapter._pool.functions.slot0.return_value = slot0_fn
        price = await adapter.get_pool_price("WETH", "USDC")
        assert price == Decimal("0")

    async def test_price_is_decimal(self, config: UniswapV3Config) -> None:
        adapter = _make_mock_adapter(config)
        price = await adapter.get_pool_price("WETH", "USDC")
        assert isinstance(price, Decimal)


# ---------------------------------------------------------------------------
# Gas Estimation
# ---------------------------------------------------------------------------


class TestEstimateGas:
    """Test gas cost estimation."""

    async def test_gas_cost_positive(self, config: UniswapV3Config) -> None:
        adapter = _make_mock_adapter(config)
        # gas_price is now handled as plain value (non-awaitable) via adapter code
        adapter._w3.eth = MagicMock(gas_price=30 * 10**9)
        gas = await adapter.estimate_gas(Decimal("1.0"))
        # 200_000 gas * 30 gwei * $3000/ETH = 200000 * 30e-9 * 3000 = $18
        assert gas > Decimal("0")
        assert isinstance(gas, Decimal)

    async def test_gas_cost_calculation(self, config: UniswapV3Config) -> None:
        adapter = _make_mock_adapter(config)
        # 50 gwei gas price
        adapter._w3.eth = MagicMock(gas_price=50 * 10**9)
        gas = await adapter.estimate_gas(Decimal("1.0"))
        # 200_000 * 50e-9 ETH * $3000 = $30
        expected = Decimal("200000") * Decimal("50") * Decimal("1e-9") * Decimal("3000")
        assert abs(gas - expected) < Decimal("0.01")

    async def test_gas_fallback_on_error(self, config: UniswapV3Config) -> None:
        adapter = _make_mock_adapter(config)
        # Make gas_price raise
        type(adapter._w3.eth).gas_price = property(lambda self: (_ for _ in ()).throw(Exception("RPC error")))
        gas = await adapter.estimate_gas(Decimal("1.0"))
        # Fallback: 30 gwei
        expected = Decimal("200000") * Decimal("30") * Decimal("1e-9") * Decimal("3000")
        assert abs(gas - expected) < Decimal("0.01")


# ---------------------------------------------------------------------------
# Pool Reserves
# ---------------------------------------------------------------------------


class TestGetPoolReserves:
    """Test virtual reserve computation from liquidity + sqrtPrice."""

    async def test_reserves_positive(self, config: UniswapV3Config) -> None:
        adapter = _make_mock_adapter(config)
        r0, r1 = await adapter.get_pool_reserves()
        assert r0 > Decimal("0")
        assert r1 > Decimal("0")
        assert isinstance(r0, Decimal)
        assert isinstance(r1, Decimal)

    async def test_reserves_zero_on_empty_pool(self, config: UniswapV3Config) -> None:
        adapter = _make_mock_adapter(config)
        # Zero liquidity
        liq_fn = MagicMock()
        liq_fn.call = AsyncMock(return_value=0)
        adapter._pool.functions.liquidity.return_value = liq_fn
        r0, r1 = await adapter.get_pool_reserves()
        assert r0 == Decimal("0")
        assert r1 == Decimal("0")

    async def test_reserves_zero_on_zero_price(self, config: UniswapV3Config) -> None:
        adapter = _make_mock_adapter(config)
        slot0_fn = MagicMock()
        slot0_fn.call = AsyncMock(return_value=[0, 0, 0, 0, 0, 0, True])
        adapter._pool.functions.slot0.return_value = slot0_fn
        r0, r1 = await adapter.get_pool_reserves()
        assert r0 == Decimal("0")
        assert r1 == Decimal("0")


# ---------------------------------------------------------------------------
# Pool Fee
# ---------------------------------------------------------------------------


class TestGetPoolFee:
    """Test pool fee retrieval."""

    async def test_fee_is_30_bps(self, config: UniswapV3Config) -> None:
        adapter = _make_mock_adapter(config)
        fee_bps = await adapter.get_pool_fee_bps()
        assert fee_bps == 30  # 3000 / 100


# ---------------------------------------------------------------------------
# Metadata Caching
# ---------------------------------------------------------------------------


class TestMetadataCaching:
    """Verify that pool metadata is fetched once and cached."""

    async def test_metadata_fetched_once(self, config: UniswapV3Config) -> None:
        adapter = _make_mock_adapter(config)
        # First call fetches metadata
        await adapter.get_pool_price("WETH", "USDC")
        call_count = adapter._pool.functions.token0.call_count
        # Second call uses cache
        await adapter.get_pool_price("WETH", "USDC")
        assert adapter._pool.functions.token0.call_count == call_count
