# US-066 Code Review: Stale Orderbook Detection + Blacklist

**Reviewer**: code-reviewer (opus)
**Date**: 2026-03-11
**Files Reviewed**: 8
**Total Issues**: 7

---

## Stage 1: Spec Compliance

### Requirements Verified

| Requirement | Status | Evidence |
|---|---|---|
| Cross-exchange price validation | PASS | `stale_detector.py:73-138` — compares mid-price vs non-self median; Korean exchanges only compared against non-Korean |
| TTL-based blacklist management | PASS | `stale_detector.py:140-177` — `is_blacklisted()` with monotonic expiry, auto-cleanup |
| `update_count` on OrderBook | PASS | `order_book.py:28,47,68` — increments on `apply_delta`, resets to 0 on `apply_snapshot` |
| Blacklist gate in SignalGenerator | PASS | `signal.py:171-178` — fast-reject for blacklisted pairs |
| Delta update count gate in SignalGenerator | PASS | `signal.py:180-189` — min 3 deltas required for Bithumb |
| Loss cap in `_execute_shadow_trade` | PASS | `shadow.py:1164-1180` — caps at `SHADOW_MAX_LOSS_PER_TRADE_USD` (default $50) |
| Loss cap in `_execute_shadow_trade_request` | PASS | `shadow.py:1425-1439` — same cap applied to N-leg trades |
| Strategy blacklist (env-driven) | PASS | `shadow.py:509-513` — `SHADOW_DISABLED_STRATEGIES` comma-separated |
| REST refresh loop for Bithumb | PASS | `shadow.py:978-1016`, `bithumb_collector.py:197-205` |
| `apply_snapshot()` resets `update_count` | PASS | `order_book.py:47` — `self.update_count = 0` |
| REST refresh applies as snapshot | PASS | `bithumb_collector.py:109` — `is_snapshot=True`, `shadow.py:728` — `not is_snapshot` guard |
| Prometheus counters | PASS | `metrics.py:152-162` — `STALE_ORDERBOOK_REJECTED`, `TRADE_LOSS_CAPPED` |
| `detector=None` backward compatibility | PASS | `signal.py:171` — `if self._stale_detector is not None` guard |
| StaleOrderbookDetector wired in main.py | PASS | `main.py:475-479` — created and injected into SignalGenerator |
| Double-slippage prevention maintained | PASS | `shadow.py:436` — `BookWalkSlippage(books=self._books)` with `fee_rate=Decimal("0")` |

**Stage 1 Verdict**: PASS -- all requirements covered.

---

## Stage 2: Code Quality

### LSP Diagnostics

All 8 modified files: **0 errors, 0 warnings**.

| File | Diagnostics |
|---|---|
| `engine/src/core/stale_detector.py` | Clean |
| `engine/src/core/order_book.py` | Clean |
| `engine/src/core/signal.py` | Clean |
| `engine/src/modes/shadow.py` | Clean |
| `engine/src/collectors/bithumb_collector.py` | Clean |
| `engine/src/collectors/manager.py` | Clean |
| `engine/src/infra/metrics.py` | Clean |
| `engine/src/main.py` | Clean |

---

### Issues by Severity

- **CRITICAL**: 0
- **HIGH**: 1
- **MEDIUM**: 3
- **LOW**: 3

---

### Issues

#### [HIGH] Missing `TRADE_LOSS_CAPPED` Prometheus counter in `_execute_shadow_trade_request`

**File**: `/Users/100aniv/Development/arbitrage_OMC/engine/src/modes/shadow.py:1425-1439`

**Issue**: In `_execute_shadow_trade()` (line 1178), the `TRADE_LOSS_CAPPED` Prometheus counter is incremented when a trade loss is capped. However, in the parallel code path `_execute_shadow_trade_request()` (lines 1425-1439), the loss cap is applied but the `TRADE_LOSS_CAPPED` counter is never incremented. This creates an observability blind spot: N-leg strategy trades (triangular, spot_futures, etc.) that hit the loss cap will not be counted in Prometheus metrics, making it impossible to monitor the frequency of capped losses from multi-leg strategies.

**Fix**: Add `TRADE_LOSS_CAPPED` counter increment in `_execute_shadow_trade_request` after line 1436:

```python
# After line 1436: net_pnl_float = float(net_pnl)
TRADE_LOSS_CAPPED.labels(exchange=trade_request.legs[0].exchange_id if trade_request.legs else "unknown").inc()
```

---

#### [MEDIUM] `Callable` type hint mismatch for `on_orderbook` callback

**File**: `/Users/100aniv/Development/arbitrage_OMC/engine/src/collectors/bithumb_collector.py:52` and all 9 other collector/manager files

