# Phase S2 Engine Wiring — Code Review

**Reviewer**: Jennie (code-reviewer/opus)
**Date**: 2026-03-14
**Files Reviewed**: 7 source + 1 test
**Total Issues**: 14

## Scope

| US | Title | Files |
|----|-------|-------|
| US-129 | PortfolioState 실제값 주입 | `main.py`, `guardian.py` |
| US-130 | DynamicSizer 연결 | `signal.py`, `main.py` |
| US-131 | RegimeDetector + ONNX 주입 | `main.py`, `signal.py` |
| US-132 | LegResult expected_price/fill_price | `executor.py` |
| US-133 | IOC Live 연결 | `atomic.py`, `main.py` |
| US-134 | filled_ratio property | `executor.py`, `tca.py`, `correlation_monitor.py` |
| US-153 | Idempotency key | `atomic.py` |
| US-154 | max_concurrent_positions CHECK #10 | `guardian.py` |
| US-155 | Graceful shutdown | `main.py` |
| — | Telegram context alerts | `telegram.py`, `test_telegram.py` |

---

## Stage 1: Spec Compliance

| US | Verdict | Notes |
|----|---------|-------|
| US-129 | PARTIAL | 8 fields populated, but `_peak_equity=0` means drawdown check disabled until first trade |
| US-130 | PARTIAL | DynamicSizer wired, but hardcoded win_prob/win_loss_ratio — not "real values" |
| US-131 | PASS | HMM → threshold fallback → None. ONNX graceful ImportError. Correct. |
| US-132 | PASS | expected_price/fill_price populated in all 5 LegResult construction sites |
| US-133 | FAIL | AtomicOrderExecutor initialized but **never wired to any execution path** |
| US-134 | PASS | `filled_ratio` property + TCA `getattr` fallback = backward compatible |
| US-153 | PARTIAL | Idempotency works but bucket boundary allows duplicates 2s apart |
| US-154 | PASS | CHECK #10 added, env-var override, test coverage present |
| US-155 | PASS | Live-only cancel, per-exchange error handling, telegram alert on failure |

---

## Stage 2: Code Quality

### By Severity

| Severity | Count |
|----------|-------|
| CRITICAL | 1 |
| HIGH | 4 |
| MEDIUM | 5 |
| LOW | 4 |

---

### Issues

#### [CRITICAL] Silent exception swallowing in position tracking

**File**: `engine/src/main.py:930-931`
**Issue**: `except Exception: pass` silently swallows ALL errors in position tracking — the component that feeds RiskGuardian. If `trade.price * trade.amount` produces a wrong type, or `order.side` enum access fails, position tracking silently dies. RiskGuardian then receives stale `_position_sizes` indefinitely, believing exposure is lower than reality. This defeats the purpose of US-129.
**Fix**: At minimum, log the exception:
```python
except Exception as exc:
    logger.warning("position_tracking_error: %s", exc, exc_info=True)
```
Consider splitting the try/except to isolate position update from peak-equity update — a peak-equity error shouldn't prevent position tracking.

---

#### [HIGH] `_peak_equity` initialized to 0 — drawdown check disabled on startup

**File**: `engine/src/main.py:109`
**Issue**: `_peak_equity = Decimal("0")`. Drawdown is computed as `(peak - current) / peak`. Since peak=0, the `if self._peak_equity > 0` branch never fires until after the first successful trade. This means CHECK #2 (drawdown limit) is **completely disabled** for the initial burst of trades — exactly when you'd want protection most (cold start, unknown market conditions).
**Fix**: Initialize `_peak_equity` to the initial capital in `_build_risk_check_fn` or during engine start:
```python
# In __init__ or start()
self._peak_equity = capital * max(len(self._exchanges), 1)
```

---

#### [HIGH] AtomicOrderExecutor created but never used (US-133 incomplete)

**File**: `engine/src/main.py:795-805`
**Issue**: `self._atomic_order_executor = AtomicOrderExecutor(timeout_ms=1000)` is created in live mode, but no code path ever calls `self._atomic_order_executor.execute()`. The trade execution flow still routes through the existing `AtomicExecutor` (in `executor.py`). US-133 requires IOC execution in live mode — the executor is instantiated but dead code.
**Fix**: Wire `_atomic_order_executor` into the trade consumer or `AtomicExecutor` as the order submission layer for live mode. Without this wiring, IOC-with-market-fallback never activates.

---

#### [HIGH] DynamicSizer uses hardcoded parameters instead of real data

