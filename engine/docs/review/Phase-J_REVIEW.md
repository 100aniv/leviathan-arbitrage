# Phase J Code Review + Security Audit (Full)

**Reviewer**: leviathan-reviewer (opus, C-Step 2~3)
**Date**: 2026-04-02
**Assembly Gate**: PASS (5/5 items, Dead Wiring resolved)
**Shadow 13-item**: PASS (13/13), PnL=+$13,243, PF=8.75, CB CLOSED

---

## Files Reviewed (10)

| # | File | Verdict |
|---|------|---------|
| 1 | `engine/src/modes/backtest.py` | PASS + 2 MEDIUM |
| 2 | `engine/src/main.py` (backtest task) | PASS + 1 MEDIUM |
| 3 | `engine/src/analysis/ml_backtest.py` | **1 HIGH** + 1 MEDIUM |
| 4 | `engine/src/tuning/backtest.py` | PASS |
| 5 | `engine/src/tuning/strategy_backtest.py` | PASS |
| 6 | `engine/src/strategies/futures_futures.py` | PASS |
| 7 | `engine/src/api/routes/backtest.py` | **1 HIGH (security)** |
| 8 | `engine/src/infra/db/migrations/005_extend_retention.sql` | PASS |
| 9 | `engine/tests/unit/test_backtest_mode.py` | PASS |
| 10 | `engine/tests/unit/test_main_engine_lifecycle.py` | PASS |

---

## CRITICAL Issues

None.

---

## HIGH Issues

### HIGH-1: API /api/backtest/* endpoints missing authentication

**File**: `engine/src/api/routes/backtest.py`
**Lines**: 18, 47

Every other API route in the codebase uses `dependencies=[Depends(require_auth)]`. The two
new backtest endpoints (`GET /api/backtest/result`, `GET /api/backtest/wfa`) have **no
authentication**. This is inconsistent with the established security pattern and allows
unauthenticated access to backtest results including PnL data, strategy breakdowns, and
Sharpe ratios.

**Evidence**: All other route files import and apply `require_auth`:
- `routes/portfolio.py`: 5 endpoints, all guarded
- `routes/trading.py`: 9 endpoints, all guarded
- `routes/strategies.py`: 4 endpoints, all guarded
- `routes/backtest.py`: 2 endpoints, **zero guarded**

**Fix**: Add `from src.api.auth import require_auth` and
`dependencies=[Depends(require_auth)]` to both `@router.get` decorators.

**Severity**: HIGH -- information disclosure of strategy performance data.

---

### HIGH-2: Sharpe ratio ddof inconsistency in ml_backtest.py

**File**: `engine/src/analysis/ml_backtest.py`
**Line**: 217

```python
std = float(np.std(pnl_arr))  # ddof=0 (population std)
```

All three other Sharpe calculations in the codebase use `ddof=1` (sample std):
- `modes/backtest.py:411` -- `np.std(returns, ddof=1)`
- `tuning/backtest.py:209` -- `np.std(returns, ddof=1)`
- `tuning/strategy_backtest.py:711` -- `np.std(returns, ddof=1)`

Using `ddof=0` inflates the Sharpe ratio by a factor of `sqrt(n/(n-1))`. For small sample
sizes (e.g., 10 trades), this is a ~5% overestimate. For ML A/B tests that compare Sharpe
deltas between baseline and ML-enhanced, this silent discrepancy makes the baseline appear
artificially better (since baseline runs through the same `ddof=0` path while real-mode
uses `ddof=1`).

**Fix**: Change line 217 to `std = float(np.std(pnl_arr, ddof=1))`.

**Severity**: HIGH -- metric inconsistency affecting quant decision-making.

---

## MEDIUM Issues

### MED-1: SQL LIMIT uses f-string instead of parameterized binding

**File**: `engine/src/modes/backtest.py`
**Line**: 239

```python
query += f" ORDER BY ts ASC LIMIT {_max_rows}"
```

While `_max_rows` comes from `int(os.environ.get(...))` which sanitizes via `int()` cast,
mixing f-string interpolation with otherwise fully parameterized queries is an anti-pattern.
All other WHERE clause parameters use `$N` binding. This inconsistency could confuse future
developers into thinking f-string SQL is acceptable.

**Risk**: LOW (integer cast prevents injection), but violates the project's own SQL
parameterization convention.

**Recommendation**: Use `$N` binding for LIMIT as well:
```python
query += f" ORDER BY ts ASC LIMIT ${idx}"
params.append(_max_rows)
```

---

### MED-2: `import numpy` inside method body (lazy import)

**File**: `engine/src/modes/backtest.py`
**Line**: 407

```python
def _compute_metrics(self) -> None:
    ...
    import numpy as np
```

