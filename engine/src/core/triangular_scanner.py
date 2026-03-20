"""Triangular arbitrage scanner using Bellman-Ford negative cycle detection.

For each exchange's orderbook snapshot, builds a directed graph with
-log(rate) edge weights, then uses Bellman-Ford to detect profitable
3-currency cycles.

Algorithm:
    - Nodes = currencies (USDT, BTC, ETH, etc.)
    - Edges for pair BASE/QUOTE:
        - BASE→QUOTE (sell): weight = -log(bid)
        - QUOTE→BASE (buy):  weight = -log(1/ask) = log(ask)
    - Negative cycle ⟺ product of rates > 1 ⟺ profitable triangle
    - Only 3-currency cycles (from/to USDT) are returned
    - Depth-aware: walks orderbook levels to find bottleneck volume
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from src.core.order_book import OrderBook

if TYPE_CHECKING:
    from src.strategies.base import CostCalculator

_ENABLE_TRIANGULAR_COST = os.environ.get("ENABLE_TRIANGULAR_COST", "false").lower() == "true"


@dataclass
class TriangleCycle:
    """A detected triangular arbitrage opportunity on a single exchange."""

    exchange_id: str
    path: list[str]           # currencies traversed: ["USDT", "BTC", "ETH"]
    pairs: list[str]          # trading pair per leg: ["BTC/USDT", "ETH/BTC", "ETH/USDT"]
    sides: list[str]          # "buy" or "sell" per leg
    prices: list[Decimal]     # effective price per leg (ask for buy, bid for sell)
    profit_pct: Decimal       # net profit fraction (0.001 = 0.1%)
    max_volume_usdt: Decimal  # bottleneck volume in USDT terms


# Internal: (from_currency, to_currency) → (symbol, side)
_EdgeInfo = dict[tuple[str, str], tuple[str, str]]
# Internal: (from_currency, to_currency) → log-weight
_Weights = dict[tuple[str, str], float]


class TriangularScanner:
    """
    Detects 3-currency triangular arbitrage cycles on a single exchange.

    Maintains a per-exchange orderbook cache.  On every update, rebuilds
    the -log(rate) graph and enumerates all USDT→X→Y→USDT 3-hop cycles
    whose total log-weight is negative (= profitable cycle).

    Usage::
        scanner = TriangularScanner(min_profit_bps=Decimal("10"))
        cycles = scanner.on_orderbook_update(exchange_id, symbol, book)
    """

    def __init__(
        self,
        min_profit_bps: Decimal = Decimal("10"),
        cost_calculator: Optional["CostCalculator"] = None,
        min_volume_usdt: Decimal = Decimal("0"),
    ) -> None:
        self._min_profit_bps = min_profit_bps
        self._cost_calculator = cost_calculator
        self._min_volume_usdt = min_volume_usdt
        # exchange_id → symbol → OrderBook
        self._books: dict[str, dict[str, OrderBook]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on_orderbook_update(
        self,
        exchange_id: str,
        symbol: str,
        book: OrderBook,
    ) -> list[TriangleCycle]:
        """Cache the updated orderbook and return any newly-detected cycles."""
        if exchange_id not in self._books:
            self._books[exchange_id] = {}
        self._books[exchange_id][symbol] = book

        weights, info = self._build_graph(exchange_id)
        if "USDT" not in {frm for frm, _ in weights}:
            return []

        paths = self._detect_negative_cycles(weights)
        results: list[TriangleCycle] = []
        for path in paths:
            cycle = self._build_cycle(exchange_id, path, info)
            if cycle is not None:
                results.append(cycle)
        return results

    # ------------------------------------------------------------------
    # Graph construction
    # ------------------------------------------------------------------

    def _build_graph(
        self, exchange_id: str
    ) -> tuple[_Weights, _EdgeInfo]:
        """Build directed graph with -log(rate) weights.

        Returns:
            weights  – (from, to) → float  (-log of effective rate)
            info     – (from, to) → (symbol, side)
        """
        books = self._books.get(exchange_id, {})
        weights: _Weights = {}
        info: _EdgeInfo = {}

        _ref_notional = Decimal("100")  # reference USDT notional for fee estimation
        _max_levels = 5

        for symbol, book in books.items():
            # Strip futures suffix: "BTC/USDT:USDT" → "BTC/USDT"
            clean = symbol.split(":")[0]
            if "/" not in clean:
                continue
            parts = clean.split("/")
            if len(parts) != 2:
                continue
            base, quote = parts

            bid = book.best_bid()
            ask = book.best_ask()
            if bid is None or ask is None or bid <= 0 or ask <= 0:
                continue

            bid_f = float(bid)
            ask_f = float(ask)
            if bid_f <= 0.0 or ask_f <= 0.0:
                continue  # guard against Decimal→float underflow

            # Hard prune: check depth for sell side (BASE→QUOTE)
            sell_depth_usdt = self._edge_depth_usdt(book, "sell", bid, _max_levels)
            if self._min_volume_usdt > 0 and sell_depth_usdt < self._min_volume_usdt:
                pass  # prune sell edge — don't add
            else:
                # Sell BASE → QUOTE: weight = -log(bid) + log(1 + fee_rate)
                fee_rate_sell = self._estimate_fee_rate(
                    exchange_id, symbol, "sell", bid, _ref_notional
                )
                weights[(base, quote)] = -math.log(bid_f) - math.log(1.0 - fee_rate_sell) if fee_rate_sell < 1.0 else -math.log(bid_f)
                info[(base, quote)] = (symbol, "sell")

            # Hard prune: check depth for buy side (QUOTE→BASE)
            buy_depth_usdt = self._edge_depth_usdt(book, "buy", ask, _max_levels)
            if self._min_volume_usdt > 0 and buy_depth_usdt < self._min_volume_usdt:
                pass  # prune buy edge — don't add
            else:
                # Buy BASE with QUOTE: weight = log(ask) - log(1 - fee_rate)
                fee_rate_buy = self._estimate_fee_rate(
                    exchange_id, symbol, "buy", ask, _ref_notional
                )
                weights[(quote, base)] = math.log(ask_f) + (-math.log(1.0 - fee_rate_buy) if fee_rate_buy < 1.0 else 0.0)
                info[(quote, base)] = (symbol, "buy")

        return weights, info

    def _edge_depth_usdt(
        self,
        book: OrderBook,
        side: str,
        price: Decimal,
        max_levels: int,
    ) -> Decimal:
        """Return available depth in USDT for a single edge."""
        total_qty = Decimal("0")
        if side == "sell":
            for p in sorted(book.bids.keys(), reverse=True)[:max_levels]:
                total_qty += book.bids[p]
        else:
            for p in sorted(book.asks.keys())[:max_levels]:
                total_qty += book.asks[p]
        return total_qty * price if total_qty > 0 else Decimal("0")

    def _estimate_fee_rate(
        self,
        exchange_id: str,
        symbol: str,
        side: str,
        price: Decimal,
        ref_notional: Decimal,
    ) -> float:
        """Return fee as a fraction of notional. 0.0 if cost_calculator absent or disabled."""
        if not _ENABLE_TRIANGULAR_COST or self._cost_calculator is None:
            return 0.0
        from src.core.models import OrderSide
        order_side = OrderSide.BUY if side == "buy" else OrderSide.SELL
        size = ref_notional / price if price > 0 else Decimal("1")
        try:
            cost = self._cost_calculator.estimate_cost(
                exchange_id=exchange_id,
                symbol=symbol,
                side=order_side,
                size=size,
                price=price,
            )
            return float(cost / ref_notional) if ref_notional > 0 else 0.0
        except Exception:
            return 0.0

    # ------------------------------------------------------------------
    # Negative cycle detection (Bellman-Ford enumeration of 3-hop paths)
    # ------------------------------------------------------------------

    def _detect_negative_cycles(self, weights: _Weights) -> list[list[str]]:
        """Enumerate all profitable 3-currency cycles via USDT.

        Finds every USDT→B→C→USDT path where the total -log weight is
        negative (product of rates > 1).  Deduplicates by currency set.
        """
        # Build adjacency for fast lookup: node → {neighbor: weight}
        adj: dict[str, dict[str, float]] = {}
        for (frm, to), w in weights.items():
            adj.setdefault(frm, {})[to] = w

        if "USDT" not in adj:
            return []

        cycles: list[list[str]] = []
        seen: set[frozenset[str]] = set()

        for b, w_ub in adj.get("USDT", {}).items():
            if b == "USDT":
                continue
            for c, w_bc in adj.get(b, {}).items():
                if c in ("USDT", b):
                    continue
                w_cu = adj.get(c, {}).get("USDT")
                if w_cu is None:
                    continue
                if w_ub + w_bc + w_cu >= 0:
                    continue  # not a negative cycle

                key: frozenset[str] = frozenset(["USDT", b, c])
                if key in seen:
                    continue
                seen.add(key)
                cycles.append(["USDT", b, c])

        return cycles

    # ------------------------------------------------------------------
    # Depth-aware profit calculation
    # ------------------------------------------------------------------

    def _build_cycle(
        self,
        exchange_id: str,
        path: list[str],
        info: _EdgeInfo,
    ) -> Optional[TriangleCycle]:
        """Construct a TriangleCycle from a detected path, if profitable.

        Computes exact decimal profit and depth-aware bottleneck volume.
        Returns None if profit falls below min_profit_bps.
        """
        if len(path) != 3:
            return None

        a, b, c = path
        books = self._books.get(exchange_id, {})

        # Resolve each leg
        legs: list[tuple[str, str]] = [
            info.get((a, b), ("", "")),
            info.get((b, c), ("", "")),
            info.get((c, a), ("", "")),
        ]
        for sym, side in legs:
            if not sym or not side:
                return None

        prices: list[Decimal] = []
        for sym, side in legs:
            book = books.get(sym)
            if book is None:
                return None
            price = book.best_ask() if side == "buy" else book.best_bid()
            if price is None or price <= 0:
                return None
            prices.append(price)

        # Cycle return: USDT → B → C → USDT
        #   Leg 1 (buy B with USDT): rate = 1/ask
        #   Leg 2 (buy/sell B for C): depends on side
        #   Leg 3 (sell C for USDT): rate = bid
        def rate(price: Decimal, side: str) -> Decimal:
            if side == "buy":
                return Decimal("1") / price
            return price

        cycle_return = rate(prices[0], legs[0][1]) * rate(prices[1], legs[1][1]) * rate(prices[2], legs[2][1])
        profit_pct = cycle_return - Decimal("1")

        min_profit = self._min_profit_bps / Decimal("10000")
        if profit_pct <= min_profit:
            return None

        # Depth-aware bottleneck volume
        max_volume_usdt = self._depth_bottleneck_usdt(books, legs, prices)

        return TriangleCycle(
            exchange_id=exchange_id,
            path=list(path),
            pairs=[legs[0][0].split(":")[0], legs[1][0].split(":")[0], legs[2][0].split(":")[0]],
            sides=[legs[0][1], legs[1][1], legs[2][1]],
            prices=prices,
            profit_pct=profit_pct,
            max_volume_usdt=max_volume_usdt,
        )

    def _depth_bottleneck_usdt(
        self,
        books: dict[str, OrderBook],
        legs: list[tuple[str, str]],
        prices: list[Decimal],
        max_levels: int = 5,
    ) -> Decimal:
        """Return bottleneck USDT volume across all legs (min of available depth)."""
        min_usdt = Decimal("999999999")

        for i, (sym, side) in enumerate(legs):
            book = books.get(sym)
            if book is None:
                return Decimal("0")

            # Sum top N levels
            total_qty = Decimal("0")
            if side == "buy":
                for p in sorted(book.asks.keys())[:max_levels]:
                    total_qty += book.asks[p]
            else:
                for p in sorted(book.bids.keys(), reverse=True)[:max_levels]:
                    total_qty += book.bids[p]

            if total_qty <= 0:
                return Decimal("0")

            # Approximate USDT value: qty × price for first leg, qty for others
            price = prices[i]
            if price > 0:
                # If quote is USDT (leg buys with USDT or sells for USDT)
                vol_usdt = total_qty * price
            else:
                vol_usdt = total_qty

            if vol_usdt < min_usdt:
                min_usdt = vol_usdt

        return min_usdt if min_usdt < Decimal("999999999") else Decimal("0")
