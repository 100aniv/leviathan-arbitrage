# US-066: Stale Orderbook Detection + Blacklist + Per-Trade Loss Cap

**Phase**: G (Strategy Profitability Restoration)
**Priority**: 70
**Mode**: DELIBERATE (high-risk: directly impacts PnL, fat-tail loss prevention)
**Date**: 2026-03-10 (v2 -- supersedes previous draft)

---

## Context

### Problem

1H Shadow run produced PnL = -$1,937 (2,554 trades, 75.6% WR). Breakdown by strategy:
- spot_futures: -$1,127
- cross_exchange: -$497
- latency_arb: -$67
- stat_arb: -$3

Root cause: Bithumb incremental orderbook (`DELTA_EXCHANGES = {"bithumb"}` at shadow.py line 684) accumulates deltas without periodic full snapshot refresh. Small-cap coins drift 2-10x from actual price, creating phantom spreads. A single fat-tail trade (-$249) wiped out hundreds of small profitable trades.

### Current Defenses and Their Gaps

| Defense | Location | Gap |
|---------|----------|-----|
| `max_spread_pct=5%` | signal.py:142-147 | 5% too permissive; stale books show 3-4% spreads that look legitimate |
| `max_book_age_seconds=30.0` | signal.py:155-165 | Checks `ob.last_update_time`; Bithumb deltas update this on every WS message even when book is drifted |
| Bithumb REST snapshot on startup | bithumb_collector.py:62-125 | One-time only; drift resumes immediately after |
| Price sanity on REST | bithumb_collector.py:96-102 | Only on REST fetch, not on WS deltas |
| RealDataSignalProducer Korean block | real_signal_producer.py:91 | Only blocks spot_futures; cross_exchange and latency_arb still exposed |
| Per-trade loss cap | **None** | Fat-tail single-trade loss is unlimited |
| Strategy blacklist | **None** | Cannot disable individual strategies without code change |

### Key Files

| File | Role | Lines |
|------|------|-------|
| `engine/src/core/order_book.py` | OrderBook with `last_update_time`, `apply_snapshot`, `apply_delta` | 176 |
| `engine/src/core/signal.py` | SignalGenerator with staleness gate (line 155-165), `SignalConfig` | 246 |
| `engine/src/collectors/bithumb_collector.py` | REST snapshot + WS delta, `is_symbol_stale()` | 203 |
| `engine/src/collectors/manager.py` | CollectorManager, 8 exchanges, `KOREAN_EXCHANGES` set | 124 |
| `engine/src/modes/shadow.py` | `_on_orderbook` (line 646), `_execute_shadow_trade` (line 933), `DELTA_EXCHANGES` | ~1400 |
| `engine/src/core/real_signal_producer.py` | Multi-strategy signal; Korean spot_futures block (line 91) | ~200 |
| `engine/src/infra/metrics.py` | Prometheus counters/gauges | ~50 |

---

## RALPLAN-DR Summary

### Principles (5)

1. **Defense-in-depth**: Multiple independent layers catch stale data -- cross-exchange validation, periodic refresh, enhanced staleness, loss cap. Any single layer failing still leaves protection.
2. **Root cause, not symptoms**: The drift is caused by incremental orderbook accumulation without re-anchoring. Periodic REST refresh IS the structural fix, not a follow-up.
3. **No double-slippage**: All changes must preserve the rule that CEXOrderbookSlippage in SignalGenerator is the only slippage source (k=0 in PaperExecutor).
4. **Configuration-driven**: Every threshold is configurable via env var with sane defaults. No code changes needed to tune in production.
5. **Backwards compatibility**: Existing 3,575 tests must pass. New defenses are additive (new fields default to existing behavior when unset).

### Decision Drivers (Top 3)