numpy is imported at the top of `ml_backtest.py` and `tuning/backtest.py` but lazily inside
`_compute_metrics()` in the main backtest module. This is called once per backtest run so
performance is negligible, but it is inconsistent with the rest of the codebase.

**Recommendation**: Move `import numpy as np` to file-level imports.

---

### MED-3: _backtest_mode_task ML A/B test uses empty signals

**File**: `engine/src/main.py`
**Lines**: 2025-2029

```python
ml_backtester = MLSignalBacktester(ml_scorer=None)
ml_ab_result = ml_backtester.ab_test(
    signals=[],
    prices=np.array([1.0]),
    features=None,
)
```

This always produces a trivial result (`comparison_valid=False`, all zeros). The code
correctly demonstrates the wiring and graceful fallback, but it is functionally a no-op
integration test embedded in production code. When a real ML scorer is eventually provided,
the empty `signals=[]` will need to be replaced with actual signal data.

**Risk**: None now (correctly returns comparison_valid=False). This is a placeholder.

**Recommendation**: Add a `# TODO(Phase K): wire real signals from backtest result` comment.

---

### MED-4: _PROJECT_ROOT path resolution is fragile

**File**: `engine/src/api/routes/backtest.py`
**Line**: 14

```python
_PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent.parent.parent
```

Five levels of `.parent` is brittle. If the file moves or the directory structure changes,
this silently points to the wrong location. The same pattern in `main.py` (line 1996) uses
only three levels which is correct for that file's position.

**Recommendation**: Use a project-level constant or resolve from git root:
```python
import subprocess
_PROJECT_ROOT = pathlib.Path(
    subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
)
```
Or import from a central config module.

---

## Checklist Verification

### Code Quality

| Check | Result | Notes |
|-------|--------|-------|
| Double slippage (PowerLaw in PaperExecutor) | PASS | Not present in any Phase J file |
| ENGINE_ENV value | PASS | No `"development"` usage |
| KRW min_exchanges=3 | PASS | Not modified in Phase J |
| cancel_order order.symbol | PASS | Not modified in Phase J |
| friction prefix auto-strip | PASS | `backtest.py:327` uses `.removeprefix("paper_").removeprefix("sandbox_")` |
| Sharpe sqrt(8760) consistency | **FAIL** | 3/4 files use ddof=1; ml_backtest.py uses ddof=0 (HIGH-2) |
| BacktestResult 3-class rename | PASS | MLBacktestResult, TuningBacktestResult, BacktestResult (modes). Aliases preserve backward compat |
| futures_futures excluded_exchanges | PASS | `["coinone","upbit","bithumb"]` hardcoded as default_factory, overridable via config_loader |
| ML A/B ml_scorer=None fallback | PASS | Returns comparison_valid=False, does not crash |
| API auth on backtest routes | **FAIL** | Missing require_auth (HIGH-1) |
| SQL parameterization | PARTIAL | WHERE clauses use $N binding; LIMIT uses f-string (MED-1) |
| WIRING: create->inject->call | PASS | BacktestMode created in _backtest_mode_task, injected with signal_generator+strategy_manager+db_pool, called via .run() |

### Security

| Check | Result | Notes |
|-------|--------|-------|
| SQL injection | LOW RISK | All WHERE params use $N. LIMIT uses int()-cast f-string (MED-1) |
| API authentication | **FAIL** | /api/backtest/* missing require_auth (HIGH-1) |
| Path traversal | PASS | _RESULTS_FILE is a static constant, no user input in path construction |
| Secrets in code | PASS | No API keys, tokens, or passwords in any changed file |
| Telegram tokens | PASS | Not referenced in Phase J files |
| Log leakage | PASS | No sensitive data logged (only metrics: pnl, sharpe, counts) |

---

## Summary

| Severity | Count | Items |
|----------|-------|-------|
| CRITICAL | 0 | -- |
| HIGH | 2 | HIGH-1 (API auth missing), HIGH-2 (Sharpe ddof inconsistency) |
| MEDIUM | 4 | MED-1 (SQL LIMIT f-string), MED-2 (lazy numpy import), MED-3 (placeholder ML A/B), MED-4 (fragile path) |
| WARN | 0 | -- |
| PASS | 10 files reviewed | -- |

### Verdict: **CONDITIONAL PASS**

HIGH-1 and HIGH-2 must be fixed before release:
1. **HIGH-1**: Add `require_auth` to both `/api/backtest/*` endpoints (1-line fix each)
2. **HIGH-2**: Change `np.std(pnl_arr)` to `np.std(pnl_arr, ddof=1)` in `ml_backtest.py:217`

After these 2 fixes: **Phase J Review Gate = PASS**.

MEDIUM items are non-blocking recommendations for a cleanup commit.
