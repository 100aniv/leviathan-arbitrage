# US-023 Code Review: ShadowMode StrategyManager Injection + Routing

**Reviewer**: code-reviewer (opus)
**Date**: 2026-03-08
**Verdict**: REQUEST CHANGES

---

## Code Review Summary

**Files Reviewed:** 6
**Total Issues:** 7

### By Severity
- CRITICAL: 0
- HIGH: 2 (must fix before merge)
- MEDIUM: 3 (should fix)
- LOW: 2 (optional)

---

## Stage 1: Spec Compliance

**Requirement**: Inject StrategyManager into ShadowMode, route signals through registered
strategies via `route_signal()`, preserve backward compatibility with direct `_execute_shadow_trade()`.

| Criterion | Status | Notes |
|-----------|--------|-------|
| StrategyManager injected into ShadowMode | PASS | `strategy_manager` param added to `__init__`, stored as `self._strategy_manager` |
| `route_signal()` method added to StrategyManager | PASS | Lines 226-258, reuses `_should_route()` for consistent matching |
| ShadowMode delegates routing when manager present | PASS | `_route_signal_to_strategies()` at lines 871-909 |
| Fallback to `_execute_shadow_trade()` on exception | PASS | Exception path at line 901-909 with Prometheus counter |
| Empty route result does NOT trigger fallback | PASS | Tested in integration test #19 |
| `_execute_shadow_trade_request()` for N-leg execution | PASS | Lines 911-1010 |
| Redis loop skipped in shadow mode | PASS | `main.py` line 756 conditional |
| `futures_futures` STRATEGY_TYPE fix | PASS | Changed from `"futures_futures_cross"` to `"futures_futures"` |
| Strategies set to shadow mode + started | PASS | `main.py` lines 1020-1030 |
| Per-strategy breakdown in daily summary | PASS | Lines 1151-1206 |
| Prometheus `ROUTING_FALLBACK_TOTAL` counter | PASS | Module-level counter at line 49 |
| BaseStrategy / SignalGenerator NOT modified | PASS | Guardrail respected |
| No forced activation of inactive strategies | PASS | `route_signal()` checks `is_active` |
| Unit tests (9) | PASS | All 36 tests pass |
| Integration tests (10) | PASS | All 36 tests pass |

**Stage 1 Verdict: PASS** -- All spec requirements are implemented.

---

## Stage 2: Code Quality

### LSP Diagnostics

| File | Errors | Warnings |
|------|--------|----------|
| `engine/src/modes/shadow.py` | 0 | 0 |
| `engine/src/strategies/manager.py` | 0 | 0 |
| `engine/src/main.py` | 0 | 0 |
| `engine/src/strategies/futures_futures.py` | 0 | 0 |
| `engine/tests/unit/strategies/test_manager.py` | 0 | 0 |
| `engine/tests/integration/test_shadow_strategy_integration.py` | 0 | 0 |

---

## Issues

### [HIGH] Metrics double-counting in `route_signal()`

**File**: `/Users/100aniv/development/arbitrage_OMC/engine/src/strategies/manager.py:244-250`

**Issue**: `route_signal()` manually increments `strategy._metrics.signals_received` (line 244),
`trade_requests_generated` (line 247), and `signals_filtered` (line 250) before/after calling
`strategy.on_signal(signal)`. However, every strategy's `on_signal()` implementation already
increments these same counters internally. For example, `FuturesFuturesStrategy.on_signal()`
at `futures_futures.py:49` does `self._metrics.signals_received += 1`, and at line 93 does
`self._metrics.trade_requests_generated += 1`.

This means all three metrics are counted **twice** per signal in shadow mode routing, while
`_dispatch()` (the Redis path at line 160) does NOT increment metrics externally -- it relies
solely on the strategy's internal accounting. This creates an inconsistency:
- Redis path (`_dispatch`): metrics counted 1x (by strategy internals)
- Shadow path (`route_signal`): metrics counted 2x (by strategy internals + route_signal)

