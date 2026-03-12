"""Real-time gas price oracle — US-086.

Ethereum/Polygon/Solana 가스비 조회 + 30초 캐시 + fallback.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class Chain(str, Enum):
    ETHEREUM = "ethereum"
    POLYGON = "polygon"
    SOLANA = "solana"
    ARBITRUM = "arbitrum"
    OPTIMISM = "optimism"
    BASE = "base"


@dataclass
class GasPrice:
    """가스비 정보."""
    chain: Chain
    gas_price_gwei: float       # Gwei (EVM) or lamports (Solana)
    max_fee_gwei: float = 0.0   # EIP-1559 maxFeePerGas
    priority_fee_gwei: float = 0.0  # EIP-1559 maxPriorityFeePerGas
    estimated_cost_usd: float = 0.0  # 스왑 1건 예상 비용 (USD)
    timestamp: float = 0.0      # 조회 시각

    @property
    def is_stale(self) -> bool:
        return (time.time() - self.timestamp) > 60  # 60초 이상 경과


# 가스비 조회 실패 시 fallback 고정값 (보수적)
FALLBACK_GAS: dict[Chain, GasPrice] = {
    Chain.ETHEREUM: GasPrice(
        chain=Chain.ETHEREUM, gas_price_gwei=30.0,
        max_fee_gwei=50.0, priority_fee_gwei=2.0,
        estimated_cost_usd=15.0,
    ),
    Chain.POLYGON: GasPrice(
        chain=Chain.POLYGON, gas_price_gwei=50.0,
        max_fee_gwei=80.0, priority_fee_gwei=30.0,
        estimated_cost_usd=0.05,
    ),
    Chain.SOLANA: GasPrice(
        chain=Chain.SOLANA, gas_price_gwei=0.000005,
        estimated_cost_usd=0.01,
    ),
    Chain.ARBITRUM: GasPrice(
        chain=Chain.ARBITRUM, gas_price_gwei=0.1,
        estimated_cost_usd=0.50,
    ),
    Chain.OPTIMISM: GasPrice(
        chain=Chain.OPTIMISM, gas_price_gwei=0.01,
        estimated_cost_usd=0.30,
    ),
    Chain.BASE: GasPrice(
        chain=Chain.BASE, gas_price_gwei=0.01,
        estimated_cost_usd=0.20,
    ),
}

# 스왑 가스 사용량 (units)
SWAP_GAS_UNITS: dict[Chain, int] = {
    Chain.ETHEREUM: 150_000,
    Chain.POLYGON: 200_000,
    Chain.ARBITRUM: 800_000,
    Chain.OPTIMISM: 150_000,
    Chain.BASE: 150_000,
    Chain.SOLANA: 200_000,  # compute units
}

# 네이티브 토큰 USD 가격 fallback
NATIVE_PRICE_USD: dict[Chain, float] = {
    Chain.ETHEREUM: 3000.0,
    Chain.POLYGON: 0.50,
    Chain.SOLANA: 150.0,
    Chain.ARBITRUM: 3000.0,  # ETH
    Chain.OPTIMISM: 3000.0,  # ETH
    Chain.BASE: 3000.0,      # ETH
}


class GasOracle:
    """실시간 가스비 오라클.

    30초 캐시 + RPC 조회 + fallback 고정값.
    Web3 lazy import ([dex] optional dep).
    """

    CACHE_TTL_SECONDS = 30

    def __init__(self, rpc_urls: dict[Chain, str] | None = None) -> None:
        self._rpc_urls = rpc_urls or {}
        self._cache: dict[Chain, GasPrice] = {}
        self._providers: dict[Chain, Any] = {}

    def _get_web3(self, chain: Chain) -> Any | None:
        """Lazy import web3 + 프로바이더 생성."""
        if chain in self._providers:
            return self._providers[chain]

        url = self._rpc_urls.get(chain)
        if not url:
            return None

        try:
            from web3 import Web3
            if chain == Chain.SOLANA:
                return None  # Solana는 별도 클라이언트
            provider = Web3(Web3.HTTPProvider(url))
            self._providers[chain] = provider
            return provider
        except ImportError:
            logger.warning("web3 not installed. Install with: pip install leviathan-engine[dex]")
            return None

    async def get_gas_price(self, chain: Chain) -> GasPrice:
        """가스비 조회 (캐시 → RPC → fallback).

        Parameters:
            chain: 블록체인 네트워크
        Returns:
            GasPrice 정보
        """
        # 1. 캐시 확인
        cached = self._cache.get(chain)
        if cached and (time.time() - cached.timestamp) < self.CACHE_TTL_SECONDS:
            return cached

        # 2. RPC 조회 시도
        try:
            gas = await self._fetch_from_rpc(chain)
            if gas is not None:
                gas.timestamp = time.time()
                self._cache[chain] = gas
                return gas
        except Exception as exc:
            logger.warning("gas_oracle: RPC failed for %s: %s", chain.value, exc)

        # 3. Fallback
        fallback = FALLBACK_GAS.get(chain, GasPrice(chain=chain, gas_price_gwei=50.0, estimated_cost_usd=10.0))
        fallback.timestamp = time.time()
        self._cache[chain] = fallback
        logger.info("gas_oracle: using fallback for %s (%.2f gwei, $%.2f)",
                    chain.value, fallback.gas_price_gwei, fallback.estimated_cost_usd)
        return fallback

    async def _fetch_from_rpc(self, chain: Chain) -> GasPrice | None:
        """RPC에서 가스비 조회."""
        w3 = self._get_web3(chain)
        if w3 is None:
            return None

        loop = asyncio.get_event_loop()

        if chain == Chain.SOLANA:
            return None  # Solana RPC 별도 구현 필요

        # EVM chains
        gas_price_wei = await loop.run_in_executor(None, w3.eth.gas_price)
        gas_price_gwei = float(gas_price_wei) / 1e9

        # EIP-1559 시도
        max_fee = gas_price_gwei
        priority_fee = 2.0
        try:
            latest = await loop.run_in_executor(None, lambda: w3.eth.get_block("latest"))
            base_fee_wei = getattr(latest, "baseFeePerGas", 0) or 0
            base_fee_gwei = float(base_fee_wei) / 1e9
            max_fee = base_fee_gwei * 2 + priority_fee
        except Exception:
            pass

        # 예상 비용 (USD)
        gas_units = SWAP_GAS_UNITS.get(chain, 150_000)
        native_price = NATIVE_PRICE_USD.get(chain, 3000.0)
        estimated_cost = gas_price_gwei * gas_units / 1e9 * native_price

        return GasPrice(
            chain=chain,
            gas_price_gwei=gas_price_gwei,
            max_fee_gwei=max_fee,
            priority_fee_gwei=priority_fee,
            estimated_cost_usd=estimated_cost,
        )

    def get_cached(self, chain: Chain) -> GasPrice | None:
        """캐시된 가스비 반환 (없으면 None)."""
        return self._cache.get(chain)

    def get_estimated_swap_cost(self, chain: Chain) -> float:
        """스왑 예상 비용 USD (캐시 → fallback)."""
        cached = self._cache.get(chain)
        if cached:
            return cached.estimated_cost_usd
        fallback = FALLBACK_GAS.get(chain)
        return fallback.estimated_cost_usd if fallback else 10.0

    @property
    def supported_chains(self) -> list[Chain]:
        return list(Chain)