1. **Fat-tail elimination**: A single trade lost -$249 while the median winning trade earned +$0.76. Per-trade loss cap is the highest-priority defense as it provides a hard ceiling regardless of detection quality.
2. **Bithumb delta drift**: The root cause is incremental orderbook accumulation without periodic snapshot re-anchor. Periodic REST re-fetch every 60s is the structural fix and cannot be deferred.
3. **Observability**: Stale events must be logged with Prometheus counters so Phase G validation (1H Shadow) can confirm the fix is working.

### Viable Options

#### Option A: Multi-Layer Defense (RECOMMENDED)

Add 4 independent defense layers:

1. **Cross-exchange price validation** in ShadowMode `_on_orderbook` -- compare incoming book's mid-price against median of other exchanges for the same symbol. Reject if deviation > N% (default 10%).
2. **Periodic Bithumb REST snapshot refresh** -- re-fetch full orderbook every 60s for delta exchanges to re-anchor accumulated deltas.
3. **Enhanced staleness tracking** -- add `update_count` to OrderBook; require delta exchanges to have >= N updates before trusting. Extend Korean exchange stale detection to all strategies (not just spot_futures).
4. **Per-trade loss cap** in `_execute_shadow_trade` -- cap any single trade's loss at $50. Trades exceeding cap trigger blacklist for involved exchange-symbol pairs.

**Pros** (bounded):
- 4 independent layers = near-zero probability of stale trade slipping through all defenses
- Per-trade loss cap provides hard guarantee regardless of detection quality
- Periodic refresh fixes the root cause structurally (delta drift)
- All thresholds configurable via env vars; can tune per-exchange
- Cross-exchange validation catches drift that timestamp-based checks miss (Bithumb timestamps update on every WS message)

**Cons** (bounded):
- Cross-exchange validation adds O(n) comparison per orderbook update (n = max 8 exchanges; negligible)
- Periodic REST adds ~175 HTTP requests/60s for Bithumb (rate-limited at 5 req/s = 35s per cycle; within limits)
- +1 new module, ~200 lines net new production code
- Minor increase in log volume from stale rejection logging

#### Option B: Loss Cap + Strategy Blacklist Only (Minimal)

Add per-trade loss cap ($50) and disable Korean exchange strategies via `SHADOW_DISABLED_STRATEGIES` env var. No structural fix for delta drift.

**Pros** (bounded):
- Minimal code change (~60 lines)
- Hard loss cap prevents fat-tail regardless of cause
- Strategy blacklist immediately stops the bleeding

**Cons** (bounded):
- Does NOT fix the root cause -- stale data still generates false signals and inflates signal count
- Strategy blacklist is a blunt instrument: disabling cross_exchange for Korean exchanges also blocks legitimate Korean-to-global arb opportunities
- Metrics remain noisy (false signals counted as real)
- When Korean exchanges eventually get fixed (US-067+), re-enabling requires careful validation since the underlying detection infra was never built

**Why Option B is not chosen**: The -$1,937 loss came from many stale-data trades across multiple strategies, not just one. Without cross-validation and periodic refresh, the engine generates hundreds of false signals that distort win rate and PnL metrics. Option A eliminates the root cause AND provides a hard cap. Option B defers the structural fix, making US-067 harder.

---

## Pre-Mortem (3 Failure Scenarios)

### Scenario 1: Periodic REST refresh overloads Bithumb API

**Trigger**: 175 symbols x 1 request every 60s = ~3 req/s sustained. Bithumb public API rate limit is ~5 req/s.
**Impact**: HTTP 429 errors; snapshot refresh fails; drift resumes.
**Mitigation**: Stagger requests with existing 0.25s delay (bithumb_collector.py:114). Add exponential backoff on 429. Increase refresh interval to 120s via `BITHUMB_REFRESH_INTERVAL_S` if needed. Cross-validation layer still catches drift independently. Loss cap provides final backstop.

### Scenario 2: Cross-exchange median is itself wrong (e.g., all Korean exchanges stale simultaneously)

