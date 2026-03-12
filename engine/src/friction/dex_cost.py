"""DEX cost calculator — US-087.

DEX 거래 비용: LP fee + gas + MEV 추정 + bridge cost.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class DEXCost:
    """DEX 거래 비용 상세."""
    lp_fee: Decimal           # LP fee (e.g., Uniswap 0.3% = 30bps)
    gas_cost_usd: Decimal     # 가스비 (USD)
    mev_cost_bps: Decimal     # MEV 추정 (2-5 bps)
    bridge_cost_usd: Decimal  # 브릿지 비용 (cross-chain)
    total_cost_usd: Decimal   # 총 비용 (USD)
    total_cost_bps: Decimal   # 총 비용 (bps, notional 대비)


# DEX LP fee tiers (Uniswap V3)
LP_FEE_TIERS: dict[int, Decimal] = {
    100: Decimal("0.0001"),    # 1 bps (stable pairs)
    500: Decimal("0.0005"),    # 5 bps
    3000: Decimal("0.003"),    # 30 bps (standard)
    10000: Decimal("0.01"),    # 100 bps (exotic)
}

# MEV 추정 범위 (bps)
MEV_ESTIMATE_BPS = Decimal("3")  # 중간값 3 bps

# Bridge 비용 (USD, 체인별)
BRIDGE_COST_USD: dict[str, Decimal] = {
    "ethereum_polygon": Decimal("5.0"),
    "ethereum_arbitrum": Decimal("3.0"),
    "ethereum_optimism": Decimal("3.0"),
    "ethereum_base": Decimal("3.0"),
    "polygon_ethereum": Decimal("5.0"),
    "same_chain": Decimal("0"),
}


class DEXCostCalculator:
    """DEX 비용 계산기.

    LP fee + gas + MEV + bridge = total cost.
    """

    def __init__(
        self,
        gas_oracle: Any | None = None,
        mev_estimate_bps: Decimal = MEV_ESTIMATE_BPS,
    ) -> None:
        self._gas_oracle = gas_oracle
        self._mev_bps = mev_estimate_bps

    def calculate(
        self,
        notional_usd: Decimal,
        fee_tier: int = 3000,
        gas_cost_usd: Decimal | None = None,
        source_chain: str = "ethereum",
        dest_chain: str = "ethereum",
    ) -> DEXCost:
        """DEX 비용 계산.

        Parameters:
            notional_usd: 거래 규모 (USD)
            fee_tier: Uniswap V3 fee tier (100/500/3000/10000)
            gas_cost_usd: 가스비 직접 지정 (None이면 oracle에서 조회)
            source_chain: 출발 체인
            dest_chain: 도착 체인
        """
        # LP fee
        lp_rate = LP_FEE_TIERS.get(fee_tier, Decimal("0.003"))
        lp_fee = notional_usd * lp_rate

        # Gas cost
        if gas_cost_usd is not None:
            gas = gas_cost_usd
        elif self._gas_oracle is not None:
            from src.infra.dex.gas_oracle import Chain
            chain_map = {
                "ethereum": Chain.ETHEREUM,
                "polygon": Chain.POLYGON,
                "arbitrum": Chain.ARBITRUM,
                "optimism": Chain.OPTIMISM,
                "base": Chain.BASE,
                "solana": Chain.SOLANA,
            }
            chain = chain_map.get(source_chain)
            gas = Decimal(str(self._gas_oracle.get_estimated_swap_cost(chain))) if chain else Decimal("15")
        else:
            gas = Decimal("15")  # fallback

        # MEV
        mev = notional_usd * self._mev_bps / Decimal("10000")

        # Bridge
        bridge_key = f"{source_chain}_{dest_chain}" if source_chain != dest_chain else "same_chain"
        bridge = BRIDGE_COST_USD.get(bridge_key, Decimal("5.0"))

        # Total
        total_usd = lp_fee + gas + mev + bridge
        total_bps = (total_usd / notional_usd * Decimal("10000")) if notional_usd > 0 else Decimal("0")

        return DEXCost(
            lp_fee=lp_fee,
            gas_cost_usd=gas,
            mev_cost_bps=self._mev_bps,
            bridge_cost_usd=bridge,
            total_cost_usd=total_usd,
            total_cost_bps=total_bps,
        )
