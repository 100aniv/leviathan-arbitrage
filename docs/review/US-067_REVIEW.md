# US-067 Code Review: 전략별 개별 Shadow 검증 (StrategyValidationOrchestrator)

> **Reviewer**: code-reviewer (opus) | **Date**: 2026-03-11 | **Phase**: G

---

## Code Review Summary

**Files Reviewed:** 5
**Total Issues:** 7

### By Severity
- CRITICAL: 1 (must fix)
- HIGH: 1 (should fix)
- MEDIUM: 3 (consider fixing)
- LOW: 2 (optional)

---

## Stage 1: Spec Compliance

### Handoff Requirements vs Implementation

| Requirement | Status | Notes |
|-------------|--------|-------|
| StrategyValidationOrchestrator class | PASS | Implemented with run(), _validate_single/combined, _write_activation_config, _send_telegram_report |
| ShadowMode.reset_stats() | PASS | Resets _stats, _balance_tracker, _rate_limiter, _stale_detector |
| ShadowMode.set_disabled_strategies() | PASS | Dynamically replaces _disabled_strategies set |
| ShadowMode.get_strategy_report() | PASS | Returns serializable dict from _stats.by_strategy |
| cross_exchange shadow_arb_v1 special handling | PASS | Correctly keeps shadow_arb_v1 enabled for cross_exchange, blocks for others |
| main.py STRATEGY_VALIDATION=true branch | PASS | Properly added before SHADOW_PROGRESSIVE with elif chain |
| config/strategy_activation.json output | PASS | Correct schema with _meta, active_strategies, disabled_strategies, results |
| Telegram report | PASS | Sends formatted summary with profitable/unprofitable/insufficient |
| 17 unit tests | PASS (16/17) | 1 test has wrong mock key — see CRITICAL issue |
| 1 integration test | FAIL | Same root cause — signal ID key mismatch |
| 이중 슬리피지 금지 | PASS | No PowerLaw/SlippageModel references in strategy_validation.py |
| Existing code untouched | PASS | Only additive changes — 3 new methods on shadow.py, new branch in main.py |

