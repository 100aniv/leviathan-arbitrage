# US-019-022 Architecture Review: Signal Production (Phase B-3)

**Reviewer**: Winter (architect) | **Date**: 2026-03-08
**GAPs Resolved**: GAP 7 (TriangularScanner), GAP 3 (MultiStrategySignalProducer Paper-only), GAP 2 (SignalGenerator cross_exchange-only)

---

## Executive Summary

Phase B-3 connects four US items that together unlock 5 of the 6 inactive strategies for
real-data signal production. The current architecture has all the signal evaluation logic
hardcoded inside `shadow.py:559-916` as private methods (`_evaluate_triangular`,
`_evaluate_spot_futures`, etc.). This review recommends extracting that logic into a
`RealDataSignalProducer` class, adding a proper `TriangularScanner` with Bellman-Ford,
and wiring both into ShadowMode without disrupting the existing cross_exchange flow.

---

## US-019: TriangularScanner (Bellman-Ford)

### Current State

The triangular evaluation at `shadow.py:578-643` is hardcoded with a single path
(`USDT->BTC->ETH`) and uses direct best_ask/best_bid comparisons rather than graph-based
cycle detection. This means:

1. Only 1 triangle is checked, while an exchange with N symbols has O(N^2) potential pairs
   and O(N^3) triangles
2. No depth-aware sizing -- uses `MultiSignalConfig.default_notional_usd` ($500) regardless
   of available liquidity at each leg
3. No systematic negative cycle detection -- misses profitable cycles that emerge dynamically

### Decision D1: Graph Representation -- Adjacency List with -log(rate) Edges

**Recommended**: Directed adjacency list `dict[str, list[Edge]]` where each `Edge` stores
`(target_currency, -log(rate), pair_symbol, side, available_qty)`.

```python
@dataclass
class Edge:
    target: str             # target currency node
    weight: float           # -log(rate), where rate is best executable price
    pair: str               # e.g. "BTC/USDT"
    side: str               # "buy" or "sell"
    rate: Decimal           # actual exchange rate
    available_qty: Decimal  # depth at best level
```

Graph construction from orderbooks:

- For pair `BTC/USDT` with `best_ask=90000`:
  - Edge `USDT -> BTC`: weight = `-log(1/90000)` = `log(90000)`, rate = `1/ask`
  - Edge `BTC -> USDT`: weight = `-log(90000)` = ... wait, we use bid for selling:
    Edge `BTC -> USDT`: weight = `-log(bid)`, rate = `bid`

- For pair `ETH/BTC` with `best_ask=0.054`:
  - Edge `BTC -> ETH`: weight = `-log(1/0.054)`, rate = `1/ask`
  - Edge `ETH -> BTC`: weight = `-log(bid)`, rate = `bid`

A negative-weight cycle in this graph means the product of rates around the cycle > 1.0,
which is a profitable arbitrage.

**Rejected alternatives**:

| Alternative | Why Rejected |
|------------|--------------|
| Adjacency matrix (Floyd-Warshall) | O(V^3) per update. With ~50 currencies on Binance, that is 125K operations per orderbook update. Adjacency list + Bellman-Ford is O(V*E), typically 50*100 = 5K operations. |
| NetworkX DiGraph | External dependency, no Decimal support, Python overhead. We need <1ms per scan. |
| Brute-force all 3-permutations | O(V^3) with no early termination. Bellman-Ford converges faster on typical sparse currency graphs. |

### Decision D2: Bellman-Ford vs Floyd-Warshall

**Recommended**: Bellman-Ford with USDT source node.

**Rationale**:

| Criterion | Bellman-Ford | Floyd-Warshall |
|-----------|-------------|----------------|
| Complexity | O(V*E), V~50 currencies, E~100 pairs = **5K ops** | O(V^3) = **125K ops** |
| Incremental update | Re-run from source on edge weight change -- **O(V*E)** | Must recompute full matrix -- **O(V^3)** |
| Negative cycle detection | After V-1 rounds, check for improvement -- **native** | Requires diagonal check -- **native** |
| All-pairs shortest | No (single source) | Yes |
| Implementation effort | ~80 lines | ~60 lines |
| Memory | O(V+E) | O(V^2) |

