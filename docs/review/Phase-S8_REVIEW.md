# Phase S8: System Integration Hardening -- Code Review

**Reviewer**: Jennie (code-reviewer/opus)
**Date**: 2026-03-15
**Verdict**: **REQUEST CHANGES** (2 CRITICAL, 3 HIGH, 5 MEDIUM, 4 LOW)

---

## Code Review Summary

**Files Reviewed**: 15 source files + 1 test file
**Total Issues**: 14
**LSP Diagnostics**: 0 errors across all 11 modified source files

### By Severity
- **CRITICAL**: 2 (must fix before merge)
- **HIGH**: 3 (should fix before merge)
- **MEDIUM**: 5 (consider fixing)
- **LOW**: 4 (optional improvements)

---

## Stage 1: Spec Compliance

### US-169: Live Mode Loop -- PASS (with caveats)
- `_live_mode_loop()` correctly mirrors `_real_data_feed_loop` with MultiStrategySignalProducer
- Routes via `REAL_AUTHENTICATED` DataMode
- **Caveat**: AtomicExecutor routing is implicit (via event bus), not explicit wiring -- acceptable

### US-170: TriangularScanner -- PASS
- Initialized in `_init_signal_pipeline()` with correct `min_profit_bps`
- Orderbook callback wired in both `_real_data_feed_loop` and `_live_mode_loop`
- Cycles routed to `MultiStrategySignalProducer.produce_triangular_signal()`

### US-171: KRW Staleness -> KillSwitch -- **PARTIAL** (see CRITICAL #2)
- 3x debounce logic correct (3 consecutive stale checks at 30s intervals = ~90s)
- Telegram alerts on block/unblock -- good
- **Problem**: Uses full KillSwitch.trigger() which halts the ENTIRE engine, not just KRW

### US-172: ML Scorer Integration -- PASS
- NaN/Inf defense via `math.isfinite()`
- Soft filter with debug logging on rejection
- Confidence modulated by `ml_score` -- correct

