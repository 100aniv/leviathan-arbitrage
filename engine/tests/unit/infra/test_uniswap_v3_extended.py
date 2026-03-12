"""Tests for UniswapV3Adapter — US-088.

Covers:
  - Adapter construction with mocked Web3
  - estimate_slippage: normal, large trade, zero liquidity, exception, cap
  - get_spot_price / _get_liquidity method existence
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.infra.dex.uniswap_v3 import UniswapV3Adapter, UniswapV3Config


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_w3():
    """Patch AsyncWeb3 and AsyncHTTPProvider so no network calls occur."""
    with patch("src.infra.dex.uniswap_v3.AsyncHTTPProvider"), \
         patch("src.infra.dex.uniswap_v3.AsyncWeb3") as MockW3:
        mock_instance = MagicMock()
        MockW3.return_value = mock_instance
        mock_instance.to_checksum_address.side_effect = lambda addr: addr
        mock_instance.eth.contract.return_value = MagicMock()
        yield mock_instance


@pytest.fixture
def adapter(mock_w3):
    config = UniswapV3Config(
        rpc_url="http://mock-rpc",
        pool_address="0x" + "a" * 40,
    )
    return UniswapV3Adapter(config)


# ---------------------------------------------------------------------------
# 1. Construction
# ---------------------------------------------------------------------------


class TestUniswapV3AdapterInit:
    def test_adapter_created_with_mocked_web3(self, adapter):
        """UniswapV3Adapter is created without errors when Web3 is mocked."""
        assert adapter is not None

    def test_pool_address_stored(self, adapter):
        """pool_address property returns the configured address."""
        assert adapter.pool_address == "0x" + "a" * 40

    def test_dex_id_is_uniswap_v3(self, adapter):
        """dex_id property returns 'uniswap_v3'."""
        assert adapter.dex_id == "uniswap_v3"


# ---------------------------------------------------------------------------
# 2-6. estimate_slippage
# ---------------------------------------------------------------------------


class TestEstimateSlippage:
    @pytest.mark.asyncio
    async def test_normal_case_returns_slippage_bps(self, adapter):
        """Normal liquidity and trade size → positive slippage bps calculated."""
        # liquidity_usd = 1e24 * 1 / 1e18 = 1_000_000
        # slippage = 100 / (2 * 1_000_000) * 10_000 = 0.5 bps
        with patch.object(adapter, "_get_liquidity", new=AsyncMock(return_value=10**24)), \
             patch.object(adapter, "get_spot_price", new=AsyncMock(return_value=Decimal("1"))):
            result = await adapter.estimate_slippage(Decimal("100"))
        assert result == Decimal("0.5")

    @pytest.mark.asyncio
    async def test_larger_trade_produces_larger_slippage(self, adapter):
        """Larger trade size against same liquidity yields higher slippage."""
        with patch.object(adapter, "_get_liquidity", new=AsyncMock(return_value=10**24)), \
             patch.object(adapter, "get_spot_price", new=AsyncMock(return_value=Decimal("1"))):
            small = await adapter.estimate_slippage(Decimal("100"))
            large = await adapter.estimate_slippage(Decimal("10000"))
        assert large > small

    @pytest.mark.asyncio
    async def test_zero_liquidity_returns_fallback_10bps(self, adapter):
        """Liquidity == 0 triggers the fallback return of 10 bps."""
        with patch.object(adapter, "_get_liquidity", new=AsyncMock(return_value=0)), \
             patch.object(adapter, "get_spot_price", new=AsyncMock(return_value=Decimal("1"))):
            result = await adapter.estimate_slippage(Decimal("100"))
        assert result == Decimal("10")

    @pytest.mark.asyncio
    async def test_exception_in_liquidity_fetch_returns_fallback_10bps(self, adapter):
        """Exception during _get_liquidity call returns fallback 10 bps."""
        with patch.object(
            adapter, "_get_liquidity", new=AsyncMock(side_effect=Exception("RPC error"))
        ):
            result = await adapter.estimate_slippage(Decimal("100"))
        assert result == Decimal("10")

    @pytest.mark.asyncio
    async def test_slippage_capped_at_100bps(self, adapter):
        """Tiny liquidity with large trade size → slippage capped at 100 bps."""
        # liquidity_usd = 1 * 1 / 1e18 ≈ 0 → slippage >> 100 → capped
        with patch.object(adapter, "_get_liquidity", new=AsyncMock(return_value=1)), \
             patch.object(adapter, "get_spot_price", new=AsyncMock(return_value=Decimal("1"))):
            result = await adapter.estimate_slippage(Decimal("1000000"))
        assert result == Decimal("100")


# ---------------------------------------------------------------------------
# 7-8. Method existence
# ---------------------------------------------------------------------------


class TestMethodExistence:
    def test_get_spot_price_method_exists(self, adapter):
        """get_spot_price is a callable method on UniswapV3Adapter."""
        assert hasattr(adapter, "get_spot_price")
        assert callable(adapter.get_spot_price)

    def test_get_liquidity_method_exists(self, adapter):
        """_get_liquidity is a callable method on UniswapV3Adapter."""
        assert hasattr(adapter, "_get_liquidity")
        assert callable(adapter._get_liquidity)
