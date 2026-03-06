"""CEX-DEX Hybrid Arbitrage Strategy.

Exploits price differences between:
  - CEX (centralized exchange) orderbook mid-price
  - DEX (decentralized exchange) AMM pool spot price

Protocol:
  1. Receive signal with CEX bid/ask prices
  2. Fetch DEX pool spot price via DEXAdapter
  3. Compute AMM slippage using constant-product formula (x*y=k)
  4. Estimate gas cost for DEX swap
  5. If |CEX_mid - DEX_spot| > gas_cost + friction_cost + min_edge → submit TradeRequest
  6. Direction: buy on cheaper venue, sell on more expensive venue
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from src.core.models import OrderSide, Signal, Trade
from src.strategies.base import BaseStrategy, CostCalculator, TradeLeg, TradeRequest


# ---------------------------------------------------------------------------
# DEX Adapter Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class DEXAdapter(Protocol):
    """
    Interface for DEX pool adapters (Uniswap V3, Curve, Balancer, etc.).

    Implementors must provide: price lookup, gas estimation, and pool reserves.
    """

    @property
    def pool_address(self) -> str:
        """On-chain pool contract address."""
        ...

    @property
    def dex_id(self) -> str:
        """DEX identifier (e.g. 'uniswap_v3', 'curve_3pool', 'balancer')."""
        ...

    async def get_pool_price(self, token_in: str, token_out: str) -> Decimal:
        """
        Return current pool spot price: units of token_out per 1 token_in.
        E.g. for BTC/USDT: returns USDT per BTC.
        """
        ...

    async def estimate_gas(self, size: Decimal) -> Decimal:
        """
        Return estimated gas cost in USD for a swap of given base-asset size.
        Includes: gas_price * gas_limit, converted to USD at current ETH price.
        """
        ...

    async def get_pool_reserves(self) -> tuple[Decimal, Decimal]:
        """
        Return (reserve_token0, reserve_token1) for AMM constant-product pool.
        token0 = base asset (e.g. WBTC), token1 = quote asset (e.g. USDC).
        """
        ...


# ---------------------------------------------------------------------------
# AMM Slippage Model — Constant Product (x*y=k)
# ---------------------------------------------------------------------------


class AMMSlippageModel:
    """
    Constant-product AMM slippage model: x * y = k.

    Used for Uniswap V2/V3 (concentrated liquidity approximation),
    SushiSwap, and similar x*y=k pools.
    """

    @staticmethod
    def price_impact(
        reserve_in: Decimal,
        reserve_out: Decimal,
        amount_in: Decimal,
    ) -> Decimal:
        """
        Compute price impact fraction for given swap (no fee applied).

          amount_out = reserve_out * amount_in / (reserve_in + amount_in)
          spot_rate  = reserve_out / reserve_in
          effective  = amount_out / amount_in
          impact     = 1 - effective / spot_rate

        Returns fraction in [0, 1]. A 1% impact returns Decimal("0.01").
        """
        if reserve_in <= 0 or reserve_out <= 0 or amount_in <= 0:
            return Decimal("0")
        amount_out = reserve_out * amount_in / (reserve_in + amount_in)
        spot_rate = reserve_out / reserve_in
        effective_rate = amount_out / amount_in
        if spot_rate <= 0:
            return Decimal("0")
        impact = Decimal("1") - effective_rate / spot_rate
        return max(Decimal("0"), impact)

    @staticmethod
    def expected_output(
        reserve_in: Decimal,
        reserve_out: Decimal,
        amount_in: Decimal,
        fee_bps: int = 30,
    ) -> Decimal:
        """
        Compute expected output after AMM fee (constant product formula).

          amount_in_net  = amount_in * (10000 - fee_bps) / 10000
          amount_out     = reserve_out * amount_in_net / (reserve_in + amount_in_net)

        Default fee_bps=30 = 0.30% (Uniswap V2 / SushiSwap standard).
        """
        if reserve_in <= 0 or reserve_out <= 0 or amount_in <= 0:
            return Decimal("0")
        fee_factor = Decimal(str(10000 - fee_bps)) / Decimal("10000")
        amount_in_net = amount_in * fee_factor
        return reserve_out * amount_in_net / (reserve_in + amount_in_net)

    @staticmethod
    def effective_price(
        reserve_in: Decimal,
        reserve_out: Decimal,
        amount_in: Decimal,
        fee_bps: int = 30,
    ) -> Decimal:
        """
        Return effective execution price (token_out per token_in) including AMM fee.
        """
        if amount_in <= 0:
            return Decimal("0")
        out = AMMSlippageModel.expected_output(reserve_in, reserve_out, amount_in, fee_bps)
        return out / amount_in if amount_in > 0 else Decimal("0")


# ---------------------------------------------------------------------------
# CEX-DEX Strategy Config
# ---------------------------------------------------------------------------


class CexDexConfig(BaseModel):
    """Configuration for CexDexStrategy."""

    min_edge_bps: Decimal = Field(default=Decimal("10"), ge=Decimal("0"))
    """Minimum net edge in basis points after all costs."""

    max_position_size: Decimal = Field(default=Decimal("1.0"), gt=Decimal("0"))
    """Maximum position size in base asset units."""

    dex_fee_bps: int = Field(default=30, ge=0)
    """DEX pool swap fee in basis points (Uniswap V3 default = 30)."""

    friction_cost_pct: Decimal = Field(default=Decimal("0.002"), ge=Decimal("0"))
    """Default CEX friction (fee + slippage) as fraction of notional."""


# ---------------------------------------------------------------------------
# CEX-DEX Hybrid Arbitrage Strategy
# ---------------------------------------------------------------------------


class CexDexStrategy(BaseStrategy):
    """
    CEX-DEX Hybrid Arbitrage Strategy.

    Detects and exploits price discrepancies between:
      - A CEX orderbook mid-price (fast, centralized, low latency)
      - A DEX AMM pool spot price (on-chain, gas-gated, higher latency)

    Minimum viable edge:
      net_edge = |cex_mid - dex_spot| / cex_mid - friction_cost_pct - gas_pct
      net_edge must exceed min_edge_bps / 10000 to generate a trade request.

    AMM slippage is modeled via constant-product formula (x*y=k).
    """

    STRATEGY_TYPE = "cex_dex_hybrid"

    def __init__(
        self,
        strategy_id: str,
        cost_calculator: CostCalculator,
        dex_adapter: DEXAdapter,
        cex_exchange_id: str,
        symbol: str,
        config: CexDexConfig | None = None,
    ) -> None:
        super().__init__(strategy_id, cost_calculator)
        self._dex = dex_adapter
        self._cex_exchange_id = cex_exchange_id
        self._symbol = symbol
        self._config = config or CexDexConfig()
        self._amm = AMMSlippageModel()

    @property
    def config(self) -> CexDexConfig:
        return self._config

    async def on_signal(self, signal: Signal) -> Optional[TradeRequest]:
        """
        Process incoming CEX price signal.

        Returns TradeRequest if CEX-DEX spread exceeds:
          friction_cost + gas_cost + min_edge_bps
        Otherwise returns None (signal filtered).
        """
        self._metrics.signals_received += 1

        if not self._is_active:
            self._metrics.signals_filtered += 1
            return None

        # Step 1: Fetch DEX pool spot price
        try:
            tokens = signal.symbol.split("/")
            token_in = tokens[0] if tokens else signal.symbol
            token_out = tokens[1] if len(tokens) > 1 else "USDT"
            dex_price = await self._dex.get_pool_price(token_in, token_out)
        except Exception:
            self._metrics.signals_filtered += 1
            return None

        if dex_price <= Decimal("0"):
            self._metrics.signals_filtered += 1
            return None

        # Step 2: Estimate gas cost as fraction of notional
        cex_mid = (signal.buy_price + signal.sell_price) / Decimal("2")
        notional = cex_mid * signal.volume

        try:
            gas_cost_usd = await self._dex.estimate_gas(signal.volume)
        except Exception:
            gas_cost_usd = Decimal("0")

        gas_pct = gas_cost_usd / notional if notional > 0 else Decimal("0")

        # Step 3: Compute spread and net edge
        raw_spread_pct = abs(cex_mid - dex_price) / cex_mid
        net_edge_pct = raw_spread_pct - self._config.friction_cost_pct - gas_pct
        min_edge_pct = self._config.min_edge_bps / Decimal("10000")

        if net_edge_pct <= min_edge_pct:
            self._metrics.signals_filtered += 1
            return None

        # Step 4: Determine direction
        if cex_mid < dex_price:
            # CEX cheaper → BUY on CEX, SELL on DEX
            cex_side = OrderSide.BUY
            dex_side = OrderSide.SELL
            direction = "buy_cex_sell_dex"
        else:
            # DEX cheaper → SELL on CEX, BUY on DEX
            cex_side = OrderSide.SELL
            dex_side = OrderSide.BUY
            direction = "buy_dex_sell_cex"

        # Step 5: Cap to max position size
        size = min(signal.volume, self._config.max_position_size)

        self._metrics.trade_requests_generated += 1

        return TradeRequest(
            strategy_id=self._strategy_id,
            legs=[
                TradeLeg(
                    exchange_id=self._cex_exchange_id,
                    symbol=self._symbol,
                    side=cex_side,
                    size=size,
                ),
                TradeLeg(
                    exchange_id=self._dex.dex_id,
                    symbol=self._symbol,
                    side=dex_side,
                    size=size,
                    metadata={
                        "dex_pool": self._dex.pool_address,
                        "gas_cost_usd": str(gas_cost_usd),
                        "dex_fee_bps": str(self._config.dex_fee_bps),
                    },
                ),
            ],
            expected_profit_usdt=notional * net_edge_pct,
            confidence=min(1.0, float(net_edge_pct / min_edge_pct) * 0.5),
            metadata={
                "direction": direction,
                "cex_mid": str(cex_mid),
                "dex_price": str(dex_price),
                "raw_spread_pct": str(raw_spread_pct),
                "gas_pct": str(gas_pct),
                "net_edge_pct": str(net_edge_pct),
            },
        )

    async def on_fill(self, trade: Trade) -> None:
        """Handle fill and update metrics."""
        await super().on_fill(trade)

    def compute_amm_output(
        self,
        reserve_in: Decimal,
        reserve_out: Decimal,
        amount_in: Decimal,
        fee_bps: int | None = None,
    ) -> Decimal:
        """
        Compute expected AMM swap output (constant product, with fee).
        Convenience wrapper for AMM model access from tests/callers.
        """
        return self._amm.expected_output(
            reserve_in, reserve_out, amount_in,
            fee_bps if fee_bps is not None else self._config.dex_fee_bps,
        )

    def compute_price_impact(
        self,
        reserve_in: Decimal,
        reserve_out: Decimal,
        amount_in: Decimal,
    ) -> Decimal:
        """Compute AMM price impact fraction (0–1) for given swap size."""
        return self._amm.price_impact(reserve_in, reserve_out, amount_in)