Bellman-Ford is the better choice because:
1. We only need cycles reachable from USDT (our settlement currency)
2. Incremental update on each orderbook change is cheaper
3. We need cycle *detection* (does a profitable cycle exist?) more than cycle *enumeration*

**Important caveat**: After detecting a negative cycle, we must extract the actual cycle
path by tracing the predecessor array. Standard Bellman-Ford gives `predecessor[v]`, so
we follow back from the relaxed vertex until we loop.

### Decision D3: Depth-Aware Sizing

The maximum tradeable volume through a cycle is constrained by the **minimum available
liquidity** across all legs, converted to a common denomination.

```
max_volume_usdt = min(
    leg1_qty * leg1_rate_to_usdt,
    leg2_qty * leg2_rate_to_usdt,
    leg3_qty * leg3_rate_to_usdt,
)
```

Where `leg_qty` comes from the top-of-book level captured in the `Edge.available_qty` field.
For more aggressive sizing, walk deeper into the book and recompute cycle profitability
at each depth level (diminishing returns due to price impact).

**Recommendation**: Start with top-of-book depth only. Walking the book adds complexity
and the marginal volume is small for triangular arb (sub-second opportunities).

### Decision D4: Integration with Orderbook Data Flow

The `TriangularScanner` should maintain its own graph, updated on each orderbook callback:

```python
class TriangularScanner:
    def __init__(self, exchange_id: str, min_profit_bps: Decimal = Decimal("10")):
        self._exchange_id = exchange_id
        self._graph: dict[str, list[Edge]] = {}
        self._pairs: dict[str, OrderBook] = {}  # pair -> OrderBook
        self._currencies: set[str] = set()

    def on_orderbook(self, symbol: str, book: OrderBook) -> list[TriangleCycle]:
        """Update graph edge for this pair, then scan for negative cycles.
        Returns list of profitable cycles found (may be empty).
        """
```

One `TriangularScanner` instance per exchange (triangular arb is single-exchange).
ShadowMode creates one per connected exchange during `start()`.

### Interface Contract: TriangularScanner

```python
@dataclass
class TriangleCycle:
    exchange_id: str
    path: list[str]          # ["USDT", "BTC", "ETH"]
    pairs: list[str]         # ["BTC/USDT", "ETH/BTC", "ETH/USDT"]
    sides: list[str]         # ["buy", "buy", "sell"]
    rates: list[Decimal]     # execution rate per leg
    profit_pct: Decimal      # net cycle return - 1.0
    max_volume_usdt: Decimal  # depth-constrained max size

class TriangularScanner:
    def __init__(self, exchange_id: str, min_profit_bps: Decimal): ...
    def on_orderbook(self, symbol: str, book: OrderBook) -> list[TriangleCycle]: ...
    def get_currencies(self) -> set[str]: ...
    def get_edge_count(self) -> int: ...
```

### Performance Estimate

With ~50 currencies and ~100 pairs on Binance:
- Graph update: O(1) per edge (2 edges per pair) -- negligible
- Bellman-Ford: O(V*E) = 50 * 200 = 10K operations -- **<0.5ms in Python**
- Per orderbook update: 1 graph update + 1 Bellman-Ford = **<1ms total**

This is acceptable for the ~100 orderbook updates/second we see in shadow mode.

### Risk: False Positives from Stale Books

If pair A/B updates but pair B/C is 500ms old, the triangle may appear profitable
but evaporate by execution time. Mitigation:

- Check `book.last_update_time` for all 3 legs; reject if any > 5s stale
- This reuses the same staleness pattern from `signal.py:138-148`

---

## US-020: RealDataSignalProducer

### Current State (GAP 3 and GAP 2)

**GAP 3**: `MultiStrategySignalProducer` (`multi_signal.py:60-398`) is a "dumb publisher" --
it has `produce_*_signal()` methods that accept pre-computed parameters and publish Signal
objects to Redis Streams. It does NOT compute signals from raw orderbook data. The actual
computation logic lives in `shadow.py:559-1037` as private methods of `ShadowMode`.