**Stage 1 Verdict**: CONDITIONAL PASS — spec implementation is correct, but 2/18 tests fail due to test data bug (see CRITICAL #1).

---

## Stage 2: Code Quality

### LSP Diagnostics

| File | Errors | Warnings |
|------|--------|----------|
| src/modes/strategy_validation.py | 0 | 0 |
| src/modes/shadow.py | 0 | 0 |
| src/main.py | 0 | 0 |

### Issues

---

#### [CRITICAL] Signal ID key mismatch in test mocks causes 2 test failures

**Files:**
- `tests/unit/test_strategy_validation.py:266-267`
- `tests/integration/test_strategy_validation_integration.py:38-57`

**Issue:**
The orchestrator code at `strategy_validation.py:185` looks up stats via:
```python
signal_id = STRATEGY_SIGNAL_ID_MAP.get(strategy_id, strategy_id)
stats = report.get(signal_id, {})
```

For `spot_futures_v1`, this maps to `"spot_futures_basis"`. But the test mocks provide the inner dict keyed by registration ID `"spot_futures_v1"`:
```python
# Unit test (line 267) — WRONG key
return_value={"spot_futures_v1": {"trades": 10, ...}}
# Should be:
return_value={"spot_futures_basis": {"trades": 10, ...}}
```

The integration test `_STRATEGY_REPORTS` (lines 38-57) has the same problem for ALL non-cross_exchange strategies:
- `"spot_futures_v1"` should be `"spot_futures_basis"`
- `"futures_futures_v1"` should be `"futures_futures"`
- `"triangular_v1"` should be `"triangular"`
- `"funding_rate_v1"` should be `"funding_rate_arb"`
- `"statistical_arb_v1"` should be `"statistical_arb"`
- `"latency_arb_v1"` should be `"latency_arb"`

**Result:** 2 tests FAIL. Only `cross_exchange_v1` works because its signal ID (`shadow_arb_v1`) is already used correctly. All other strategies get empty dict → 0 trades → wrongly classified as `insufficient_data`.

**Evidence:**
```
FAILED test_classify_unprofitable
E  AssertionError: assert 'unprofitable' in 'insufficient_data (0 trades < 5 min)'

FAILED test_full_validation_lifecycle
E  AssertionError: assert 'funding_rate_v1' in ['cross_exchange_v1']
   # Only cross_exchange classified as profitable; all others got 0 trades
```

**Fix:** Update mock dict keys to use signal IDs from `STRATEGY_SIGNAL_ID_MAP`:

Unit test `test_classify_unprofitable` (line 267):
```python
return_value={"spot_futures_basis": {"trades": 10, "wins": 3, "losses": 7, "pnl": -3.0, "win_rate": 0.3}}
```

Integration test `_STRATEGY_REPORTS` (lines 38-57): replace ALL inner dict keys with their signal ID equivalents.

---

#### [HIGH] No validation on env var integer parsing — unhandled ValueError

**File:** `src/modes/strategy_validation.py:76-79`

**Issue:** Four `int(os.getenv(...))` calls will crash with `ValueError` if operator provides non-integer values:
```python
self._duration_s = int(os.getenv("STRATEGY_VALIDATION_DURATION_S", "600"))
self._combined_duration_s = int(os.getenv("STRATEGY_VALIDATION_COMBINED_DURATION_S", "600"))
self._min_trades = int(os.getenv("STRATEGY_VALIDATION_MIN_TRADES", "5"))
self._hydration_s = int(os.getenv("STRATEGY_VALIDATION_HYDRATION_S", "30"))
```

Setting `STRATEGY_VALIDATION_DURATION_S=abc` or `STRATEGY_VALIDATION_DURATION_S=` (empty) crashes the entire engine at init.

**Fix:** Add validation with fallback:
```python
def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key, "")
    try:
        return int(raw) if raw else default
    except ValueError:
        logger.warning("Invalid %s=%r, using default %d", key, raw, default)
        return default
```

---

#### [MEDIUM] Prometheus metrics not reset between strategy isolations

**File:** `src/modes/shadow.py:1710-1723`

**Issue:** `reset_stats()` correctly resets `_stats`, `_balance_tracker`, `_rate_limiter`, and `_stale_detector`, but does NOT reset Prometheus counters/gauges (`PNL_TOTAL`, `TRADES_TOTAL`, `SIGNALS_TOTAL`, `DRAWDOWN_CURRENT`, etc.). During the ~80-minute validation, these global metrics accumulate across all 7+1 strategy runs, making dashboard monitoring misleading.

**Fix:** Add Prometheus gauge resets in `reset_stats()`:
```python
PNL_TOTAL.set(0)
DRAWDOWN_CURRENT.set(0)
```
Or document that Prometheus metrics are aggregate-only during validation mode.

---

#### [MEDIUM] `_strategy_validation_loop` creates ShadowMode with `paper_executor=None` and `collector_manager=None`

**File:** `src/main.py:1150-1153`

**Issue:** Passing `None` for `paper_executor` and `collector_manager` means ShadowMode's `__init__` will create its own internal instances. This is intentional per the handoff design (isolated ShadowMode), but the `paper_executor=None` path creates a new PaperExecutor with default settings. The code should verify that the internally-created PaperExecutor uses `SlippageModel.ZERO` (per the 이중 슬리피지 금지 규칙).

Looking at `shadow.py:428-429`, the default PaperExecutor construction uses:
```python
self._paper_executor = PaperExecutor(slippage_model=SlippageModel.BOOK_WALK, ...)
```

This is the correct CEXOrderbookSlippage path (not PowerLaw), so the 이중 슬리피지 rule is NOT violated. However, this implicit dependency is fragile.

**Fix:** Explicitly pass the slippage model or add a code comment documenting the invariant.

---

#### [MEDIUM] Integration test comment acknowledges "buggy log format" but doesn't fix it

**File:** `tests/integration/test_strategy_validation_integration.py:106-108`

**Issue:** The test comment says:
```python
# Suppress the buggy "%.1%%" log format in strategy_validation.py:109
# which causes ValueError in pytest's log formatter for UNPROFITABLE records
caplog.set_level(logging.CRITICAL, logger="src.modes.strategy_validation")
```

The log format at `strategy_validation.py:111` uses `%.1f%%` for win rate display. If the test acknowledges this is "buggy", the source format string should be fixed, not suppressed in tests.

**Fix:** Verify `strategy_validation.py:111` format string is correct. `%.1f%%` actually IS correct Python format syntax (literal `%` after `%f`). If pytest's formatter chokes, the suppression is acceptable but the comment calling it "buggy" is misleading — update the comment.

---

#### [LOW] Inconsistent logging framework: `logging` vs `structlog`

**File:** `src/modes/strategy_validation.py:14`

**Issue:** `strategy_validation.py` uses `logging.getLogger(__name__)` while `shadow.py` uses `structlog.get_logger()`. All other modules in `src/modes/` use structlog for structured key-value logging.

**Fix:** Switch to `structlog.get_logger()` for consistency:
```python
import structlog
logger = structlog.get_logger(__name__)
```

---

#### [LOW] `hasattr` defensive checks in reset_stats() are unnecessary

**File:** `src/modes/shadow.py:1714-1722`

**Issue:** `reset_stats()` uses `hasattr(self, '_balance_tracker')` and `hasattr(self, '_rate_limiter')` guards. These attributes are always set in `__init__` (lines 457-458), so the `hasattr` checks are dead code that adds unnecessary defensive complexity.

**Fix:** Remove `hasattr` guards and access directly:
```python
self._balance_tracker.reset()
self._rate_limiter._buckets = {}
if self._stale_detector is not None:
    self._stale_detector._blacklist.clear()
```

---

## Positive Observations

1. **Clean isolation design**: The orchestrator correctly reuses ShadowMode's existing `_disabled_strategies` mechanism rather than inventing a parallel path. No WS reconnection needed.
2. **cross_exchange shadow_arb_v1 handling**: The special case is well-documented and correctly implemented in both `_validate_single_strategy` and `_validate_combined`.
3. **Output schema**: `config/strategy_activation.json` includes `shadow_disabled_env` for easy env var copy-paste — good operational UX.
4. **Graceful degradation**: `_send_telegram_report` catches exceptions without crashing the orchestrator.
5. **try/finally in main.py**: `shadow.stop()` is guaranteed even if orchestrator.run() fails.
6. **Test coverage**: 92% on strategy_validation.py (excluding Telegram branch). Good assertion variety.

---

## Recommendation

### REQUEST CHANGES

**Blocking issues (must fix before merge):**

1. **CRITICAL**: Fix test mock keys to use signal IDs from `STRATEGY_SIGNAL_ID_MAP` — 2 tests currently FAIL, meaning unprofitable classification logic is untested.
2. **HIGH**: Add `int()` validation for env var parsing — engine crash on malformed config.

**Non-blocking (can address in follow-up):**

3. MEDIUM: Prometheus metric reset (or document as known limitation)
4. MEDIUM: Verify PaperExecutor slippage model in isolated ShadowMode
5. MEDIUM: Fix misleading "buggy" comment in integration test
6. LOW: Switch to structlog
7. LOW: Remove unnecessary hasattr guards
