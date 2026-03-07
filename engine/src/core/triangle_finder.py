"""Triangle path discovery for triangular arbitrage.

Builds a directed currency exchange-rate graph from live pair data.
Enumerates all 3-cycles and returns those whose gross profit exceeds
the configured minimum threshold.

Algorithm:
    For each pair BASE/QUOTE with bid/ask:
      - Sell leg  BASE → QUOTE: effective rate = bid
      - Buy  leg  QUOTE → BASE: effective rate = 1 / ask

    For every triple (A, B, C) of distinct currencies:
      profit = rate(A→B) * rate(B→C) * rate(C→A) - 1
      If profit > min_profit_pct → profitable triangle detected.

Complexity: O(n³) in number of currencies, suitable for a single
exchange with O(100) traded assets.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class PairRate:
    """Bid/ask snapshot for a single trading pair."""

    base: str
    quote: str
    bid: Decimal   # best bid — rate received when selling base
    ask: Decimal   # best ask — rate paid when buying base (in quote per base)


@dataclass
class TrianglePath:
    """A detected triangular arbitrage cycle."""

    currencies: list[str]       # [A, B, C] — the three currencies
    pairs: list[str]            # trading pair symbol for each leg
    sides: list[str]            # "buy" or "sell" for each leg
    rates: list[Decimal]        # effective exchange rate per leg
    gross_profit_pct: Decimal   # gross profit fraction (e.g. 0.005 == 0.5%)


# Internal type alias: graph[from][to] = (rate, pair_symbol, side)
_Edge = tuple[Decimal, str, str]


class TriangleFinder:
    """
    Discovers triangular arbitrage paths on a single exchange.

    Usage:
        finder = TriangleFinder(min_profit_pct=Decimal("0.001"))
        paths = finder.find_triangles(rates)   # list[TrianglePath], sorted desc
    """

    def __init__(self, min_profit_pct: Decimal = Decimal("0.001")) -> None:
        self._min_profit_pct = min_profit_pct
        self._graph: dict[str, dict[str, _Edge]] = {}

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def currencies(self) -> set[str]:
        """Return the set of currencies currently in the rate graph."""
        return set(self._graph.keys())

    def update(self, rates: list[PairRate]) -> None:
        """Rebuild the rate graph from a fresh snapshot of pair rates."""
        self._graph = {}
        for pr in rates:
            b, q = pr.base, pr.quote
            # Selling BASE → QUOTE: rate = bid price
            self._add_edge(b, q, pr.bid, f"{b}/{q}", "sell")
            # Buying BASE with QUOTE: rate = 1/ask (quote → base)
            if pr.ask > Decimal("0"):
                self._add_edge(q, b, Decimal("1") / pr.ask, f"{b}/{q}", "buy")

    def find_triangles(
        self, rates: list[PairRate] | None = None
    ) -> list[TrianglePath]:
        """
        Find all profitable 3-cycles.

        If *rates* is provided the internal graph is updated first.
        Returns paths sorted by gross_profit_pct descending.
        """
        if rates is not None:
            self.update(rates)

        paths: list[TrianglePath] = []
        seen: set[frozenset[str]] = set()
        nodes = list(self._graph.keys())

        for a in nodes:
            neighbors_a = self._graph.get(a, {})
            for b, edge_ab in neighbors_a.items():
                if b == a:
                    continue
                neighbors_b = self._graph.get(b, {})
                for c, edge_bc in neighbors_b.items():
                    if c == a or c == b:
                        continue
                    edge_ca = self._graph.get(c, {}).get(a)
                    if edge_ca is None:
                        continue

                    rate_ab, pair_ab, side_ab = edge_ab
                    rate_bc, pair_bc, side_bc = edge_bc
                    rate_ca, pair_ca, side_ca = edge_ca

                    product = rate_ab * rate_bc * rate_ca
                    gross_profit_pct = product - Decimal("1")

                    if gross_profit_pct <= self._min_profit_pct:
                        continue

                    # Deduplicate by unordered currency set
                    key: frozenset[str] = frozenset([a, b, c])
                    if key in seen:
                        continue
                    seen.add(key)

                    paths.append(
                        TrianglePath(
                            currencies=[a, b, c],
                            pairs=[pair_ab, pair_bc, pair_ca],
                            sides=[side_ab, side_bc, side_ca],
                            rates=[rate_ab, rate_bc, rate_ca],
                            gross_profit_pct=gross_profit_pct,
                        )
                    )

        paths.sort(key=lambda p: p.gross_profit_pct, reverse=True)
        return paths

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _add_edge(
        self, frm: str, to: str, rate: Decimal, pair: str, side: str
    ) -> None:
        """Insert or replace edge if the new rate is more favourable."""
        if frm not in self._graph:
            self._graph[frm] = {}
        existing = self._graph[frm].get(to)
        if existing is None or rate > existing[0]:
            self._graph[frm][to] = (rate, pair, side)