### US-173: RegimeDetector Background Loop -- PASS (with concerns)
- 60s periodic detection loop
- CRISIS 30min timeout with fallback to HIGH -- correct
- `max(adaptive_edge, regime_edge)` follows QUANT GATE guidance
- **Concern**: Returns series is a single PnL snapshot, not actual returns (see MEDIUM #1)

### US-174: AdaptiveThreshold -- PASS
- 1-hour periodic loop with env-configurable interval
- Win rate sourced from shadow stats or trade history
- Updates `SignalConfig.min_edge` at runtime

### US-175: ExposureTracker -- PASS (with concern)
- Redis-backed, initialized only when Redis available
- Updates on successful fills via `_on_execution_result`
- **Concern**: Uses `asyncio.ensure_future()` (see HIGH #2)

### US-176: Correlation Scale-Down -- PASS
- `DynamicSizer.set_correlation_scale()` with clamping to [0.0, 1.0]
- `RiskGuardian` propagates correlation events to DynamicSizer
- Correctly integrated into `compute_dynamic_size()` combined multiplier

### US-177: DEX Adapter Config -- PASS
- Correctly switched from positional args to `UniswapV3Config` object
- Matches `UniswapV3Adapter.__init__(self, config: UniswapV3Config)` signature

### US-178: IOC Limit Orders -- **FAIL** (see CRITICAL #1)
- Binance: correct (`timeInForce: "IOC"`)
- Bybit: correct (`timeInForce: "IOC"`)
- OKX: **WRONG** (`"force": "ioc"` -- nonexistent parameter)
- Bitget: **MISSING** (no `place_ioc_limit` implementation)

### US-179: ScheduledTuner Hot-Reload -- PASS
- Atomic write via `tempfile.mkstemp` + `os.rename` -- correct
- Reload callback wired in `_init_tuner()`
- Merges with existing params -- good

### US-180: EventBus Limits -- PASS
- `EVENT_BUS_MAXSIZE` env var support
- 80% capacity warning
- Prometheus metrics (queue depth gauge, dropped counter)

### multi_signal.py: funding_rate_min_diff_bps 5->30 -- PASS
- Matches funding_rate.py default -- consistent

### funding_rate.py: Slippage cost estimation -- PASS
- 20bps round-trip conservative estimate -- reasonable
- Prevents structural losses from friction underestimation

---

## Stage 2: Code Quality Issues

### CRITICAL Issues

#### [CRITICAL-1] OKX IOC Limit Order: Wrong API Parameter Name
**File**: `/Users/100aniv/development/arbitrage_OMC/engine/src/infra/exchange/native_okx.py:187`
**Issue**: The OKX API v5 does not have a `"force"` parameter. The correct way to submit an IOC order on OKX is to set `"ordType": "ioc"` (not `"ordType": "limit"` + `"force": "ioc"`). The OKX API will silently ignore the unknown `"force"` field and submit a regular GTC limit order instead.
**Impact**: In LIVE mode, IOC orders on OKX will behave as regular limit orders that remain on the book indefinitely. This defeats the purpose of IOC (avoid unfilled orders hanging) and creates unmanaged open order risk.
**Evidence**: OKX API v5 documentation confirms the `ordType` parameter accepts values: `market`, `limit`, `post_only`, `fok`, `ioc`. There is no separate `force` or `timeInForce` parameter.
**Fix**:
```python
# BEFORE (wrong):
body: dict = {
    "instId": self._normalize_symbol(symbol),
    "tdMode": "cash",
    "side": side.lower(),
    "ordType": "limit",       # <-- should be "ioc"
    "px": str(price),
    "sz": str(size),
    "force": "ioc",           # <-- nonexistent parameter, silently ignored
}

# AFTER (correct):
body: dict = {
    "instId": self._normalize_symbol(symbol),
    "tdMode": "cash",
    "side": side.lower(),
    "ordType": "ioc",         # OKX uses ordType directly for IOC
    "px": str(price),
    "sz": str(size),
}
```

#### [CRITICAL-2] KRW Staleness Triggers Full KillSwitch (Engine-Wide Halt)
**File**: `/Users/100aniv/development/arbitrage_OMC/engine/src/modes/shadow.py:1690-1694`
**Issue**: When KRW rate goes stale for 3 consecutive checks, the code calls `self._kill_switch.trigger()`. The `KillSwitch.trigger()` method (defined in `kill_switch.py:184-219`) executes a **full 3-tier sequence**: Tier 1 (local halt via threading.Event), Tier 2 (cancel ALL orders on ALL exchanges), Tier 3 (close ALL positions). This is not a "KRW soft-block" -- it kills the entire engine.
**Impact**: A temporary KRW rate data feed outage (which is common -- Upbit/Bithumb APIs are flaky) would halt ALL trading on ALL exchanges, including perfectly healthy non-KRW pairs on Binance/Bybit/OKX.
**Evidence**: `kill_switch.py:184-219` shows `trigger()` calls `_tier1_local_halt()`, `_tier2_cancel_orders()`, `_tier3_close_positions()` sequentially. The `_krw_stale` flag at line 745 already filters KRW orderbooks from signal generation. The KillSwitch is overkill.
**Fix**: Do NOT call `KillSwitch.trigger()`. The existing `_krw_stale` flag (line 745) already prevents KRW signals from being generated. Instead, add a dedicated per-exchange soft-block mechanism:
```python
# Option A: Just use the existing _krw_stale flag (already works!)
# Remove the kill_switch.trigger() call entirely.
# The _krw_stale check at line 745 already blocks KRW orderbooks.

# Option B: If you want extra safety, disable KRW exchange adapters:
if self._krw_stale_count >= 3 and not self._krw_soft_blocked:
    self._krw_soft_blocked = True
    logger.warning("shadow_mode.krw_soft_block_activated", stale_seconds=elapsed)
    # Do NOT trigger KillSwitch -- just log + alert
    if self._telegram is not None:
        asyncio.create_task(self._telegram.send_alert(
            f"KRW rate stale {elapsed:.0f}s -- KRW exchanges soft-blocked",
            level="WARNING",
        ))
```

---

### HIGH Issues

#### [HIGH-1] Bitget Missing `place_ioc_limit()` Implementation
**File**: `/Users/100aniv/development/arbitrage_OMC/engine/src/infra/exchange/native_bitget.py`
**Issue**: The `ExchangeOrderAPI` protocol (defined in `atomic.py:21-24`) requires `place_ioc_limit()`. Binance, Bybit, and OKX all implement it, but Bitget does not. If `AtomicOrderExecutor` is used with a Bitget adapter, it will raise `AttributeError` at runtime.
**Impact**: Live trading on Bitget via `AtomicOrderExecutor` will crash with an unhandled exception.
**Fix**: Add `place_ioc_limit()` to `native_bitget.py`, or add a `hasattr` check in `AtomicOrderExecutor.execute()` before calling it.

#### [HIGH-2] `asyncio.ensure_future()` for Fire-and-Forget Coroutines
**File**: `/Users/100aniv/development/arbitrage_OMC/engine/src/main.py:1174`
**Issue**: `asyncio.ensure_future()` is used for exposure tracking updates. If the coroutine raises an exception, it produces "Task exception was never retrieved" warnings in logs. Additionally, the created tasks are not tracked, so they cannot be cancelled during shutdown.
**Impact**: Noisy logs during shutdown; potential for unobserved exception warnings that obscure real errors.
**Fix**: Use `asyncio.create_task()` and either store the reference or add a done callback:
```python
task = asyncio.create_task(
    self._exposure_tracker.update_exposure(exchange_id, base_asset, Decimal(str(delta)))
)
task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
```

#### [HIGH-3] `_regime_detect_loop` Uses Single PnL Snapshot as "Returns"
**File**: `/Users/100aniv/development/arbitrage_OMC/engine/src/main.py:1780-1782`
**Issue**: The regime detector receives `[pnl_snapshot]` -- a single cumulative PnL value, not a time series of returns. Most regime detection algorithms (HMM, etc.) expect a series of period returns (e.g., `[0.01, -0.003, 0.005, ...]`), not a single absolute value. A single value provides no distributional information for regime classification.
**Impact**: RegimeDetector will either fail silently, produce meaningless regime classifications, or always return the same regime. This undermines US-173's goal of regime-adaptive trading.
**Fix**: Maintain a rolling window of PnL deltas:
```python
# In __init__:
self._pnl_history: list[float] = []
self._last_pnl_snapshot: float = 0.0

# In _regime_detect_loop:
pnl_snapshot = float(self._total_pnl)
delta = pnl_snapshot - self._last_pnl_snapshot
self._last_pnl_snapshot = pnl_snapshot
if abs(delta) > 1e-10:
    self._pnl_history.append(delta)
    if len(self._pnl_history) > 60:  # Keep last 60 observations (1 hour at 60s intervals)
        self._pnl_history = self._pnl_history[-60:]
if len(self._pnl_history) >= 10:
    self._regime_detector.detect(self._pnl_history)
```

---

### MEDIUM Issues

#### [MEDIUM-1] `_regime_detect_loop` Calls Both `detect()` and `predict()` with Bare `except: pass`
**File**: `/Users/100aniv/development/arbitrage_OMC/engine/src/main.py:1784-1791`
**Issue**: The code calls both `detect()` and `predict()` in sequence with bare `except: pass` blocks, hoping one of them exists. This is a code smell -- the caller should know its dependency's interface.
**Fix**: Check `hasattr()` before calling, or unify the interface with a single method name:
```python
if hasattr(self._regime_detector, 'detect'):
    self._regime_detector.detect(returns)
elif hasattr(self._regime_detector, 'predict'):
    self._regime_detector.predict(returns)
```

#### [MEDIUM-2] `_tuner_reload_callback` Accesses Private `_config` Attribute
**File**: `/Users/100aniv/development/arbitrage_OMC/engine/src/main.py:526-531`
**Issue**: The hot-reload callback accesses `self._signal_generator._config.min_edge` -- a private attribute of `SignalGenerator`. This creates tight coupling. If `SignalGenerator` changes its internal structure, the callback silently breaks.
**Fix**: Add a public method to `SignalGenerator`:
```python
# In SignalGenerator:
def set_min_edge(self, new_edge: Decimal) -> None:
    self._config.min_edge = new_edge

# In callback:
self._signal_generator.set_min_edge(new_edge)
```

#### [MEDIUM-3] `_adaptive_threshold_loop` Also Accesses Private `_config` and `_stats`
**File**: `/Users/100aniv/development/arbitrage_OMC/engine/src/main.py:1813-1831`
**Issue**: Similar to MEDIUM-2. Accesses `self._shadow_mode._stats` (private) and `self._signal_generator._config.min_edge` (private). Two instances of tight coupling with internal state.
**Fix**: Add public accessor methods: `ShadowMode.get_stats()` and `SignalGenerator.set_min_edge()`.

#### [MEDIUM-4] `place_ioc_limit` Not in `NativeAdapter` Base Class
**File**: `/Users/100aniv/development/arbitrage_OMC/engine/src/infra/exchange/native_adapter.py`
**Issue**: `place_ioc_limit()` is implemented in 3 of 7 concrete adapters (Binance, Bybit, OKX) but not declared in the `NativeAdapter` ABC. This means there's no compile-time guarantee that new adapters will implement it. The `ExchangeOrderAPI` Protocol in `atomic.py` defines the interface separately, creating a parallel hierarchy.
**Fix**: Add `place_ioc_limit` as an abstract method to `NativeAdapter` (or at minimum a default implementation that raises `NotImplementedError`).

#### [MEDIUM-5] Empty `except Exception: pass` Blocks in Exposure Tracking
**File**: `/Users/100aniv/development/arbitrage_OMC/engine/src/main.py:1182-1183`
**Issue**: The US-175 exposure tracking wraps the entire update in `except Exception: pass`. While the comment says "Non-critical," silently swallowing ALL exceptions (including `TypeError`, `AttributeError` from API mismatches) makes debugging impossible.
**Fix**: At minimum log the exception:
```python
except Exception as exc:
    logger.debug("exposure_tracking_error (non-critical): %s", exc)
```

---

### LOW Issues

#### [LOW-1] `import time` Inside Method Bodies
**Files**: `native_binance.py:283`, `native_bybit.py:198`, `native_okx.py:178`
**Issue**: `import time` is placed inside `place_ioc_limit()` method bodies. The `time` module is already imported at the module level in all three files.
**Fix**: Remove the redundant `import time` statements from method bodies.

#### [LOW-2] `_tuner_reload_callback` Defined as Closure Inside `_init_tuner`
**File**: `/Users/100aniv/development/arbitrage_OMC/engine/src/main.py:512-535`
**Issue**: The callback is defined as a nested function with `import json` and `import pathlib` inside. These modules could be imported at the top level. The closure captures `self` implicitly, which is fine but makes the callback harder to test independently.
**Fix**: Consider making this a method: `def _on_tuner_params_reloaded(self) -> None`.

#### [LOW-3] ScheduledTuner `_reload_callback` Set via Direct Attribute Assignment
**File**: `/Users/100aniv/development/arbitrage_OMC/engine/src/main.py:536`
**Issue**: `self._scheduled_tuner._reload_callback = _tuner_reload_callback` directly sets a private-ish attribute. The underscore prefix suggests it's not intended for external assignment.
**Fix**: Use a setter method or constructor parameter:
```python
# Option A: Constructor parameter
self._scheduled_tuner = ScheduledTuner(reload_callback=_tuner_reload_callback)

# Option B: Public setter
self._scheduled_tuner.set_reload_callback(_tuner_reload_callback)
```

#### [LOW-4] Hardcoded Slippage Estimate in funding_rate.py
**File**: `/Users/100aniv/development/arbitrage_OMC/engine/src/strategies/funding_rate.py:96`
**Issue**: `Decimal("20") / Decimal("10000")` (20bps round-trip slippage) is hardcoded. While conservative and documented, it should ideally come from configuration.
**Fix**: Move to `FundingRateConfig`:
```python
class FundingRateConfig(BaseModel):
    est_round_trip_slippage_bps: Decimal = Field(default=Decimal("20"), ge=Decimal("0"))
```

---

## Shadow Test Results Alignment

The Shadow results (PnL: +$1.85, WR: 92.2%, 8,689 trades, 0 crashes, 35.8 min) are consistent with the code changes:
- `funding_rate_v1: 0 trades` confirms the 30bps threshold correctly blocks unprofitable funding rate trades
- 0 crashes confirms the defensive coding (try/except) is working
- The KillSwitch was NOT triggered during shadow (KRW rate stayed fresh), so CRITICAL-2 was not exercised

---

## API Contract Review

### Breaking Changes: None detected
- `DynamicSizer.__init__` gains `_correlation_scales` but no constructor signature change
- `RiskGuardian.__init__` gains optional `dynamic_sizer` parameter (backward compatible)
- `ShadowMode.__init__` gains optional `kill_switch` parameter (backward compatible)
- `InMemoryEventBus.__init__` changes `maxsize: int = 10000` to `maxsize: int | None = None` -- backward compatible (None uses same default)

### Protocol Compliance
- `ExchangeOrderAPI` protocol requires `place_ioc_limit()` -- Bitget does NOT comply (HIGH-1)
- `NativeAdapter` ABC does NOT declare `place_ioc_limit()` -- inconsistency (MEDIUM-4)

### Backward Compatibility: GOOD
- All new parameters are optional with sensible defaults
- No existing signatures broken
- `multi_signal.py` `funding_rate_min_diff_bps` default change (5->30) affects new instances only

---

## Recommendation

### **REQUEST CHANGES**

**Must fix before merge (CRITICAL)**:
1. **CRITICAL-1**: Fix OKX IOC parameter (`"force": "ioc"` -> `"ordType": "ioc"`)
2. **CRITICAL-2**: Remove `KillSwitch.trigger()` from KRW staleness handler (existing `_krw_stale` flag already provides the soft-block)

**Should fix before merge (HIGH)**:
3. **HIGH-1**: Add `place_ioc_limit()` to Bitget adapter (or guard in AtomicOrderExecutor)
4. **HIGH-2**: Replace `asyncio.ensure_future()` with `asyncio.create_task()` + exception handling
5. **HIGH-3**: Fix `_regime_detect_loop` to use actual return deltas, not single PnL snapshot

**After fixing CRITICAL and HIGH issues**, re-run Shadow for 10min to verify no regressions, then proceed to TF QF.

---

## Files Reviewed

| File | Lines Changed | US Coverage |
|------|--------------|-------------|
| `engine/src/main.py` | +190 | US-169,170,173,174,175,177,179 |
| `engine/src/core/signal.py` | +40 | US-172,173 |
| `engine/src/core/multi_signal.py` | +1 | funding_rate fix |
| `engine/src/execution/sizer.py` | +8 | US-176 |
| `engine/src/risk/guardian.py` | +8 | US-176 |
| `engine/src/modes/shadow.py` | +50 | US-171 |
| `engine/src/strategies/funding_rate.py` | +6 | friction fix |
| `engine/src/infra/exchange/native_binance.py` | +32 | US-178 |
| `engine/src/infra/exchange/native_bybit.py` | +28 | US-178 |
| `engine/src/infra/exchange/native_okx.py` | +28 | US-178 |
| `engine/src/tuning/scheduled_tuner.py` | +65 | US-179 |
| `engine/src/infra/redis/memory_bus.py` | +30 | US-180 |
| `engine/tests/unit/tuning/test_scheduled_tuner.py` | +3 | test fix |

---

*Review by Jennie (code-reviewer/opus) -- Phase S8 System Integration Hardening*