**Impact**: Incorrect Prometheus/observability metrics. `signals_received` will show 2x the
actual count. This can lead to wrong conclusions about strategy performance during shadow
evaluation -- the most critical phase before live trading.

**Fix**: Remove the three manual metric increments from `route_signal()` and let strategies
handle their own metrics, consistent with `_dispatch()`:

```python
async def route_signal(self, signal: Signal) -> list[TradeRequest]:
    results: list[TradeRequest] = []
    for strategy in self._strategies.values():
        if not strategy.is_active:
            continue
        if not self._should_route(strategy, signal):
            # ...
            continue
        try:
            # REMOVE: strategy._metrics.signals_received += 1
            request = await strategy.on_signal(signal)
            if request is not None:
                # REMOVE: strategy._metrics.trade_requests_generated += 1
                results.append(request)
            # REMOVE: else: strategy._metrics.signals_filtered += 1
        except Exception as exc:
            # ...
    return results
```

**Note**: The unit tests that assert on these metrics (lines 620-689 in `test_manager.py`)
use `MagicMock` strategies whose `on_signal` does NOT internally increment metrics, so the
tests pass despite the bug. Tests using real strategy instances (lines 463-501) would catch
this -- but they are shadowed by duplicate function names (see next issue).

---

### [HIGH] Duplicate test function names silently shadow 9 tests

**File**: `/Users/100aniv/development/arbitrage_OMC/engine/tests/unit/strategies/test_manager.py`

**Issue**: The file contains two complete sets of `route_signal` tests with identical function
names. The first set (lines 330-501) uses real `CrossExchangeStrategy` and `FundingRateStrategy`
instances. The second set (lines 561-689) uses `MagicMock` strategies.

Duplicate names found:
- `test_route_signal_dispatches_to_matching_strategy` (lines 330, 561)
- `test_route_signal_returns_trade_requests` (lines 350, 575)
- `test_route_signal_skips_inactive_strategies` (lines 378, 590)
- `test_route_signal_skips_non_matching_type` (lines 396, 605)
- `test_route_signal_returns_empty_when_all_filtered` (lines 416, 623)
- `test_route_signal_handles_strategy_exception` (lines 434, 637)
- `test_route_signal_updates_metrics_signals_received` (lines 463, 658)
- `test_route_signal_updates_metrics_trade_requests_generated` (lines 482, 673)
- `test_route_signal_updates_metrics_signals_filtered` (lines 501, 689)

In Python, the second definition overwrites the first at module level. pytest only discovers
and runs the second definition. The first 9 tests (using real strategies) **never execute**.