**Trigger**: Upbit, Bithumb, Coinone all have stale data for the same symbol at the same time.
**Impact**: If median includes stale prices, validation may pass incorrectly.
**Mitigation**: When validating a Korean exchange book, compute median from non-Korean exchanges only (Binance, Bybit, OKX, Bitget, Binance_futures). Require at least 2 non-Korean exchanges in the comparison set. Per-trade loss cap remains as final backstop.

### Scenario 3: Cross-exchange deviation threshold too tight rejects legitimate volatile coins

**Trigger**: Deviation threshold set at 3%; volatile altcoin legitimately differs by 4% across exchanges.
**Impact**: False negatives -- valid arb opportunities rejected.
**Mitigation**: Set initial threshold at 10% (generous). Log all rejections with deviation amount for post-analysis. Tune threshold based on 1H Shadow data. Default `min_comparison_exchanges=2` ensures sufficient confidence.

---

## ADR: Architecture Decision Record

| Field | Content |
|-------|---------|
| **Decision** | Implement multi-layer stale orderbook defense (Option A): cross-exchange validation, periodic REST refresh, enhanced staleness gate, per-trade loss cap |
| **Drivers** | Fat-tail elimination is Phase G's primary goal; root cause (delta drift) requires structural fix; defense-in-depth required for pre-live system |
| **Alternatives** | Option B (loss cap + blacklist only): rejected because it does not fix root cause and leaves metrics noisy with false signals |
| **Why Chosen** | Option A provides structural fix (periodic refresh) + detection (cross-validation) + hard safety cap (loss limit). Each layer independently testable and configurable |
| **Consequences** | +1 new module (`stale_detector.py`), ~200 lines production code, ~35 new tests, periodic REST adds ~3 req/s to Bithumb API (within limits), ~0.1ms latency per orderbook update for cross-validation |
| **Follow-ups** | US-067 (strategy parameter tuning based on US-066 Shadow data), Phase H (dashboard display of stale rejection metrics), consider extending periodic refresh to Coinone if similar drift observed |

---

## Task Flow (5 Steps)

### Step 1: Create StaleOrderbookDetector Module

**Files**: NEW `engine/src/core/stale_detector.py`

Create a standalone module with three responsibilities:

**Cross-exchange price validation**:
- Given a book, compare its mid-price against median of other exchanges for the same symbol
- For Korean exchanges (`{"upbit", "bithumb", "coinone"}`), compute median from non-Korean exchanges only
- Flag as stale if deviation > threshold (default 10%, env: `STALE_CROSS_DEVIATION_PCT`)
- Require at least `min_comparison_exchanges` (default 2) non-self exchanges to perform check; skip validation if insufficient data

**Blacklist management**:
- Maintain `dict[(exchange, symbol)] -> expiry_monotonic_time`
- `add_blacklist(exchange, symbol)`: add with TTL (default 300s, env: `STALE_BLACKLIST_TTL_S`)
- `is_blacklisted(exchange, symbol) -> bool`: check with auto-cleanup of expired entries
- `cleanup_expired()`: remove expired entries (called lazily on each `is_blacklisted` check)

**Prometheus counters** (defined in module, registered on import):
- `shadow_stale_rejected_total` with labels `(exchange, reason)` where reason is: `cross_validation`, `book_age`, `low_update_count`, `blacklisted`
- `shadow_blacklist_active` gauge with label `(exchange)`

```python
class StaleOrderbookDetector:
    KOREAN_EXCHANGES = {"upbit", "bithumb", "coinone"}

    def __init__(
        self,
        deviation_pct: float = 0.10,
        blacklist_ttl_s: float = 300.0,
        min_comparison_exchanges: int = 2,
    ): ...

    def check_cross_exchange(
        self, exchange: str, symbol: str, book: OrderBook,
        all_books: dict[str, dict[str, OrderBook]],
    ) -> bool:
        """Return True if book passes validation, False if stale."""
        ...

    def is_blacklisted(self, exchange: str, symbol: str) -> bool: ...
    def add_blacklist(self, exchange: str, symbol: str) -> None: ...
    def cleanup_expired(self) -> None: ...
    def blacklist_count(self) -> int: ...
```