**GAP 2**: `SignalGenerator` (`signal.py:38-219`) handles ONLY `cross_exchange_spot`
signals. It has no mechanism to produce spot_futures, triangular, funding_rate, etc.

### Decision D5: New Class vs Extend MultiStrategySignalProducer

**Recommended**: Create a NEW class `RealDataSignalProducer` that:
1. Owns the signal computation logic (extracted from `shadow.py`)
2. Delegates to `MultiStrategySignalProducer` for publishing
3. Owns a `TriangularScanner` per exchange

**Rejected alternatives**:

| Alternative | Why Rejected |
|------------|--------------|
| Extend `MultiStrategySignalProducer` | Violates SRP. MSSP is a publisher/serializer. Adding orderbook analysis makes it a God class. |
| Extend `SignalGenerator` | `SignalGenerator` is tightly coupled to cross_exchange via `PriceHub` and `CostCalculator`. Adding 6 more strategies would bloat it beyond maintainability. |
| Keep logic in `ShadowMode` | Current state. 400+ lines of strategy logic in an orchestrator class. ShadowMode should orchestrate, not compute signals. |
| One producer class per strategy | Too many classes (6+). The strategies share common patterns (orderbook lookup, dedup, publish). A single coordinator is cleaner. |

### Decision D6: RealDataSignalProducer vs PaperSignalSimulator

These serve fundamentally different purposes:

| Aspect | PaperSignalSimulator | RealDataSignalProducer |
|--------|---------------------|----------------------|
| Data source | Random number generator | Real orderbooks from WebSocket collectors |
| Purpose | Test signal->strategy->execution pipeline | Production signal generation |
| Accuracy | Synthetic (fake basis/funding/triangle) | Real market microstructure |
| Mode | Paper trading only | Shadow + Live |
| Location | `multi_signal.py:401-509` | New file: `core/real_signal_producer.py` |

`PaperSignalSimulator` remains unchanged. `RealDataSignalProducer` replaces the inline
evaluation methods currently in `shadow.py`.

### Interface Contract: RealDataSignalProducer

```python
class RealDataSignalProducer:
    """Produces trading signals from real orderbook + funding rate data.

    Owns the computation logic for:
      - triangular (via TriangularScanner, Bellman-Ford)
      - spot_futures (spot vs futures basis)
      - futures_futures (cross-exchange futures spread)
      - funding_rate (funding rate differential)
      - statistical_arb (z-score on cross-exchange spread)
      - latency_arb (exchange update delay)

    Does NOT own cross_exchange -- that stays in SignalGenerator.
    """

    def __init__(
        self,
        publisher: MultiStrategySignalProducer,
        config: RealDataSignalConfig | None = None,
    ) -> None: ...

    async def on_orderbook_update(
        self,
        exchange_id: str,
        symbol: str,
        book: OrderBook,
        all_spot_books: dict[str, dict[str, OrderBook]],
        all_futures_books: dict[str, dict[str, OrderBook]],
    ) -> list[Signal]:
        """Evaluate all applicable strategies for this orderbook update.
        Returns list of signals produced (may be empty).
        """

    async def on_funding_rates_update(
        self,
        rates: dict[str, dict[str, float]],  # exchange -> symbol -> rate
        spot_books: dict[str, dict[str, OrderBook]],
    ) -> list[Signal]:
        """Evaluate funding rate arb when new rates arrive."""

    def set_futures_exchanges(self, exchanges: set[str]) -> None:
        """Configure which exchange_ids are futures (for spot/futures routing)."""

    def set_disabled_strategies(self, strategies: set[str]) -> None:
        """Disable specific strategies (e.g. stat_arb, latency_arb in shadow)."""
```

### What Moves Where