**File**: `engine/src/core/signal.py:250-254`
**Issue**: `win_prob=Decimal("0.6")`, `win_loss_ratio=Decimal("1.5")`, `strategy_used_capital=Decimal("0")` are all hardcoded constants. US-130 requires "DynamicSizer 연결" — connection to real sizing. With `strategy_used_capital=0`, the per-strategy allocation limit in `PositionSizer.compute_size()` is never triggered (line 80: `remaining_strategy = max_strategy_value - 0` = always positive). The Kelly fraction with fixed 60%/1.5x produces a constant output, defeating the "dynamic" purpose.
**Fix**:
- `win_prob` / `win_loss_ratio`: Source from SlippageFeedbackLoop or TCAAnalyzer historical data
- `strategy_used_capital`: Track per-strategy capital from `Engine._position_sizes` (filter by strategy_id)
- If historical data unavailable at startup, document the hardcoded defaults with a TODO and env-var override

---

#### [HIGH] Idempotency bucket boundary allows near-boundary duplicates

**File**: `engine/src/execution/atomic.py:79`
**Issue**: `timestamp_bucket = int(time.time() / 300)` creates hard 5-minute boundaries. Two identical orders at t=299s and t=301s (2 seconds apart, same signal_id) fall in different buckets and **won't be deduped**. In a high-frequency arbitrage engine, signals repeat within seconds.
**Fix**: Drop the timestamp bucket from the key entirely. Store `signal_id` with its actual timestamp:
```python
idem_key = f"{exchange_id}:{symbol}:{signal_id}"
self._cleanup_old_keys()
if idem_key in self._executed_keys:
    # Already executed within the TTL window
    ...
self._executed_keys[idem_key] = time.time()
```
The `_cleanup_old_keys()` with 300s TTL already provides the time-windowing.

---

#### [MEDIUM] `import os as _os` inside `__init__` method

**File**: `engine/src/risk/guardian.py:96`
**Issue**: `import os as _os` is placed inside `__init__`. While Python caches imports, this is unconventional, obscures the dependency, and the `_os` alias is confusing since `os` is a stdlib module. Other modules (e.g., `atomic.py:28`) import `os` at module level.
**Fix**: Move to module-level: `import os` at the top of the file. Use `os.getenv(...)` directly on line 108-109.

---

#### [MEDIUM] Direct private attribute assignment across class boundary

**File**: `engine/src/main.py:790`
**Issue**: `self._signal_generator._dynamic_sizer = self._dynamic_sizer` directly sets a private attribute on `SignalGenerator` from `Engine`. The `SignalGenerator.__init__` already accepts `dynamic_sizer` as a constructor parameter (line 84). This bypass creates two initialization paths and makes the dependency invisible at construction time.
**Fix**: Pass `dynamic_sizer` during `SignalGenerator` construction in `_init_signal_pipeline()`:
```python
self._signal_generator = SignalGenerator(
    ...,
    dynamic_sizer=self._dynamic_sizer,
)
```
Then remove the post-hoc assignment on line 790. Note: `_init_extended_modules()` (where DynamicSizer is created) is called **after** `_init_signal_pipeline()`, so the initialization order needs adjustment — either move DynamicSizer init earlier, or keep the post-hoc assignment but use a setter method.

---

#### [MEDIUM] Docstring says "9 pre-trade checks" but now implements 10+

**File**: `engine/src/risk/guardian.py:1-16`
**Issue**: Module docstring lists checks #0 through #8 (9 checks). CHECK #9 (correlation, US-118) and CHECK #10 (max_concurrent_positions, US-154) are now implemented but not documented in the module header.
**Fix**: Update docstring to reflect the actual check list:
```
#9: Strategy correlation scale-down (US-118) — advisory only
#10: Max concurrent positions (US-154)
```

---

#### [MEDIUM] `_position_sizes` dict has no size bound or cleanup

**File**: `engine/src/main.py:108, 910-919`
**Issue**: `_position_sizes` grows with each new symbol traded. SELL operations remove entries only when position reaches exactly 0. Over a long-running session with 175+ symbols, this dict grows without bound. While each entry is small (string key + Decimal value), it also inflates CHECK #10 (concurrent positions) because closed positions with residual dust values (e.g., `Decimal("0.000001")`) are never cleaned up.
**Fix**: Use a threshold for cleanup — positions below a minimum notional (e.g., $0.01) should be removed:
```python
if updated < Decimal("0.01"):
    self._position_sizes.pop(symbol, None)
```

---

#### [MEDIUM] `volatility_1min` and `volatility_24h` always empty