**Acceptance Criteria**:
- [ ] Module importable with zero side effects (no global state mutation outside Prometheus registration)
- [ ] Cross-exchange deviation correctly flags book with mid-price 5x off median
- [ ] Normal deviation (0.5%) passes validation
- [ ] Korean exchange comparison uses only non-Korean exchanges for median
- [ ] Insufficient comparison exchanges (<2) skips validation (returns True)
- [ ] Blacklist entries expire after TTL
- [ ] `STALE_CROSS_DEVIATION_PCT` and `STALE_BLACKLIST_TTL_S` env vars configurable
- [ ] 8 unit tests pass

---

### Step 2: Enhance OrderBook + SignalGenerator Staleness

**Files**: `engine/src/core/order_book.py`, `engine/src/core/signal.py`

**OrderBook changes**:
- Add `update_count: int = 0` field to `__init__`
- Increment `self.update_count += 1` in both `apply_snapshot()` and `apply_delta()`
- This allows distinguishing "delta exchange with 1 update" (only initial REST snapshot, likely incomplete) from "delta exchange with 50 updates" (accumulated sufficient depth)

**SignalGenerator changes**:
- Add optional `stale_detector: StaleOrderbookDetector | None = None` parameter to `__init__`
- Add `min_delta_update_count: int = 3` to `SignalConfig` (env: `STALE_MIN_DELTA_UPDATES`)
- In `on_orderbook_update()`, after existing staleness gate (line 165), add:

```python
# Blacklist gate (fast reject)
if self._stale_detector is not None:
    for label, ob in [("buy", buy_book), ("sell", sell_book)]:
        if self._stale_detector.is_blacklisted(ob.exchange, symbol):
            logger.debug("blacklisted_rejected symbol=%s exchange=%s", symbol, ob.exchange)
            return None

# Delta exchange minimum update count gate
DELTA_EXCHANGES = {"bithumb"}
for label, ob in [("buy", buy_book), ("sell", sell_book)]:
    if ob.exchange in DELTA_EXCHANGES and ob.update_count < self._config.min_delta_update_count:
        logger.debug("low_update_count_rejected symbol=%s exchange=%s count=%d",
                     symbol, ob.exchange, ob.update_count)
        return None
```

**Acceptance Criteria**:
- [ ] `OrderBook.update_count` increments on every `apply_snapshot` and `apply_delta`
- [ ] SignalGenerator rejects blacklisted `(exchange, symbol)` pairs
- [ ] Delta exchanges with `update_count < 3` are rejected
- [ ] All existing signal.py tests pass with no modification (detector defaults to None = no new behavior)
- [ ] 5 new unit tests pass

---

### Step 3: Per-Trade Loss Cap + Strategy Blacklist in ShadowMode

**Files**: `engine/src/modes/shadow.py`

**Strategy blacklist** (in `__init__`):
```python
disabled_raw = os.environ.get("SHADOW_DISABLED_STRATEGIES", "")
self._disabled_strategies: set[str] = {s.strip() for s in disabled_raw.split(",") if s.strip()}
```
- Check at top of `_execute_shadow_trade()` and `_execute_shadow_trade_request()`:
```python
sid = signal.strategy_id or self.STRATEGY_ID
if sid in self._disabled_strategies:
    logger.debug("shadow_mode.strategy_disabled", strategy=sid)
    return
```

**Per-trade loss cap** (in `_execute_shadow_trade`, after `net_pnl` computed at line 1062-1067):
```python
max_loss = self._max_loss_per_trade_usd  # Decimal, parsed from env in __init__
if net_pnl < -max_loss:
    capped_pnl = -max_loss
    logger.warning(
        "shadow_mode.trade_loss_capped",
        symbol=signal.symbol,
        buy_exchange=signal.buy_exchange,
        sell_exchange=signal.sell_exchange,
        raw_pnl=f"{float(net_pnl):+.4f}",
        capped_pnl=f"{float(capped_pnl):+.4f}",
    )
    net_pnl = capped_pnl
    net_pnl_float = float(net_pnl)
    # Blacklist involved exchange-symbol pairs to prevent repeat
    if self._stale_detector is not None:
        self._stale_detector.add_blacklist(signal.buy_exchange, signal.symbol)
        self._stale_detector.add_blacklist(signal.sell_exchange, signal.symbol)
```