**Issue**: The `on_orderbook` type hint across all collectors is `Callable[[str, str, list, list], Awaitable[None]]` (4 positional args). But `bithumb_collector.py:109` now passes `is_snapshot=True` as a 5th keyword argument to the callback. While this works at runtime because Python allows extra kwargs for functions that declare them, the type annotation is inaccurate -- a strict type checker (mypy strict mode) would flag the call site as a type error since the `Callable` signature does not declare the 5th parameter.

This is not a runtime bug because `shadow.py:683-689` declares `_on_orderbook(self, exchange_id, symbol, bids, asks, is_snapshot=False)` with `is_snapshot` as an optional kwarg. However, the type hint drift could cause confusion for future contributors.

**Fix**: Update the `Callable` type in `bithumb_collector.py:52` to use `Protocol` or a broader callback type, or simply document that the callback may receive `is_snapshot` as a keyword argument. Minimal fix:

```python
# bithumb_collector.py line 52 — note the broader type or Protocol usage
on_orderbook: Callable[..., Awaitable[None]] | None = None,
```

---

#### [MEDIUM] `DELTA_EXCHANGES` constant defined in two places with no single source of truth

**File**: `/Users/100aniv/Development/arbitrage_OMC/engine/src/core/signal.py:181` and `/Users/100aniv/Development/arbitrage_OMC/engine/src/modes/shadow.py:722`

**Issue**: `DELTA_EXCHANGES = {"bithumb"}` is hardcoded as a local variable in two separate locations. If a new delta-based exchange is added (e.g., Coinone switches to incremental WS), both locations must be updated independently with no compile-time or runtime enforcement. This violates the DRY principle and creates a maintenance risk.

**Fix**: Extract to a shared constant, either in `stale_detector.py` (since it already has `KOREAN_EXCHANGES`) or in a shared constants module:

```python
# In stale_detector.py or a shared constants module:
DELTA_EXCHANGES: frozenset[str] = frozenset({"bithumb"})

# Then import in signal.py and shadow.py
from src.core.stale_detector import DELTA_EXCHANGES
```

---

#### [MEDIUM] Potential race condition in `refresh_snapshots()` during concurrent WS message processing

**File**: `/Users/100aniv/Development/arbitrage_OMC/engine/src/collectors/bithumb_collector.py:197-205`

**Issue**: `refresh_snapshots()` resets `self._snapshot_fetched = False` and then calls `_fetch_initial_snapshots()` which makes HTTP requests. During this window, the WS `_handle_message()` loop in the base class continues processing incoming deltas concurrently (both are async coroutines in the same event loop). While asyncio is single-threaded, the HTTP `await client.get(...)` calls yield control, allowing WS message handlers to run.

The specific risk: between `_snapshot_fetched = False` and the completion of the REST fetch for a given symbol, WS deltas for that symbol could arrive and be dispatched via `_handle_message -> _on_orderbook` without `is_snapshot=True`. Then the REST snapshot arrives and overwrites via `_on_orderbook(..., is_snapshot=True)`, which correctly resets the book. However, during the REST fetch window, any deltas that arrived and were applied to the OLD (pre-refresh) book state are now lost (the snapshot overwrites them). This is actually the desired behavior (re-anchoring), but the timing window could theoretically produce a brief inconsistency where the book has stale data from the old snapshot plus new deltas that will be wiped.

In practice this is mitigated by: (a) the 60-second refresh interval is much larger than the fetch duration, (b) the cross-validation gate catches stale prices, (c) asyncio single-thread prevents true data races. This is a theoretical concern, not a practical bug.

**Fix**: No immediate fix required, but consider adding a log message at the start of `refresh_snapshots()` to aid debugging:

```python
logger.info("bithumb_refresh_snapshots_start", symbols=len(self.symbols))
```

---

#### [LOW] Docstring inaccuracy: `PaperExecutor` with `PowerLawSlippage` vs actual `BookWalkSlippage`

**File**: `/Users/100aniv/Development/arbitrage_OMC/engine/src/modes/shadow.py:393`

**Issue**: The docstring at line 393 says `paper_executor: PaperExecutor; if None, one with PowerLawSlippage` but the actual implementation at line 436 uses `BookWalkSlippage`. This docstring predates the refactoring and is misleading.

**Fix**: Update the docstring:

```python
paper_executor:    PaperExecutor; if None, one with BookWalkSlippage
                   (zero fee_rate) is created automatically.
```

---

#### [LOW] Bare `except Exception` in `stale_detector.py` swallows all errors silently

**File**: `/Users/100aniv/Development/arbitrage_OMC/engine/src/core/stale_detector.py:93` and `:114`

