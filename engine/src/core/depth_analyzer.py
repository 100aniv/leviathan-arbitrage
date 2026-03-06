"""DepthAnalyzer — orderbook depth analysis (VWAP, liquidity at price levels)."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.core.order_book import OrderBook


@dataclass
class VWAPResult:
    vwap: Decimal
    filled_qty: Decimal
    levels_consumed: int


class DepthAnalyzer:
    """
    Static methods for orderbook depth analysis.

    vwap_for_buy: walks asks ascending to simulate a market buy.
    vwap_for_sell: walks bids descending to simulate a market sell.
    liquidity_at_pct_depth: total qty available within N% of best price.
    """

    @staticmethod
    def vwap_for_buy(book: OrderBook, size: Decimal) -> VWAPResult:
        """
        Walk asks ascending to compute VWAP for a buy order of given size.

        Raises ValueError if insufficient ask liquidity.
        """
        remaining = size
        notional = Decimal("0")
        levels = 0

        for price in sorted(book.asks.keys()):
            if remaining <= 0:
                break
            available = book.asks[price]
            fill = min(available, remaining)
            notional += fill * price
            remaining -= fill
            levels += 1

        filled = size - remaining
        if filled == 0:
            raise ValueError("Insufficient ask liquidity to fill order")

        if remaining > 0:
            raise ValueError(
                f"Insufficient ask liquidity: needed {size}, filled {filled}"
            )

        return VWAPResult(vwap=notional / filled, filled_qty=filled, levels_consumed=levels)

    @staticmethod
    def vwap_for_sell(book: OrderBook, size: Decimal) -> VWAPResult:
        """
        Walk bids descending to compute VWAP for a sell order of given size.

        Raises ValueError if insufficient bid liquidity.
        """
        remaining = size
        notional = Decimal("0")
        levels = 0

        for price in sorted(book.bids.keys(), reverse=True):
            if remaining <= 0:
                break
            available = book.bids[price]
            fill = min(available, remaining)
            notional += fill * price
            remaining -= fill
            levels += 1

        filled = size - remaining
        if filled == 0:
            raise ValueError("Insufficient bid liquidity to fill order")

        if remaining > 0:
            raise ValueError(
                f"Insufficient bid liquidity: needed {size}, filled {filled}"
            )

        return VWAPResult(vwap=notional / filled, filled_qty=filled, levels_consumed=levels)

    @staticmethod
    def liquidity_at_pct_depth(book: OrderBook, pct: Decimal, side: str) -> Decimal:
        """
        Total available quantity within pct% of the best price on the given side.

        Args:
            book: The orderbook.
            pct:  Percentage depth (e.g., Decimal("1") = 1%).
            side: "bid" or "ask".

        Returns Decimal("0") if the book is empty on that side.
        Raises ValueError for invalid side.
        """
        if side == "bid":
            best = book.best_bid()
            if best is None:
                return Decimal("0")
            threshold = best * (1 - pct / 100)
            return sum(
                qty for price, qty in book.bids.items() if price >= threshold
            )
        elif side == "ask":
            best = book.best_ask()
            if best is None:
                return Decimal("0")
            threshold = best * (1 + pct / 100)
            return sum(
                qty for price, qty in book.asks.items() if price <= threshold
            )
        else:
            raise ValueError(f"Invalid side '{side}': must be 'bid' or 'ask'")
