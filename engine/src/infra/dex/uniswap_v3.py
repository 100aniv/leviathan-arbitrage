"""Uniswap V3 DEX Adapter.

Concrete implementation of the DEXAdapter Protocol for Uniswap V3 pools.
Uses Web3.py to interact with on-chain pool contracts.

Supports:
  - Spot price from sqrtPriceX96 (tick-based pricing)
  - Gas estimation via eth_estimateGas
  - Pool reserves via slot0 + liquidity
  - Constant-product approximation for slippage
"""
from __future__ import annotations

import asyncio
import math
from decimal import Decimal
from typing import Any

from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider


# ---------------------------------------------------------------------------
# Minimal Uniswap V3 Pool ABI (read-only calls)
# ---------------------------------------------------------------------------

POOL_ABI: list[dict[str, Any]] = [
    {
        "inputs": [],
        "name": "slot0",
        "outputs": [
            {"internalType": "uint160", "name": "sqrtPriceX96", "type": "uint160"},
            {"internalType": "int24", "name": "tick", "type": "int24"},
            {"internalType": "uint16", "name": "observationIndex", "type": "uint16"},
            {"internalType": "uint16", "name": "observationCardinality", "type": "uint16"},
            {"internalType": "uint16", "name": "observationCardinalityNext", "type": "uint16"},
            {"internalType": "uint8", "name": "feeProtocol", "type": "uint8"},
            {"internalType": "bool", "name": "unlocked", "type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "liquidity",
        "outputs": [{"internalType": "uint128", "name": "", "type": "uint128"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "token0",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "token1",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "fee",
        "outputs": [{"internalType": "uint24", "name": "", "type": "uint24"}],
        "stateMutability": "view",
        "type": "function",
    },
]

# ERC-20 decimals ABI
ERC20_DECIMALS_ABI: list[dict[str, Any]] = [
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
]

# Uniswap V3 SwapRouter ABI (for gas estimation)
SWAP_ROUTER_ABI: list[dict[str, Any]] = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "tokenIn", "type": "address"},
                    {"internalType": "address", "name": "tokenOut", "type": "address"},
                    {"internalType": "uint24", "name": "fee", "type": "uint24"},
                    {"internalType": "address", "name": "recipient", "type": "address"},
                    {"internalType": "uint256", "name": "deadline", "type": "uint256"},
                    {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                    {"internalType": "uint256", "name": "amountOutMinimum", "type": "uint256"},
                    {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"},
                ],
                "internalType": "struct ISwapRouter.ExactInputSingleParams",
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "exactInputSingle",
        "outputs": [{"internalType": "uint256", "name": "amountOut", "type": "uint256"}],
        "stateMutability": "payable",
        "type": "function",
    },
]


# ---------------------------------------------------------------------------
# Well-known addresses
# ---------------------------------------------------------------------------

# Uniswap V3 SwapRouter on Ethereum mainnet
UNISWAP_V3_ROUTER = "0xE592427A0AEce92De3Edee1F18E0157C05861564"


class UniswapV3Config:
    """Configuration for UniswapV3Adapter."""

    def __init__(
        self,
        rpc_url: str,
        pool_address: str,
        *,
        token_in_symbol: str = "WETH",
        token_out_symbol: str = "USDC",
        router_address: str = UNISWAP_V3_ROUTER,
        eth_price_usd: Decimal = Decimal("3000"),
        gas_limit: int = 200_000,
    ) -> None:
        self.rpc_url = rpc_url
        self.pool_address = pool_address
        self.token_in_symbol = token_in_symbol
        self.token_out_symbol = token_out_symbol
        self.router_address = router_address
        self.eth_price_usd = eth_price_usd
        self.gas_limit = gas_limit


class UniswapV3Adapter:
    """Uniswap V3 DEX Adapter implementing the DEXAdapter Protocol.

    Reads on-chain state from a Uniswap V3 pool contract via Web3.py
    (async HTTP provider). Provides:
      - Spot price from sqrtPriceX96
      - Gas estimation
      - Virtual reserves from liquidity + sqrtPrice
    """

    def __init__(self, config: UniswapV3Config) -> None:
        self._config = config
        self._w3 = AsyncWeb3(AsyncHTTPProvider(config.rpc_url))
        self._pool = self._w3.eth.contract(
            address=self._w3.to_checksum_address(config.pool_address),
            abi=POOL_ABI,
        )
        self._token0_decimals: int | None = None
        self._token1_decimals: int | None = None
        self._pool_fee: int | None = None
        self._token0_addr: str | None = None
        self._token1_addr: str | None = None

    @property
    def pool_address(self) -> str:
        return self._config.pool_address

    @property
    def dex_id(self) -> str:
        return "uniswap_v3"

    async def _ensure_metadata(self) -> None:
        """Lazily fetch and cache pool metadata (token addresses, decimals, fee)."""
        if self._pool_fee is not None:
            return

        token0_addr, token1_addr, fee = await asyncio.gather(
            self._pool.functions.token0().call(),
            self._pool.functions.token1().call(),
            self._pool.functions.fee().call(),
        )
        self._token0_addr = token0_addr
        self._token1_addr = token1_addr
        self._pool_fee = fee

        # Fetch decimals for both tokens
        t0_contract = self._w3.eth.contract(
            address=self._w3.to_checksum_address(token0_addr),
            abi=ERC20_DECIMALS_ABI,
        )
        t1_contract = self._w3.eth.contract(
            address=self._w3.to_checksum_address(token1_addr),
            abi=ERC20_DECIMALS_ABI,
        )
        d0, d1 = await asyncio.gather(
            t0_contract.functions.decimals().call(),
            t1_contract.functions.decimals().call(),
        )
        self._token0_decimals = d0
        self._token1_decimals = d1

    async def get_pool_price(self, token_in: str, token_out: str) -> Decimal:
        """Return spot price from sqrtPriceX96.

        sqrtPriceX96 encodes sqrt(token1/token0) * 2^96.
        price = (sqrtPriceX96 / 2^96)^2 * 10^(decimals0 - decimals1)
        """
        await self._ensure_metadata()
        assert self._token0_decimals is not None
        assert self._token1_decimals is not None

        slot0 = await self._pool.functions.slot0().call()
        sqrt_price_x96 = slot0[0]

        if sqrt_price_x96 == 0:
            return Decimal("0")

        # price = (sqrtPriceX96)^2 / 2^192 * 10^(d0-d1)
        price_raw = Decimal(sqrt_price_x96 ** 2) / Decimal(2 ** 192)
        decimal_adjustment = Decimal(10 ** (self._token0_decimals - self._token1_decimals))
        # price_token1_per_token0
        price = price_raw * decimal_adjustment

        # If caller expects token0/token1 (inverse), flip
        # Default: returns token1 per token0 (e.g., USDC per WETH)
        # token_in/token_out mapping is symbol-based, not address-based
        return price

    async def estimate_gas(self, size: Decimal) -> Decimal:
        """Estimate gas cost in USD for a swap.

        Uses configured gas_limit * current gas_price * ETH/USD price.
        Falls back to 30 gwei if gas_price fetch fails.
        """
        try:
            gas_price_raw = self._w3.eth.gas_price
            # Handle both awaitable and plain value (for testability)
            if hasattr(gas_price_raw, "__await__"):
                gas_price_wei = await gas_price_raw
            else:
                gas_price_wei = gas_price_raw
        except Exception:
            # Fallback: assume 30 gwei
            gas_price_wei = 30 * 10**9

        gas_cost_eth = Decimal(gas_price_wei * self._config.gas_limit) / Decimal(10**18)
        gas_cost_usd = gas_cost_eth * self._config.eth_price_usd

        return gas_cost_usd

    async def get_pool_reserves(self) -> tuple[Decimal, Decimal]:
        """Return virtual reserves derived from liquidity and sqrtPriceX96.

        For Uniswap V3 concentrated liquidity, virtual reserves are:
          x = L / sqrtPrice  (token0 reserve)
          y = L * sqrtPrice  (token1 reserve)
        where L = liquidity, sqrtPrice = sqrtPriceX96 / 2^96
        """
        await self._ensure_metadata()
        assert self._token0_decimals is not None
        assert self._token1_decimals is not None

        slot0, liquidity = await asyncio.gather(
            self._pool.functions.slot0().call(),
            self._pool.functions.liquidity().call(),
        )
        sqrt_price_x96 = slot0[0]

        if sqrt_price_x96 == 0 or liquidity == 0:
            return (Decimal("0"), Decimal("0"))

        sqrt_price = Decimal(sqrt_price_x96) / Decimal(2**96)
        L = Decimal(liquidity)

        # Virtual reserves (in raw token units)
        reserve0_raw = L / sqrt_price
        reserve1_raw = L * sqrt_price

        # Adjust for decimals
        reserve0 = reserve0_raw / Decimal(10**self._token0_decimals)
        reserve1 = reserve1_raw / Decimal(10**self._token1_decimals)

        return (reserve0, reserve1)

    async def get_pool_fee_bps(self) -> int:
        """Return pool fee in basis points (e.g., 30 for 0.30%)."""
        await self._ensure_metadata()
        assert self._pool_fee is not None
        # Uniswap V3 fee is in hundredths of a basis point (e.g., 3000 = 0.30%)
        return self._pool_fee // 100