| Current Location | Method | Destination |
|-----------------|--------|-------------|
| `shadow.py:578-643` | `_evaluate_triangular` | `RealDataSignalProducer._evaluate_triangular` (uses TriangularScanner) |
| `shadow.py:645-746` | `_evaluate_statistical_arb` | `RealDataSignalProducer._evaluate_statistical_arb` |
| `shadow.py:748-814` | `_evaluate_latency_arb` | `RealDataSignalProducer._evaluate_latency_arb` |
| `shadow.py:816-859` | `_evaluate_spot_futures` | `RealDataSignalProducer._evaluate_spot_futures` |
| `shadow.py:861-915` | `_evaluate_futures_futures` | `RealDataSignalProducer._evaluate_futures_futures` |
| `shadow.py:990-1037` | `_evaluate_funding_rate_arb` | `RealDataSignalProducer._evaluate_funding_rate_arb` |
| `shadow.py:559-576` | `_evaluate_multi_strategies` | `RealDataSignalProducer.on_orderbook_update` (orchestration) |

**State that moves with the methods**:

| State | Current Location | Notes |
|-------|-----------------|-------|
| `_spread_history` | `shadow.py:229` | Rolling z-score window for stat_arb |
| `_exchange_update_times` | `shadow.py:233` | Latency tracking for latency_arb |
| `_funding_rates` | `shadow.py:236` | Funding rate cache |
| `_futures_exchanges` | `shadow.py:239` | Futures exchange identification |

---

## US-021: MultiStrategySignalProducer Shadow Connection

### Current State

`ShadowMode._on_orderbook()` at line 478-537 follows this flow:

```
orderbook update
  -> SignalGenerator.on_orderbook_update()          [cross_exchange only]
  -> if signal: _execute_shadow_trade(signal)
  -> if multi_signal_producer:
       multi_signal_producer.on_orderbook(...)      [cache only, no signal production]
       _evaluate_multi_strategies(...)              [inline evaluation]
       -> if signal: _execute_shadow_trade(signal)
```

### Decision D7: Integration Pattern -- RealDataSignalProducer Replaces Inline Evaluation

After refactoring, the flow becomes:

```
orderbook update
  -> SignalGenerator.on_orderbook_update()          [cross_exchange, unchanged]
  -> if signal: _execute_shadow_trade(signal)
  -> if real_signal_producer:
       signals = real_signal_producer.on_orderbook_update(
           exchange_id, symbol, book,
           self._books, self._futures_books
       )
       for signal in signals:
           _execute_shadow_trade(signal)
```

**Key changes to `ShadowMode.__init__`**:

- Add parameter: `real_signal_producer: RealDataSignalProducer | None = None`
- Remove: `multi_signal_producer` parameter (RealDataSignalProducer wraps it internally)
- Remove: `_evaluate_*` methods (~400 lines)
- Remove: `_spread_history`, `_exchange_update_times` state (moved to RealDataSignalProducer)
- Keep: `_funding_rates` cache (still populated by `_funding_rate_loop`) -- pass to producer

**Risk**: Breaking change to ShadowMode constructor. Mitigate with backward-compatible
default: if `multi_signal_producer` is passed but `real_signal_producer` is not, wrap it.

### Decision D8: Event-Driven vs Polling for Signal Generation

**Recommended**: Event-driven (on each orderbook update), which is the current pattern.

**Rationale**: Orderbook updates arrive at ~100/s across 175 symbols and 8 exchanges.
Each update may invalidate a previous signal opportunity. Polling would either:
- Poll too slowly (miss opportunities)
- Poll too frequently (waste CPU computing on unchanged data)

The event-driven model naturally throttles via the dedup cooldown in
`MultiStrategySignalProducer._is_duplicate()` (`multi_signal.py:378-382`).

### Decision D9: Can We Evaluate All Strategies on Every Orderbook Update?

**No. Strategy evaluation must be selective per update.**

Performance budget per orderbook update: <5ms (to keep up with 100 updates/s).

