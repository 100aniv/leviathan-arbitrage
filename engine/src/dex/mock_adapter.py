"""US-242: MockDEXAdapter for Shadow mode testing.

Provides a mock DEX adapter that derives prices from CEX mid-prices
with configurable random spread.  Conforms to the DEXAdapter protocol
defined in src/strategies/cex_dex.py.

Usage:
    Set SHADOW_MOCK_DEX=true in engine/.env to enable.
    When DEX_RPC_URL is unset and SHADOW_MOCK_DEX=true, Engine._build_dex_adapter()
    returns a MockDEXAdapter instance.
"""
from __future__ import annotations

import random
from decimal import Decimal
from typing import Any


class MockDEXAdapter:
    """Shadow test DEX adapter — CEX mid-price based pricing + configurable spread.

    Implements the DEXAdapter protocol (src/strategies/cex_dex.py) without
    any on-chain dependencies.
    """

    def __init__(
        self,
        books: dict[str, dict[str, Any]] | None = None,
        spread_pct_min: float = 0.01,
        spread_pct_max: float = 0.05,
        gas_cost_usd: float = 0.10,
        default_reserves: tuple[float, float] = (1_000_000.0, 1_000_000.0),
    ) -> None:
        self._books = books or {}
        self._spread_min = spread_pct_min
        self._spread_max = spread_pct_max
        self._gas_cost = gas_cost_usd
        self._default_reserves = default_reserves

    @property
    def pool_address(self) -> str:
        """Mock pool address for shadow mode."""
        return "0xMOCK_SHADOW_DEX_POOL"

    @property
    def dex_id(self) -> str:
        """DEX identifier."""
        return "mock_dex"

    def set_books(self, books: dict[str, dict[str, Any]]) -> None:
        """Update CEX orderbook reference (called by ShadowMode on each tick)."""
        self._books = books

    async def get_pool_price(self, token_in: str, token_out: str) -> Decimal:
        """Return mock pool price derived from CEX mid-price with random spread.

        Looks up the CEX orderbook for {token_in}/{token_out} and applies
        a random spread between spread_pct_min and spread_pct_max.
        """
        symbol = f"{token_in}/{token_out}"
        mid_price = self._get_cex_mid(symbol)
        if mid_price is None:
            # Fallback: try reverse pair
            reverse_symbol = f"{token_out}/{token_in}"
            reverse_mid = self._get_cex_mid(reverse_symbol)
            if reverse_mid is not None and float(reverse_mid) > 0:
                mid_price = Decimal("1") / reverse_mid
            else:
                # No CEX data — return a reasonable default
                return Decimal("0")

        # Apply random spread (1-5% by default)
        spread = Decimal(str(random.uniform(self._spread_min, self._spread_max)))
        direction = random.choice([Decimal("1"), Decimal("-1")])
        return mid_price * (Decimal("1") + direction * spread)

    async def estimate_gas(self, size: Decimal) -> Decimal:
        """Return estimated gas cost in USD (Arbitrum L2 typical)."""
        return Decimal(str(self._gas_cost))

    async def get_pool_reserves(self) -> tuple[Decimal, Decimal]:
        """Return deep liquidity mock reserves."""
        return (
            Decimal(str(self._default_reserves[0])),
            Decimal(str(self._default_reserves[1])),
        )

    def _get_cex_mid(self, symbol: str) -> Decimal | None:
        """Extract mid-price from CEX orderbook cache."""
        sym_books = self._books.get(symbol, {})
        if not sym_books:
            return None

        # Take first available exchange's book
        for _ex_id, book in sym_books.items():
            bid = getattr(book, "best_bid", lambda: None)()
            ask = getattr(book, "best_ask", lambda: None)()
            if bid is not None and ask is not None:
                return (Decimal(str(bid)) + Decimal(str(ask))) / Decimal("2")
        return None