Note: The cap is applied post-execution (after buy+sell trades computed), not pre-execution. This is intentional because pre-execution PnL estimation is unreliable (slippage, partial fills, fees are unknown until execution). Post-execution capping ensures the recorded PnL never exceeds -$50 while still accurately tracking that the trade was problematic.

**StaleOrderbookDetector integration** (in `__init__`):
```python
from src.core.stale_detector import StaleOrderbookDetector
self._stale_detector = StaleOrderbookDetector(
    deviation_pct=float(os.getenv("STALE_CROSS_DEVIATION_PCT", "0.10")),
    blacklist_ttl_s=float(os.getenv("STALE_BLACKLIST_TTL_S", "300")),
)
```

**Cross-validation in `_on_orderbook`** (after book creation/update, before SignalGenerator):
```python
if self._stale_detector is not None:
    if not self._stale_detector.check_cross_exchange(
        exchange_id, symbol, book, self._books
    ):
        logger.info(
            "shadow_mode.stale_cross_validation_rejected",
            exchange=exchange_id, symbol=symbol,
        )
        return  # Skip signal generation for this update
```

**Korean strategy expansion in `_evaluate_multi_strategies`**:
- Extend Korean exchange blocking to cover latency_arb (already exposed to stale data)
- cross_exchange is handled by SignalGenerator (staleness gate + cross-validation); no change needed there

**Env vars parsed in `__init__`**:
```python
self._max_loss_per_trade_usd = Decimal(os.getenv("SHADOW_MAX_LOSS_PER_TRADE_USD", "50"))
```

**Acceptance Criteria**:
- [ ] Trades with computed PnL < -$50 are capped at -$50 in stats
- [ ] Loss-capped trades trigger blacklist for both involved exchange-symbol pairs
- [ ] `SHADOW_DISABLED_STRATEGIES` blocks listed strategies from executing
- [ ] Cross-exchange validation rejects books with >10% deviation from median
- [ ] Stale-rejected orderbooks never reach SignalGenerator
- [ ] `SHADOW_MAX_LOSS_PER_TRADE_USD` env var override works
- [ ] 8 new unit tests pass

---

### Step 4: Periodic REST Refresh for Delta Exchanges

**Files**: `engine/src/collectors/bithumb_collector.py`, `engine/src/modes/shadow.py`

**BithumbCollector changes**:
- Add `async def refresh_snapshots(self)` method:
```python
async def refresh_snapshots(self) -> int:
    """Re-fetch REST snapshots for all symbols. Returns count of refreshed symbols."""
    self._snapshot_fetched = False
    await self._fetch_initial_snapshots()
    return sum(1 for s in self.symbols if not self.is_symbol_stale(s, max_age_s=10.0))
```
This reuses the existing `_fetch_initial_snapshots()` logic (which already handles rate limiting with 0.25s delay, price sanity checks, error handling).

**ShadowMode changes** (in `start()`):
- Create background task `_delta_refresh_loop`:
```python
self._delta_refresh_task = asyncio.create_task(
    self._delta_refresh_loop(), name="shadow_delta_refresh"
)
```

- Implement loop:
```python
async def _delta_refresh_loop(self) -> None:
    """Periodically refresh REST snapshots for delta exchanges (Bithumb)."""
    interval = float(os.getenv("BITHUMB_REFRESH_INTERVAL_S", "60"))
    try:
        while self._running:
            await asyncio.sleep(interval)
            for eid in ("bithumb",):
                collector = self._collector_manager._collectors.get(eid)
                if collector is not None and hasattr(collector, "refresh_snapshots"):
                    try:
                        count = await collector.refresh_snapshots()
                        logger.info("shadow_mode.delta_refresh_done",
                                   exchange=eid, refreshed=count)
                    except Exception as exc:
                        logger.warning("shadow_mode.delta_refresh_failed",
                                      exchange=eid, error=str(exc))
    except asyncio.CancelledError:
        pass
```