**Impact**: The real-strategy tests that would catch the double-counting bug (HIGH #1) are
silently skipped. False sense of test coverage.

**Fix**: Either:
1. Remove the duplicate set (likely the second MagicMock set, since real-strategy tests are
   more valuable), or
2. Rename one set with a distinguishing suffix (e.g., `_with_real_strategies` and `_with_mocks`)

---

### [MEDIUM] Missing TimescaleDB recording in `_execute_shadow_trade_request()`

**File**: `/Users/100aniv/development/arbitrage_OMC/engine/src/modes/shadow.py:911-1010`

**Issue**: The existing `_execute_shadow_trade()` (line 697) records every execution to
TimescaleDB via `self._market_recorder.record_execution()` (lines 804-841), including
gross spread, fees, slippage, and net PnL. The new `_execute_shadow_trade_request()` does
NOT record to TimescaleDB at all.

When `strategy_manager` is present (the normal new path), all trades routed through strategies
will have no TimescaleDB record. Only the fallback path (routing exception) will record to DB.

**Impact**: Loss of historical trade data for post-hoc analysis. Shadow mode's purpose is
evaluation before live trading -- missing DB records undermines this goal.

**Fix**: Add `self._market_recorder.record_execution(...)` to `_execute_shadow_trade_request()`
after PnL computation, mirroring the pattern in `_execute_shadow_trade()`. Adapt the fields
for N-leg trades (e.g., use first buy/sell leg for exchange pair).

---

### [MEDIUM] Missing `buy_price >= sell_price` guard in `_execute_shadow_trade_request()`

**File**: `/Users/100aniv/development/arbitrage_OMC/engine/src/modes/shadow.py:911`

**Issue**: `_execute_shadow_trade()` has a defense-in-depth guard at line 706 that rejects
signals where `buy_price >= sell_price` (guaranteed loss, e.g., stat_arb z-score signals).
The new `_execute_shadow_trade_request()` has no equivalent guard.

While the strategy's `on_signal()` should filter unprofitable trades, this is a defense-in-depth
pattern. For multi-leg trades (e.g., triangular with 3 legs), the check is more complex but
some basic validation would be prudent.

**Impact**: Strategies with bugs in their `on_signal()` logic could produce unprofitable
TradeRequests that get executed without a sanity check.

**Fix**: Add a guard checking `trade_request.expected_profit_usdt > 0` before execution:

```python
if trade_request.expected_profit_usdt <= Decimal("0"):
    logger.debug(
        "shadow_mode.skip_unprofitable_request",
        strategy_id=sid,
        expected_profit=str(trade_request.expected_profit_usdt),
    )
    return
```

---

### [MEDIUM] Two loops over strategies in `main.py` shadow init can be merged

**File**: `/Users/100aniv/development/arbitrage_OMC/engine/src/main.py:1020-1030`

**Issue**: Two separate loops iterate over `self._strategy_manager.list_strategies()`:
1. Lines 1022-1025: set `shadow_mode = True`
2. Lines 1026-1030: call `start_strategy()`

These can be a single loop. More importantly, the first loop calls `get_strategy(sid)` which
returns `Optional[BaseStrategy]` and checks `if s:`, but `list_strategies()` returns keys of
the internal dict -- `get_strategy()` on those keys will always return a value, making the
`if s:` check dead code.

**Fix**: Merge into one loop:

```python
if self._strategy_manager is not None:
    for sid in self._strategy_manager.list_strategies():
        s = self._strategy_manager.get_strategy(sid)
        if s:
            s.shadow_mode = True
        try:
            await self._strategy_manager.start_strategy(sid)
        except Exception as exc:
            logger.warning("Shadow strategy %s start failed: %s", sid, exc)
```

---

### [LOW] Silent `except Exception: pass` in Prometheus metrics block

**File**: `/Users/100aniv/development/arbitrage_OMC/engine/src/modes/shadow.py:1009-1010`

**Issue**: The Prometheus metrics block in `_execute_shadow_trade_request()` swallows all
exceptions silently with `except Exception: pass`. While this matches the existing pattern in
`_execute_shadow_trade()` (line 856-857), it hides potential label cardinality issues or
Prometheus client errors that could indicate misconfiguration.

**Impact**: Minor -- Prometheus errors in shadow mode are not critical. However, at minimum a
`logger.debug()` would aid troubleshooting.

**Fix**: Either add `logger.debug("shadow_mode.prometheus_metrics_error", error=str(exc))`
or leave as-is (consistent with existing codebase pattern).

---

### [LOW] Helper function name collision between test sections

**File**: `/Users/100aniv/development/arbitrage_OMC/engine/tests/unit/strategies/test_manager.py:448,682`

**Issue**: Two helper functions named `_make_signal` (line 290) and `make_signal` (line 524)
exist in the same file with slightly different defaults (`spread_pct=0.005` vs `0.002`,
`sell_price=50250` vs `50100`). Similarly, `make_mock_strategy` is defined at line 541.
While not a bug (they serve different test sections), this creates maintenance confusion.

**Fix**: Use a single helper with parametric defaults, or rename for clarity
(e.g., `_make_signal_v1` / `_make_signal_v2`).

---

## Positive Observations

1. **Correct backward compatibility**: The `strategy_manager=None` default preserves the
   existing direct-execution path. Well-designed optional injection.

2. **Proper fallback semantics**: The distinction between "empty list = normal filtering"
   and "exception = mechanism failure" is well-documented and correctly implemented.

3. **Consistent `_should_route()` reuse**: Both `_dispatch()` (Redis path) and `route_signal()`
   (shadow path) use the same matching logic, preventing routing divergence.

4. **Substring matching correctness**: Analysis of all 7 signal strategy_ids against all 8
   STRATEGY_TYPE values confirms 1:1 matching with no false positives under current naming.

5. **Good error isolation**: Exceptions in individual strategies do not prevent other
   strategies from receiving the signal (tested in both unit and integration).

6. **Prometheus ROUTING_FALLBACK_TOTAL counter**: Excellent observability for detecting
   routing mechanism failures in production.

7. **FuturesFuturesStrategy STRATEGY_TYPE fix**: Correctly changed from `"futures_futures_cross"`
   to `"futures_futures"` so `"futures_futures" in "futures_futures_spread"` evaluates to True.

---

## Backward Compatibility Analysis

| Aspect | Impact | Assessment |
|--------|--------|------------|
| `ShadowMode.__init__` signature | New optional `strategy_manager` param | SAFE -- default None |
| `StrategyManager` public API | New `route_signal()` method added | SAFE -- additive |
| `_execute_shadow_trade()` | Preserved, used in fallback path | SAFE |
| `_strategy_manager_loop` in `main.py` | Conditionally skipped in shadow mode | SAFE -- shadow already used direct execution |
| `FuturesFuturesStrategy.STRATEGY_TYPE` | Changed from `"futures_futures_cross"` | **Check needed** -- any code comparing against the old value will break |
| Redis Streams path (`_dispatch`) | Unchanged | SAFE |

Note on STRATEGY_TYPE change: This is a **breaking change** for any code that matches on the
old `"futures_futures_cross"` string. However, grep confirms no other code references this value,
and the change is required for correct `_should_route()` matching.

---

## Security Analysis

| Check | Result |
|-------|--------|
| Hardcoded secrets | None found |
| SQL injection | N/A (no raw SQL) |
| Race conditions | Single-threaded asyncio -- no concurrent mutation of `_strategies` dict |
| Input validation | Signal validated by Pydantic model before routing |
| Exception information leakage | Exceptions logged internally, not exposed to external callers |
| Prometheus label injection | Strategy IDs from internal registry, not user input |

---

## Test Coverage Assessment

| Test Category | Count | Quality |
|---------------|-------|---------|
| Unit: route_signal happy path | 2 (9 defined but 9 shadowed) | HIGH issue -- duplicates |
| Unit: route_signal filtering | 2 | Good |
| Unit: route_signal edge cases | 2 | Good (exception, empty) |
| Unit: route_signal metrics | 3 | Compromised by double-counting bug |
| Integration: ShadowMode routing | 3 | Good |
| Integration: fallback behavior | 3 | Good (None manager, exception, empty result) |
| Integration: type matching | 3 | Good (cross_exchange, funding_rate, futures_futures) |
| Integration: Redis loop skip | 1 | Minimal but sufficient |

**Missing test coverage**:
- `_execute_shadow_trade_request()` is only tested indirectly (mocked in integration tests)
- No test for N-leg (3+) TradeRequest execution
- No test for `_execute_shadow_trade_request()` with network cost computation
- No test for daily summary per-strategy breakdown

---

## Recommendation

**REQUEST CHANGES** -- 2 HIGH severity issues must be fixed before merge:

1. **Fix metrics double-counting** in `route_signal()` by removing the three manual
   `strategy._metrics` increments (let strategy internals handle it, matching `_dispatch()`).

2. **Remove or rename duplicate test functions** in `test_manager.py` so all 18 tests
   actually execute. The real-strategy tests (first set) are especially valuable.

After fixing HIGH issues, the MEDIUM issues (missing TimescaleDB recording, missing profit
guard, loop merge) should be addressed as follow-up before the next shadow runtime validation.