| Strategy | Trigger Condition | Cost |
|----------|------------------|------|
| cross_exchange | Every update (PriceHub comparison) | <1ms |
| triangular | Only when updated symbol is on an exchange with 3+ pairs | <1ms (Bellman-Ford) |
| spot_futures | Only when we have both spot AND futures book for same symbol | <0.5ms |
| futures_futures | Only when we have 2+ futures exchanges for same symbol | <0.5ms |
| statistical_arb | Every update (rolling z-score, O(1) amortized) | <0.5ms |
| latency_arb | Every update (timestamp comparison) | <0.1ms |
| funding_rate | NOT on orderbook update. Only on funding rate poll (60s) | N/A |

**Total worst case**: ~3.5ms per update. Acceptable.

**Optimization**: Skip strategies that cannot fire. For example:
- If `exchange_id` is NOT in `_futures_exchanges`, skip `spot_futures` and `futures_futures`
- If symbol has <2 exchanges in `_books`, skip `statistical_arb` and `latency_arb`
- Triangular: only scan the exchange that was updated

These guards already exist in the current `_evaluate_*` methods (e.g., `shadow.py:600-602`
checks `books_available`). They must be preserved in the extraction.

---

## US-022: Integration Testing Strategy

### Required Test Categories

1. **TriangularScanner unit tests** (~15 tests):
   - Graph construction from orderbooks (2 edges per pair)
   - Bellman-Ford detects known profitable cycle
   - Bellman-Ford rejects unprofitable cycle
   - Depth-aware sizing returns min across legs
   - Staleness rejection when one book is >5s old
   - Empty graph returns no cycles
   - Single pair returns no cycles (need minimum 3 for triangle)
   - Multiple cycles: returns all (not just first)
   - Edge update when orderbook changes

2. **RealDataSignalProducer unit tests** (~20 tests):
   - `on_orderbook_update` dispatches to correct strategy evaluators
   - Disabled strategies are skipped
   - Futures exchange identification works
   - spot_futures: correct direction (contango/backwardation)
   - futures_futures: both directions checked
   - statistical_arb: z-score accumulation and threshold
   - latency_arb: timestamp differential detection
   - funding_rate: differential signal generation
   - Dedup cooldown prevents duplicate signals
   - Signals have correct strategy_id for routing

3. **Shadow integration tests** (~10 tests):
   - ShadowMode with RealDataSignalProducer produces signals from real-format orderbooks
   - Cross_exchange signals still work (SignalGenerator path unchanged)
   - Multi-strategy signals route to `_execute_shadow_trade`
   - Funding rate loop triggers `on_funding_rates_update`
   - Per-strategy stats tracked correctly
   - Disabled strategies produce zero signals

4. **Signal routing tests** (~5 tests):
   - `StrategyManager._should_route` matches signals to strategies by `STRATEGY_TYPE`
   - Triangular signal routes to `TriangularStrategy`
   - Spot_futures signal routes to `SpotFuturesStrategy`
   - Funding_rate signal routes to `FundingRateStrategy`
   - Unknown strategy_id logged but does not crash

---

## Signal Routing Architecture

### Current Signal Flow

```
                 ┌─────────────────────┐
                 │  CollectorManager    │
                 │  (WebSocket data)    │
                 └──────┬──────────────┘
                        │ on_orderbook(exchange, symbol, bids, asks)
                        ▼
                 ┌─────────────────────┐
                 │    ShadowMode       │
                 │  _on_orderbook()    │
                 └──┬──────────┬───────┘
                    │          │
          ┌─────────▼───┐  ┌──▼────────────────┐
          │SignalGenerator│  │ _evaluate_*()     │
          │(cross_exch)  │  │ (inline methods)  │
          └──────┬───────┘  └──────┬────────────┘
                 │                 │
                 ▼                 ▼
          ┌──────────────────────────┐
          │  _execute_shadow_trade() │
          │  (PaperExecutor)         │
          └──────────────────────────┘
```

### Proposed Signal Flow (Post B-3)