**Issue**: Two bare `except Exception` blocks (lines 93 and 114) silently return `True` (valid) when any error occurs during price extraction. While this is intentional defensive coding (fail-open to avoid rejecting valid data on transient errors), there is no logging at all. If an `OrderBook` subclass changes its interface, these silent swallows would hide the bug entirely.

**Fix**: Add `debug`-level logging to at least one of them:

```python
except Exception as exc:
    logger.debug("stale_detector.price_extract_error", exchange=exchange, error=str(exc))
    return True
```

---

#### [LOW] `_stale_detector` guard inconsistency between `_execute_shadow_trade` and `_execute_shadow_trade_request`

**File**: `/Users/100aniv/Development/arbitrage_OMC/engine/src/modes/shadow.py:1179` vs `:1437`

**Issue**: In `_execute_shadow_trade` (line 1179), `self._stale_detector.add_blacklist(...)` is called unconditionally (no `None` check), because `_stale_detector` is always initialized in `__init__` (line 504). However, in `_execute_shadow_trade_request` (line 1437), the code checks `if self._stale_detector is not None:` before calling `add_blacklist`. While neither path can actually receive `None` (it is always constructed in `__init__`), the inconsistency is confusing and suggests the author was uncertain about the invariant.

**Fix**: Either remove the `None` check from `_execute_shadow_trade_request` (since `_stale_detector` is never `None`), or add one to `_execute_shadow_trade` for consistency. The former is preferred since the invariant is clear.

---

## Security Review

| Check | Status | Notes |
|---|---|---|
| Hardcoded secrets | PASS | No API keys, passwords, or tokens in any changed file |
| Injection vectors | PASS | No user input flows into SQL, shell, or eval |
| Auth bypass | N/A | No auth-related changes |
| Env var defaults | PASS | All env vars have safe defaults (10% deviation, 300s TTL, $50 cap) |
| Double-slippage prevention | PASS | `BookWalkSlippage` at line 436, `fee_rate=Decimal("0")` at line 437 |

## Performance Review

| Check | Status | Notes |
|---|---|---|
| `check_cross_exchange` complexity | PASS | O(E) where E = number of exchanges per symbol (max ~8), acceptable |
| `is_blacklisted` lookup | PASS | O(1) dict lookup |
| `median()` computation | PASS | Called on small list (max 8 exchanges), negligible |
| Blacklist memory growth | PASS | TTL-based auto-cleanup on access; `cleanup_expired()` available |
| REST refresh interval | PASS | Default 60s, configurable via env var |

## Backward Compatibility

| Check | Status | Notes |
|---|---|---|
| `SignalGenerator` constructor | PASS | `stale_detector` parameter is optional (`None` default) |
| `_on_orderbook` callback | PASS | `is_snapshot` parameter has default `False` |
| `OrderBook.update_count` | PASS | New field, defaults to 0, no existing code depends on it |
| `CollectorManager.get_collector` | PASS | New method, no existing API changed |
| `ShadowMode.__init__` signature | PASS | No parameter changes; all US-066 features are internal |

---

## Summary

### By Severity
- **CRITICAL**: 0
- **HIGH**: 1 (missing Prometheus counter)
- **MEDIUM**: 3 (type hint mismatch, DRY violation, theoretical race)
- **LOW**: 3 (docstring, silent swallow, guard inconsistency)

### Recommendation

**REQUEST CHANGES**

The HIGH issue (missing `TRADE_LOSS_CAPPED` counter in `_execute_shadow_trade_request`) must be fixed before approval. This is an observability gap that makes it impossible to monitor loss-cap events from multi-leg strategy trades in Prometheus/Grafana dashboards.

The MEDIUM issues are recommended but not blocking. The LOW issues are optional improvements.

### What Was Done Well

1. **Defense-in-depth architecture**: Four layers of protection (cross-validation, blacklist, delta count gate, loss cap) provide robust stale data defense.
2. **Clean separation of concerns**: `StaleOrderbookDetector` is a focused, testable module with no coupling to shadow mode internals.
3. **Backward compatibility**: All changes are additive with safe defaults; existing callers continue to work.
4. **Configurable via env vars**: All thresholds (`STALE_CROSS_DEVIATION_PCT`, `STALE_BLACKLIST_TTL_S`, `SHADOW_MAX_LOSS_PER_TRADE_USD`, `BITHUMB_REFRESH_INTERVAL_S`, `SHADOW_DISABLED_STRATEGIES`) are externally configurable without code changes.
5. **Double-slippage invariant maintained**: `BookWalkSlippage` with zero `fee_rate` confirmed at shadow.py:436-437.
6. **Proper task lifecycle**: `_delta_refresh_task` is properly cancelled in `stop()` with `CancelledError` handling.