- In `stop()`, cancel `_delta_refresh_task` alongside other background tasks.

**Why the periodic refresh re-anchors correctly**: When `refresh_snapshots()` calls `_fetch_initial_snapshots()`, it fetches full REST orderbooks and delivers them via `self._on_orderbook`. In `ShadowMode._on_orderbook`, Bithumb is in `DELTA_EXCHANGES`, so subsequent WS messages apply deltas to the refreshed book. The REST snapshot effectively resets the book to ground truth every 60s, preventing long-term drift.

**Acceptance Criteria**:
- [ ] Bithumb REST snapshots re-fetched every 60s during Shadow mode
- [ ] Refresh replaces accumulated delta book with fresh snapshot (verified by `update_count` reset)
- [ ] HTTP 429 errors handled gracefully (logged, no crash)
- [ ] Refresh loop cancelled cleanly on `stop()`
- [ ] `BITHUMB_REFRESH_INTERVAL_S` env var override works
- [ ] 3 new unit tests pass

---

### Step 5: Observability + Wiring + Validation

**Files**: `engine/src/infra/metrics.py`, `engine/src/modes/shadow.py`, `engine/src/main.py`

**Prometheus metrics** (add to `engine/src/infra/metrics.py`):
```python
STALE_ORDERBOOK_REJECTED = Counter(
    "shadow_stale_orderbook_rejected_total",
    "Orderbooks rejected due to stale data detection",
    ["exchange", "reason"],
)
TRADE_LOSS_CAPPED = Counter(
    "shadow_trade_loss_capped_total",
    "Trades where per-trade loss was capped at max threshold",
    ["exchange"],
)
```

**ShadowMode metric increments**:
- In cross-validation rejection: `STALE_ORDERBOOK_REJECTED.labels(exchange=exchange_id, reason="cross_validation").inc()`
- In blacklist rejection: `STALE_ORDERBOOK_REJECTED.labels(exchange=ob.exchange, reason="blacklisted").inc()`
- In loss cap trigger: `TRADE_LOSS_CAPPED.labels(exchange=signal.buy_exchange).inc()`

**main.py wiring**:
- Import `StaleOrderbookDetector`
- Create instance and pass to `SignalGenerator`:
```python
stale_detector = StaleOrderbookDetector(...)
signal_generator = SignalGenerator(
    price_hub=price_hub,
    cost_calculator=cost_calculator,
    config=signal_config,
    stale_detector=stale_detector,
)
```

**Telegram summary enhancement**:
- Add stale rejection count and loss-capped trade count to `_send_summary()` message

**Validation** (manual, 1H Shadow):
- Run: `cd engine && timeout 3600 python -m src.main`
- Verify: zero trades with per-trade PnL < -$50
- Verify: `shadow_stale_orderbook_rejected_total` > 0 in Prometheus
- Verify: total PnL >= $0
- Verify: all tests pass: `cd engine && python -m pytest tests/ -x --tb=short`

**Acceptance Criteria**:
- [ ] All Prometheus metrics registered and incrementing during Shadow run
- [ ] Telegram summary includes stale rejection count
- [ ] main.py correctly wires StaleOrderbookDetector to SignalGenerator
- [ ] All new env vars have sane defaults and are documented in code comments
- [ ] `cd engine && python -m pytest tests/ -x --tb=short` passes with 0 failures
- [ ] 1H Shadow: zero fat-tail losses > $50 per trade, PnL >= $0

---

## Expanded Test Plan (DELIBERATE Mode)

### Unit Tests (~25 new tests)

