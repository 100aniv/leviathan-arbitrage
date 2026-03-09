# US-047 Code Review: Adaptive Threshold + Regime Detector

**Reviewer**: code-reviewer (opus)
**Date**: 2026-03-09
**Files Reviewed**: 5
**Total Issues**: 7

---

## Stage 1: Spec Compliance

### Acceptance Criteria Verification

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| 1 | adaptive_threshold.py: 매 1시간 MIN_EDGE 미세조정 | PASS | `AdaptiveThreshold.adjust()` implements hourly edge adjustment logic (line 28-62). Caller is responsible for 1-hour scheduling. |
| 2 | WR < 50% -> edge 상향, WR > 90% -> edge 하향 | PASS | Lines 40-43: `win_rate < 0.5` increases, `win_rate > 0.9` decreases, clamped to [min_edge, max_edge] |
| 3 | regime_detector.py: LOW/MEDIUM/HIGH/CRISIS 분류 | PASS | `MarketRegime` enum (line 13-17) + `RegimeDetector.detect()` (line 32-65) classifies by `np.std(returns)` |
| 4 | CRISIS -> KillSwitch 발동 | PASS | `should_kill_switch()` returns `True` when `current_regime == CRISIS` (line 67-69) |
| 5 | 변경 이력 TimescaleDB 저장 | PARTIAL | `save_history()` exists in both files using `conn.executemany()`, but no DB migration exists (see MEDIUM #1) |

**Stage 1 Verdict**: PASS with one gap (migration file missing).

---

## Stage 2: Code Quality

### LSP Diagnostics

| File | Diagnostics |
|------|-------------|
| `engine/src/tuning/adaptive_threshold.py` | 0 errors, 0 warnings |
| `engine/src/tuning/regime_detector.py` | 0 errors, 0 warnings |
| `engine/src/tuning/__init__.py` | 0 errors, 0 warnings |
| `engine/tests/unit/tuning/test_adaptive_threshold.py` | 0 errors, 0 warnings |
| `engine/tests/unit/tuning/test_regime_detector.py` | 0 errors, 0 warnings |

### Test Results

- **24 tests passed** (12 adaptive_threshold + 12 regime_detector), 0 failures
- Coverage: `adaptive_threshold.py` 91%, `regime_detector.py` 93%
- Uncovered lines: save_history error paths (expected for exception handlers)

---

## Issues

### [MEDIUM] #1 -- Missing DB migration for new tables

**File**: N/A (missing file)
**Issue**: `save_history()` in both modules writes to `adaptive_threshold_log` and `regime_detector_log` tables, but no SQL migration file exists. The only migration is `001_init_schema.sql` which defines `orderbook_snapshots`, `execution_log`, and `ohlcv_1m`. At runtime, `save_history()` will silently fail because the catch-all `except Exception` swallows the "relation does not exist" error.
**Fix**: Add `engine/src/infra/db/migrations/002_tuning_logs.sql` with:
```sql
CREATE TABLE IF NOT EXISTS adaptive_threshold_log (
    timestamp TIMESTAMPTZ NOT NULL,
    old_edge   DOUBLE PRECISION NOT NULL,
    new_edge   DOUBLE PRECISION NOT NULL,
    win_rate   DOUBLE PRECISION NOT NULL,
    trades     INTEGER NOT NULL,
    PRIMARY KEY (timestamp)
);
SELECT create_hypertable('adaptive_threshold_log', 'timestamp', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS regime_detector_log (
    timestamp   TIMESTAMPTZ NOT NULL,
    old_regime  TEXT NOT NULL,
    new_regime  TEXT NOT NULL,
    volatility  DOUBLE PRECISION NOT NULL,
    spread_std  DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (timestamp)
);
SELECT create_hypertable('regime_detector_log', 'timestamp', if_not_exists => TRUE);
```

---

### [MEDIUM] #2 -- save_history does not clear history after successful write

**File**: `engine/src/tuning/adaptive_threshold.py:64-86`
**File**: `engine/src/tuning/regime_detector.py:71-95`
**Issue**: After `conn.executemany()` succeeds, `self.history` is never cleared. If `save_history()` is called on a periodic schedule (e.g., every hour), the same rows will be re-inserted on every call. The `ON CONFLICT DO NOTHING` clause prevents duplicates in the DB, but every cycle re-serializes and transmits the entire accumulated history list, causing unbounded memory growth and wasted I/O.
**Fix**: Add `self.history.clear()` after the successful `executemany` call in both files:
```python
await conn.executemany(...)
self.history.clear()  # prevent re-insertion and unbounded growth
```

---

### [MEDIUM] #3 -- Untyped `conn` parameter in save_history

**File**: `engine/src/tuning/adaptive_threshold.py:64`
**File**: `engine/src/tuning/regime_detector.py:71`
**Issue**: `async def save_history(self, conn) -> None` has no type annotation for `conn`. This bypasses static analysis and makes the API contract unclear. The existing codebase (`src/infra/db/timescale.py:101`) also uses untyped `conn`, but this is an opportunity to improve. The project uses asyncpg.
**Fix**: Add type hint:
```python
from asyncpg import Connection

async def save_history(self, conn: Connection) -> None:
```
Or use a Protocol if you want to keep asyncpg as an optional dependency:
```python
from typing import Protocol, Any

class DBConnection(Protocol):
    async def executemany(self, query: str, args: list[tuple]) -> Any: ...
```

---

### [MEDIUM] #4 -- Timestamp stored as ISO string, not datetime object

**File**: `engine/src/tuning/adaptive_threshold.py:47`
**File**: `engine/src/tuning/regime_detector.py:50`
**Issue**: History entries store `datetime.now(timezone.utc).isoformat()` (a string). When passed to `conn.executemany()` with a `TIMESTAMPTZ` column, asyncpg expects a `datetime` object, not an ISO string. This will either cause a type error at runtime or require implicit parsing, depending on the driver version.
**Fix**: Store the datetime object directly:
```python
"timestamp": datetime.now(timezone.utc),
```

---

### [LOW] #5 -- Dual logging imports in adaptive_threshold.py

**File**: `engine/src/tuning/adaptive_threshold.py:4,7`
**Issue**: Both `import logging` (line 4) and `import structlog` (line 7) are imported. `structlog` is used for the main logger (line 9), but `logging.getLogger` is used in the except block (line 88). This creates inconsistent log output -- normal operations use structured logging, but errors use stdlib formatting. `regime_detector.py` consistently uses only stdlib `logging`.
**Fix**: Choose one logger consistently. Since structlog is already set up:
```python
# Remove: import logging
# In except block, use:
logger.error("adaptive_threshold.save_history failed", exc_info=exc)
```

---

### [LOW] #6 -- Missing boundary tests for WR=50% and WR=90%

**File**: `engine/tests/unit/tuning/test_adaptive_threshold.py`
**Issue**: Tests check `win_rate=0.45` (below 50%), `win_rate=0.70` (between), and `win_rate=0.95` (above 90%). But the exact boundary values WR=0.5 and WR=0.9 are not tested. The spec says "WR < 50% -> up" and "WR > 90% -> down", meaning WR=50% and WR=90% should leave edge unchanged. This is an important boundary condition.
**Fix**: Add two boundary tests:
```python
def test_wr_exactly_50_percent_keeps_edge_unchanged(self):
    at = AdaptiveThreshold(initial_edge_bps=10.0, min_edge=2.0, max_edge=50.0)
    before = at.current_edge_bps
    at.adjust(win_rate=0.5, total_trades=20)
    assert at.current_edge_bps == before

def test_wr_exactly_90_percent_keeps_edge_unchanged(self):
    at = AdaptiveThreshold(initial_edge_bps=10.0, min_edge=2.0, max_edge=50.0)
    before = at.current_edge_bps
    at.adjust(win_rate=0.9, total_trades=20)
    assert at.current_edge_bps == before
```

---

### [LOW] #7 -- spread_std parameter accepted but unused in classification

**File**: `engine/src/tuning/regime_detector.py:32`
**Issue**: `detect()` accepts `spread_std: float = 0.0` as a parameter and stores it in history, but it is never used in the regime classification logic. The regime is determined solely by `np.std(returns)`. If spread_std is intended for future use, this is acceptable but should be documented. If it should influence classification, the logic is incomplete.
**Fix**: Add a docstring note clarifying this is reserved for future multi-signal classification:
```python
Args:
    spread_std: Reserved for future multi-signal regime classification.
                Currently logged but not used in regime decision.
```

---

## By Severity

| Severity | Count | Details |
|----------|-------|---------|
| CRITICAL | 0 | -- |
| HIGH | 0 | -- |
| MEDIUM | 4 | #1 missing migration, #2 unbounded history, #3 untyped conn, #4 timestamp type mismatch |
| LOW | 3 | #5 dual logging, #6 boundary tests, #7 unused spread_std |

---

## Positive Observations

1. **Clean architecture**: Both classes are single-responsibility, stateless between calls (except accumulated history), and easy to test.
2. **Correct clamp logic**: `min()` and `max()` correctly enforce edge boundaries in `adjust()`.
3. **Good test coverage**: 91-93% coverage, well-structured TDD-style test classes with clear docstrings.
4. **Proper enum pattern**: `MarketRegime(str, Enum)` enables both comparison and JSON serialization.
5. **Safe defaults**: `total_trades < 10` guard prevents volatile adjustments on small samples.
6. **ON CONFLICT DO NOTHING**: Prevents duplicate insertion errors on retry.
7. **Proper `__init__.py` exports**: Clean alphabetical ordering, all new symbols exported.

---

## Recommendation

**COMMENT** -- No blocking issues. All 4 MEDIUM issues should be addressed before merging to prevent runtime failures (especially #1 missing migration and #4 timestamp type). None require architectural changes; all are localized fixes.

### Priority Order for Fixes
1. **#1** (migration) + **#4** (timestamp type) -- these will cause `save_history()` to silently fail at runtime
2. **#2** (history clear) -- unbounded memory growth in long-running processes
3. **#3** (type annotation) -- improves maintainability
4. **#5, #6, #7** -- low priority polish