```
                 ┌─────────────────────┐
                 │  CollectorManager    │
                 │  (WebSocket data)    │
                 └──────┬──────────────┘
                        │ on_orderbook(exchange, symbol, bids, asks)
                        ▼
                 ┌─────────────────────┐
                 │    ShadowMode       │
                 │  _on_orderbook()    │
                 └──┬──────────┬───────┘
                    │          │
          ┌─────────▼───┐  ┌──▼──────────────────────┐
          │SignalGenerator│  │  RealDataSignalProducer  │
          │(cross_exch)  │  │  (6 strategies)          │
          │              │  │  ┌─TriangularScanner(s)  │
          │              │  │  ├─spot_futures eval      │
          │              │  │  ├─futures_futures eval   │
          │              │  │  ├─statistical_arb eval   │
          │              │  │  ├─latency_arb eval       │
          │              │  │  └─funding_rate eval      │
          └──────┬───────┘  └──────┬───────────────────┘
                 │                 │ list[Signal]
                 ▼                 ▼
          ┌──────────────────────────┐
          │  _execute_shadow_trade() │
          │  (PaperExecutor)         │
          └──────────────────────────┘
```

### Signal Deduplication

Signals are deduplicated at TWO layers:

1. **Producer level**: `MultiStrategySignalProducer._is_duplicate()` at `multi_signal.py:378-382`
   uses per-strategy cooldowns (1-30s depending on signal type)
2. **SignalGenerator level**: `signal.py:72-76` uses a 5s cooldown per (symbol, buy_ex, sell_ex)

These are independent and both remain active. No cross-producer dedup is needed because
they produce different `strategy_id` values that route to different strategies.

### Who Decides Which Signals to Act On?

The decision chain is:

1. **Producer** applies minimum threshold gates (e.g., `min_profit_bps`, `min_basis_bps`)
2. **Strategy.on_signal()** applies strategy-specific filters (e.g., `FundingRateStrategy`
   checks funding_rate_threshold at `funding_rate.py:73-75`)
3. **ShadowMode._execute_shadow_trade()** applies final `buy_price >= sell_price` rejection
   at `shadow.py:1052`
4. In live mode, the `StrategyManager._dispatch()` routes signals and `BaseStrategy.on_signal()`
   returns `TradeRequest | None`

No single arbiter exists -- filtering is progressive through the pipeline. This is correct
for arbitrage: each layer narrows the signal space.

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Bellman-Ford false positives from stale books | HIGH | MEDIUM | Staleness check on all 3 legs (<5s). Already proven pattern in `signal.py:138-148`. |
| Triangular signals with zero volume | MEDIUM | LOW | Depth-aware sizing with minimum volume floor. Signal with volume < dust_threshold is dropped. |
| RealDataSignalProducer refactor breaks cross_exchange | LOW | HIGH | Cross_exchange path (SignalGenerator) is completely untouched. No shared state. |
| Performance regression (>5ms per update) | LOW | HIGH | Selective evaluation guards. Benchmark test: inject 1000 orderbook updates, assert P99 < 5ms. |
| Stat_arb / latency_arb produce lossy signals in shadow | KNOWN | MEDIUM | Already disabled in `shadow.py:568-572`. `RealDataSignalProducer.set_disabled_strategies()` preserves this. |
| Multiple TriangularScanners per exchange waste memory | LOW | LOW | One scanner per exchange. Binance graph: ~50 nodes, ~200 edges = ~10KB. |

---

## Performance Considerations

### Memory

- TriangularScanner graph: ~200 edges * ~100 bytes/edge = **20KB per exchange**
- With 8 exchanges: **160KB total** -- negligible
- Statistical arb spread history: 100-element deque per (symbol, exchange_pair) combo.
  Worst case 175 symbols * C(8,2) = 4,900 deques * 100 floats * 8 bytes = **3.9MB** --
  acceptable but consider reducing window if needed

### CPU

- Bellman-Ford per scan: ~10K operations = **<0.5ms** in CPython
- All strategies combined: **<3.5ms** worst case per orderbook update
- At 100 updates/s: **35% CPU** on strategy evaluation
- Optimization path: Move Bellman-Ford to Rust via PyO3 if CPU becomes bottleneck.
  The existing `rust_bridge.py` pattern supports this.

### Latency