**test_stale_detector.py** (8 tests):
1. `test_cross_exchange_deviation_detected` -- mid-price 5x off median returns False
2. `test_cross_exchange_deviation_normal` -- 0.5% deviation returns True
3. `test_korean_exchange_uses_non_korean_median` -- median excludes Korean exchanges when checking Korean book
4. `test_insufficient_exchanges_skips_validation` -- <2 comparison exchanges returns True (skip)
5. `test_blacklist_add_and_check` -- blacklisted (exchange, symbol) returns True from `is_blacklisted`
6. `test_blacklist_expiry` -- blacklist entry expires after TTL
7. `test_blacklist_env_override` -- custom TTL via constructor
8. `test_cleanup_expired_removes_old_entries` -- `cleanup_expired` removes stale entries

**test_orderbook_update_count.py** (3 tests):
9. `test_update_count_increments_on_snapshot` -- count goes from 0 to 1
10. `test_update_count_increments_on_delta` -- count increments on each delta
11. `test_update_count_accumulates` -- snapshot + 3 deltas = count 4

**test_signal_staleness_enhanced.py** (5 tests):
12. `test_blacklisted_book_rejected` -- signal returns None for blacklisted exchange-symbol
13. `test_low_update_count_delta_rejected` -- Bithumb book with update_count=1 rejected
14. `test_sufficient_update_count_passes` -- Bithumb book with update_count=5 passes
15. `test_non_delta_exchange_skips_count_check` -- Binance (not delta) not subject to count check
16. `test_no_detector_backwards_compat` -- detector=None preserves all existing behavior

**test_shadow_loss_cap.py** (5 tests):
17. `test_loss_cap_caps_large_loss` -- -$200 PnL recorded as -$50
18. `test_loss_cap_allows_profitable_trade` -- +$5 PnL unchanged
19. `test_loss_cap_allows_small_loss` -- -$10 PnL unchanged
20. `test_loss_cap_triggers_blacklist` -- capped trade adds exchange-symbol to blacklist
21. `test_loss_cap_env_override` -- `SHADOW_MAX_LOSS_PER_TRADE_USD=100` works

**test_shadow_strategy_blacklist.py** (4 tests):
22. `test_disabled_strategy_skips_execution` -- blacklisted strategy trade count = 0
23. `test_empty_blacklist_executes_all` -- empty string = no blacklist
24. `test_multiple_strategies_blacklisted` -- comma-separated list works
25. `test_blacklist_does_not_affect_other_strategies` -- non-listed strategy executes normally

### Integration Tests (~5 tests)

26. `test_shadow_rejects_stale_bithumb_signal` -- full pipeline: stale Bithumb book does not produce signal
27. `test_shadow_accepts_fresh_bithumb_signal` -- full pipeline: fresh Bithumb book produces valid signal
28. `test_shadow_blacklist_persists_across_updates` -- blacklisted symbol stays blocked until TTL
29. `test_shadow_loss_cap_prevents_fat_tail` -- full pipeline: stale trade capped at $50
30. `test_shadow_cross_validation_blocks_drift` -- 5x price drift detected and blocked

### E2E / Shadow Validation

31. **10min Shadow regression**: existing performance not degraded (trade count >= 50% of baseline 3,110)
32. **1H Shadow acceptance**: PnL >= $0, zero trades with per-trade loss < -$50, `stale_rejected_total` > 0

### Observability Verification

- `shadow_stale_orderbook_rejected_total` counter active with appropriate labels
- `shadow_trade_loss_capped_total` counter active
- All rejection events visible in structured logs

---

## Guardrails

### Must Have
- Per-trade loss cap of $50 (hard limit, post-execution)
- Cross-exchange price validation for all exchanges (Korean vs non-Korean median)
- Periodic Bithumb REST refresh every 60s (structural fix for delta drift)
- Blacklist with TTL for detected stale pairs
- Strategy blacklist via `SHADOW_DISABLED_STRATEGIES` env var
- All env vars documented with sane defaults
- All existing 3,575 tests pass (zero regression)