**File**: `engine/src/main.py:870-871`
**Issue**: Both volatility dicts are hardcoded to `{}` with a comment "populated when live vol data available." This means CHECK #7 (volatility guard) is **permanently bypassed** — the `if vol_1min is not None and vol_24h is not None` condition at `guardian.py:251` never fires.
**Fix**: If volatility data is not yet available, document this as a known gap. Consider connecting to the VolatilityTracker or adding a TODO with the specific Phase/US where this will be addressed.

---

#### [LOW] Redundant `getattr` for known attributes

**File**: `engine/src/main.py:902-904`
**Issue**: `getattr(execution_result, "legs", [])` and `getattr(leg, "trade", None)` — `ExecutionResult.legs` and `LegResult.trade` are declared dataclass fields that always exist. `getattr` is defensive but misleading about the type contract.
**Fix**: Use direct attribute access: `execution_result.legs`, `leg.trade`, `leg.order`.

---

#### [LOW] Missing return type annotation

**File**: `engine/src/main.py:835`
**Issue**: `def _build_risk_check_fn(self):` has no return type. The function returns a `Callable[[TradeRequest], tuple[bool, str]]`.
**Fix**: Add type hint:
```python
def _build_risk_check_fn(self) -> Callable:
```

---

#### [LOW] Telegram cancel alert uses emoji in string literal

**File**: `engine/src/main.py:1064`
**Issue**: `f"⚠️ 주문 취소 실패: ..."` — other log/alert calls in the codebase don't use emoji. Minor inconsistency.
**Fix**: Remove emoji or standardize emoji usage across alerts.

---

#### [LOW] Test coverage gap for position tracking in `_on_execution_result`

**File**: `engine/tests/unit/test_phase_s2_risk.py`
**Issue**: Tests cover `PortfolioState` construction and CHECK #10 in `RiskGuardian`, but no test covers the `Engine._on_execution_result` position tracking logic (lines 899-931 of main.py) — BUY accumulation, SELL reduction, peak equity update, and the drawdown calculation path.
**Fix**: Add integration-level tests that call `_on_execution_result` with mock trade results and verify `_position_sizes` and `_peak_equity` are updated correctly.

---

## Double-Slippage Verification

**PASS**: No new slippage application found. `DynamicSizer` adjusts `trade_size` only (position sizing), not slippage. `CEXOrderbookSlippage` in the friction filter remains the sole slippage source. `PaperExecutor` changes were not included in this diff (no `paper.py` changes detected).

---

## Backward Compatibility

| Change | Compatible? | Notes |
|--------|------------|-------|
| `PortfolioState` new fields | YES | All new fields have no defaults, but all callers updated |
| `LegResult.expected_price/fill_price` | YES | Optional fields with `None` default |
| `LegResult.filled_ratio` property | YES | New property, no naming conflict (`fill_ratio` is a method with different signature) |
| `SignalGenerator(dynamic_sizer=)` | YES | Optional kwarg with `None` default |
| `AtomicOrderExecutor.execute(signal_id=)` | YES | Optional kwarg with `""` default |
| `RiskGuardian(max_concurrent_positions=)` | YES | Optional kwarg with default `20` |
| Existing test `make_portfolio()` helpers | RISK | `test_guardian.py:52-64` doesn't include new fields — will break if PortfolioState removes defaults |

**Checked**: `test_guardian.py:52-64` `make_portfolio()` still works because `PortfolioState` fields that were previously required (`total_exposure`, `position_sizes`, etc.) are still passed. No breakage detected.

---

## LSP Diagnostics

| File | Errors |
|------|--------|
| `engine/src/main.py` | 0 |
| `engine/src/risk/guardian.py` | 0 |
| `engine/src/core/signal.py` | 0 |
| `engine/src/execution/executor.py` | 0 |
| `engine/src/execution/atomic.py` | 0 |
| `engine/src/infra/telegram.py` | 0 |

All type checks pass.

---

## Recommendation

### REQUEST CHANGES

**Blocking issues (must fix before merge):**

1. **[CRITICAL]** `except Exception: pass` in position tracking (`main.py:930`) — at minimum add logging
2. **[HIGH]** `_peak_equity = 0` disables drawdown protection on startup — initialize to capital
3. **[HIGH]** `AtomicOrderExecutor` created but never wired (US-133 incomplete)
4. **[HIGH]** Idempotency bucket boundary allows near-boundary duplicates

**Should fix (strong recommendation):**

5. **[HIGH]** DynamicSizer hardcoded params — at least `strategy_used_capital` should be real
6. **[MEDIUM]** Direct `_dynamic_sizer` private attr assignment — use constructor or setter
7. **[MEDIUM]** Guardian docstring stale (9 checks → 10+)

**Estimated fix effort**: 2-3 hours for CRITICAL + HIGH items.