- Signal-to-execution latency target: <10ms (shadow mode)
- Current SignalGenerator path: <2ms (`SIGNAL_PROCESSING_TIME` histogram)
- Adding RealDataSignalProducer: +3.5ms worst case = **<6ms total**
- Well within budget

---

## File Structure Recommendation

```
engine/src/
  core/
    real_signal_producer.py   # NEW: RealDataSignalProducer
    triangular_scanner.py     # NEW: TriangularScanner + Bellman-Ford
    multi_signal.py           # UNCHANGED: publisher + PaperSignalSimulator
    signal.py                 # UNCHANGED: SignalGenerator (cross_exchange)
  modes/
    shadow.py                 # MODIFIED: remove _evaluate_* methods, add RealDataSignalProducer
  tests/
    test_triangular_scanner.py  # NEW: ~15 tests
    test_real_signal_producer.py # NEW: ~20 tests
    test_shadow_multi_strategy.py # NEW: ~10 integration tests
    test_signal_routing.py       # NEW: ~5 tests
```

---

## Implementation Priority

| Priority | US | Effort | Dependencies |
|----------|-----|--------|-------------|
| 1 | US-019: TriangularScanner | ~200 LOC | None (pure algorithm) |
| 2 | US-020: RealDataSignalProducer | ~350 LOC | US-019 (for triangular) |
| 3 | US-021: Shadow Integration | ~50 LOC (net reduction) | US-020 |
| 4 | US-022: Integration Tests | ~400 LOC | US-019, US-020, US-021 |

**Net LOC change**: shadow.py loses ~400 lines, gains ~50 lines of wiring.
Two new files add ~550 lines. Tests add ~400 lines. Total: +600 LOC net.

---

## Trade-offs

| Option | Pros | Cons |
|--------|------|------|
| Extract to RealDataSignalProducer (recommended) | Clean SRP, testable in isolation, reusable in Live mode | Refactoring effort, temporary API break if not careful |
| Keep inline in ShadowMode | Zero refactoring risk, already working for 2 strategies | shadow.py stays 1200+ lines, untestable, duplicated for Live mode |
| One producer per strategy type | Maximum isolation, independent deployment | 6 classes, boilerplate explosion, shared state (books) must be threaded through |

---

## References

- `engine/src/core/signal.py:38-219` -- SignalGenerator (cross_exchange only, GAP 2 evidence)
- `engine/src/core/multi_signal.py:60-398` -- MultiStrategySignalProducer (publisher only, GAP 3 evidence)
- `engine/src/core/multi_signal.py:401-509` -- PaperSignalSimulator (synthetic signals)
- `engine/src/modes/shadow.py:559-576` -- `_evaluate_multi_strategies` (inline evaluation)
- `engine/src/modes/shadow.py:578-643` -- `_evaluate_triangular` (hardcoded single path)
- `engine/src/modes/shadow.py:645-746` -- `_evaluate_statistical_arb` (z-score rolling window)
- `engine/src/modes/shadow.py:748-814` -- `_evaluate_latency_arb` (timestamp differential)
- `engine/src/modes/shadow.py:816-859` -- `_evaluate_spot_futures` (basis trade)
- `engine/src/modes/shadow.py:861-915` -- `_evaluate_futures_futures` (cross-exchange futures)
- `engine/src/modes/shadow.py:990-1037` -- `_evaluate_funding_rate_arb` (funding differential)
- `engine/src/strategies/manager.py:201-218` -- `_should_route` (signal-to-strategy routing)
- `engine/src/strategies/base.py:46-58` -- `CostCalculator` Protocol
- `engine/src/strategies/triangular.py:55-138` -- `TriangularStrategy.on_signal` (consumer side)
- `engine/src/friction/slippage_model.py:59-186` -- `CEXOrderbookSlippage` (existing slippage model)
- `engine/src/collectors/funding_rate_collector.py:97-304` -- `FundingRateCollector` (Phase B-2 output)
- `engine/src/core/order_book.py:14-143` -- `OrderBook` (L2 with Decimal prices)