### Must NOT Have
- PowerLaw slippage re-enabled (k=0.0 stays in PaperExecutor)
- Any modification to PaperExecutor's slippage model
- Disabling Bithumb or any Korean exchange entirely (they have low fees; fix the data, keep the routes)
- Hard-coded thresholds without env var overrides
- Architectural changes to collector or signal pipeline beyond the additions described
- Pre-execution PnL estimation for loss cap (unreliable; use post-execution)

---

## Env Var Summary

| Env Var | Default | Description |
|---------|---------|-------------|
| `STALE_CROSS_DEVIATION_PCT` | 0.10 | Max allowed mid-price deviation from cross-exchange median (fraction) |
| `STALE_BLACKLIST_TTL_S` | 300 | Seconds a blacklisted (exchange, symbol) pair remains blocked |
| `STALE_MIN_DELTA_UPDATES` | 3 | Minimum OrderBook.update_count for delta exchanges to be trusted |
| `SHADOW_MAX_LOSS_PER_TRADE_USD` | 50 | Per-trade loss cap (USD) |
| `SHADOW_DISABLED_STRATEGIES` | (empty) | Comma-separated strategy IDs to disable |
| `BITHUMB_REFRESH_INTERVAL_S` | 60 | Seconds between periodic Bithumb REST snapshot refreshes |

Existing env vars (unchanged): `MAX_SPREAD_PCT=0.05`, `MIN_EDGE_BPS=5`, `SHADOW_MAX_BOOK_AGE_SECONDS=30`

---

## File Change Summary

| File | Action | Lines (est.) |
|------|--------|-------------|
| `engine/src/core/stale_detector.py` | NEW | ~120 |
| `engine/src/core/order_book.py` | MODIFY (+update_count) | +5 |
| `engine/src/core/signal.py` | MODIFY (+blacklist gate, +delta count gate) | +25 |
| `engine/src/modes/shadow.py` | MODIFY (+detector, +loss cap, +strategy blacklist, +refresh loop) | +80 |
| `engine/src/collectors/bithumb_collector.py` | MODIFY (+refresh_snapshots method) | +10 |
| `engine/src/infra/metrics.py` | MODIFY (+2 Prometheus counters) | +10 |
| `engine/src/main.py` | MODIFY (+detector wiring) | +10 |
| `engine/tests/unit/test_stale_detector.py` | NEW | ~180 |
| `engine/tests/unit/test_orderbook_update_count.py` | NEW | ~50 |
| `engine/tests/unit/test_signal_staleness_enhanced.py` | NEW | ~100 |
| `engine/tests/unit/test_shadow_loss_cap.py` | NEW | ~120 |
| `engine/tests/unit/test_shadow_strategy_blacklist.py` | NEW | ~80 |
| **Total** | | **~790** |

---

## Success Criteria

1. **1H Shadow PnL >= $0** (was -$1,937)
2. **Zero trades with per-trade PnL < -$50** (was -$249 single trade)
3. **`stale_orderbook_rejected_total` > 0** in Prometheus (proves detection is active)
4. **All 3,575 + ~30 new tests pass** with zero regression
5. **10min Shadow trade count >= 50% of baseline** (no over-filtering of legitimate signals)
6. **Korean exchange stale signals blocked** (verified via structured logs)

---

## Risk Assessment

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|-----------|
| Periodic REST overloads Bithumb API (429s) | Low | Medium | 0.25s delay per request; backoff on 429; increase interval to 120s via env var |
| Cross-validation false positive on volatile coins | Low | Low | 10% threshold is generous; tune via `STALE_CROSS_DEVIATION_PCT` |
| Loss cap masks legitimate market moves | Very Low | Low | $50 cap is 20% of observed max loss (-$249); legitimate arb losses are typically <$5 |
| All Korean exchanges stale simultaneously | Medium | Low | Non-Korean median used; per-trade loss cap as final backstop |
| `update_count` gate too strict for initial startup | Low | Low | Grace period: first 3 deltas ~5-15s after startup; negligible signal loss window |
