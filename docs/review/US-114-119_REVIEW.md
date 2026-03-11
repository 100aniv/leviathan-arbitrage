# Code Review: US-114 + US-115 + US-117 + US-118 + US-119 (Wave 3 Batch 1)

**Reviewer:** code-reviewer (opus)
**Date:** 2026-03-12
**Files Reviewed:** 6 source + 5 test
**LSP Diagnostics:** Python — clean (0 errors, 0 warnings across all 6 files)

---

## Summary

**Files Reviewed:** 6 source, 5 test
**Total Issues:** 8

### By Severity
- CRITICAL: 0
- HIGH: 2 (must fix before merge)
- MEDIUM: 4 (should fix)
- LOW: 2 (optional)

---

## Stage 1 — Spec Compliance

### US-114: DynamicSizer (engine/src/execution/sizer.py:152-196)
- confidence × regime × liquidity multipliers: PASS
- Delegates to PositionSizer.compute_size for Kelly base: PASS
- MarketRegime enum with 4 states: PASS
- LOW_VOL=1.5 multiplier (amplifies size in quiet markets): PASS
- Tests cover all regime multipliers, confidence sigmoid, liquidity factor, zero-liquidity guard: PASS

### US-115: SlippageFeedbackLoop (engine/src/risk/slippage.py)
- EMA-based adjustment tracking: PASS
- record_fill records actual vs expected, updates EMA: PASS
- get_adjusted_slippage() returns calibrated bps only: PASS
- No fill_price modification methods present: PASS (confirmed by test_no_apply_fill_price_method)
- NOT wired to SignalGenerator or PaperExecutor: PASS (grep confirms no callers in src/)

### US-117: TelegramCommandHandler (engine/src/infra/telegram_bot.py)
- 5 commands (/status /kill /mode /balance /help): PASS
- Long-poll getUpdates with 30s timeout: PASS
- Callback injection pattern for command handlers: PASS
- stop() signals exit: PASS
- Tests cover all 5 commands, unknown fallback, empty text: PASS

### US-118: CorrelationMonitor (engine/src/risk/correlation_monitor.py)
- Rolling-window Pearson correlation: PASS
- PositionScaleEvent emission above threshold: PASS
- Scales down lower-PnL strategy: PASS
- Guardian integration declared (self.correlation_monitor: CorrelationMonitor | None): PASS (guardian.py:106)
- Tests cover perfect/negative/independent correlations, window guard, scale selection: PASS

### US-119: AtomicOrderExecutor (engine/src/execution/atomic.py)
- IOC limit first, market fallback for remainder: PASS
- asyncio.wait_for timeout wrapper: PASS
- IOC_MIN_FILL_RATIO = 0.95: PASS
- FillQuality metrics aggregation: PASS

**Stage 1 Verdict: All 5 user stories implemented. No missing requirements detected.**

---

## Stage 2 — Code Quality

### LSP Diagnostics
All 6 files: 0 errors, 0 warnings.

---

## Issues

---

### [HIGH] Mid-file `import math` violates module-level import convention

**File:** `engine/src/execution/sizer.py:134`

**Issue:**
```python
import math  # noqa: E402 — placed after class to avoid reordering existing imports
```
`import math` is placed at line 134, after the `PositionSizer` class definition. This is a PEP 8 violation (`E402`), suppressed with a `noqa` comment. The stated justification ("to avoid reordering existing imports") is cosmetic, not technical. Python resolves module-level names at import time, so placing `import math` at line 134 does work, but it creates two problems: (1) any tool that scans module-level imports (type checkers, linters, code navigation) may miss it or flag it; (2) it sets a precedent for mid-module imports that creates maintenance confusion.

**Fix:** Move `import math` to the top of the file alongside the existing standard-library imports. The `from __future__ import annotations` line is already there; `math` belongs directly after it. The `# noqa` comment can be removed.

```python
# engine/src/execution/sizer.py — top of file
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
```

---

### [HIGH] `poll_updates` accesses private `TelegramAlerter` attributes directly

**File:** `engine/src/infra/telegram_bot.py:83-85`

**Issue:**
```python
if not self._alerter._bot_token or not self._alerter._enabled:
    return []
url = f"https://api.telegram.org/bot{self._alerter._bot_token}/getUpdates"
```
`TelegramCommandHandler` directly reads `_bot_token` and `_enabled` — two name-mangled private attributes of `TelegramAlerter`. This is a broken encapsulation boundary. If `TelegramAlerter` renames or restructures its internals (which it owns the right to do), `telegram_bot.py` silently breaks with an `AttributeError`. The test fixture works around this by mocking the private attributes directly (`alerter._bot_token = "bot123:TOKEN"`), confirming the coupling is real.

**Fix:** Add `bot_token: str | None` and `enabled: bool` as public read-only properties on `TelegramAlerter`, then consume those here. Alternatively, expose a `is_configured() -> bool` method and a `bot_token` property on `TelegramAlerter`, eliminating all private attribute access from `telegram_bot.py`.

```python
# In TelegramAlerter (telegram.py):
@property
def bot_token(self) -> str | None:
    return self._bot_token

@property
def is_enabled(self) -> bool:
    return self._enabled

# In telegram_bot.py:
if not self._alerter.bot_token or not self._alerter.is_enabled:
    return []
url = f"https://api.telegram.org/bot{self._alerter.bot_token}/getUpdates"
```

---

### [MEDIUM] `except (asyncio.TimeoutError, Exception)` swallows all exceptions silently

**File:** `engine/src/execution/atomic.py:87-89`

**Issue:**
```python
except (asyncio.TimeoutError, Exception):
    logger.warning("ioc_order_failed", symbol=symbol, side=side, exc_info=True)
    remaining = size
```
`asyncio.TimeoutError` is already a subclass of `Exception` in Python 3.11+, so the tuple is redundant. More critically, catching bare `Exception` here means network errors, exchange rejections, and unexpected runtime bugs all collapse to the same silent path: "fall back to market order and log a warning." If `place_ioc_limit` raises `ValueError` (bad argument) or a `KeyError` (exchange API schema changed), the fallback will silently send a market order against incorrect data. This masks bugs that should surface immediately.

**Fix:** Narrow the except clause to the specific exceptions that represent expected transient failures. Exchange-specific exceptions should be caught at a higher layer if they need special handling.

```python
except asyncio.TimeoutError:
    logger.warning("ioc_order_timeout", symbol=symbol, side=side)
    remaining = size
except httpx.NetworkError as exc:
    logger.warning("ioc_order_network_error", symbol=symbol, side=side, error=str(exc))
    remaining = size
```

---

### [MEDIUM] Market fallback `filled_size` hardcodes `size` regardless of actual market fill

**File:** `engine/src/execution/atomic.py:99-103`

**Issue:**
```python
return OrderResult(
    filled_size=size,          # <-- always reports the full original requested size
    avg_price=market_result.avg_price,
    order_type="market_fallback",
    latency_ms=elapsed,
)
```
In the partial-fill path, `remaining = size - result.filled_size` (the IOC partial amount). The market order is placed for `remaining`, but the returned `OrderResult.filled_size` is set to `size` (the full original order). This silently reports a larger fill than actually occurred if the market order itself partially fills (which is possible on illiquid pairs or exchanges with minimum lot-size constraints).

The correct total filled quantity is `result.filled_size + market_result.filled_size` in the partial path, and `market_result.filled_size` in the full-timeout path.

**Fix:**
```python
# Track IOC partial fill before entering market fallback
ioc_filled = result.filled_size if "result" in locals() else Decimal("0")

market_result = await exchange.place_market(symbol, side, remaining)
total_filled = ioc_filled + market_result.filled_size
...
return OrderResult(
    filled_size=total_filled,
    avg_price=market_result.avg_price,
    order_type="market_fallback",
    latency_ms=elapsed,
)
```
Note: the test `test_total_filled_equals_requested` passes today only because `_market_result` always returns `filled_size == remaining`, which is a mock assumption not guaranteed in production.

---

### [MEDIUM] `poll_updates` does not check Telegram API `ok` field

**File:** `engine/src/infra/telegram_bot.py:91-95`

**Issue:**
```python
async with httpx.AsyncClient(timeout=35) as client:
    resp = await client.get(url, params=params)
    data = resp.json()
    return data.get("result", [])
```
The Telegram Bot API returns `{"ok": false, "error_code": 401, "description": "Unauthorized"}` on auth failures. The code never checks `data["ok"]` or `resp.status_code`. A 401 (bad token) or 429 (rate limit) will silently return `[]` from `data.get("result", [])` and the poll loop will spin at full speed with no backoff, generating noise in logs.

**Fix:**
```python
resp.raise_for_status()
data = resp.json()
if not data.get("ok"):
    logger.warning("telegram_api_error", error=data.get("description"), code=data.get("error_code"))
    return []
return data.get("result", [])
```
Also add exponential backoff in `poll_loop` on consecutive empty/error results to avoid tight spinning.

---

### [MEDIUM] `poll_loop` has no error backoff on repeated failures

**File:** `engine/src/infra/telegram_bot.py:100-113`

**Issue:**
```python
async def poll_loop(self) -> None:
    self._running = True
    while self._running:
        updates = await self.poll_updates()
        for update in updates:
            ...
        if not updates:
            await asyncio.sleep(1)
```
`asyncio.sleep(1)` only triggers when there are no updates. If `poll_updates` raises an exception that is caught internally and returns `[]`, the loop sleeps 1 second then retries. If the Telegram API is down or returning errors repeatedly (e.g. bot token revoked), the loop spins every 1 second indefinitely with no backoff. This creates log spam and unnecessary load.

**Fix:** Track consecutive failure count and apply exponential backoff (cap at 60s).

```python
_consecutive_errors = 0
while self._running:
    try:
        updates = await self.poll_updates()
        _consecutive_errors = 0
    except Exception:
        _consecutive_errors += 1
        await asyncio.sleep(min(2 ** _consecutive_errors, 60))
        continue
    ...
    if not updates:
        await asyncio.sleep(1)
```

---

### [LOW] `LOW_VOL` regime multiplier of 1.5 bypasses `max_single_trade_pct` cap intent

**File:** `engine/src/execution/sizer.py:148, 190-196`

**Issue:**
```python
MarketRegime.LOW_VOL: 1.5,
```
`PositionSizer.compute_size` already caps at `max_single_trade_pct` (default 2%). `DynamicSizer.compute_dynamic_size` then multiplies by up to `1.5 × 1.0 × 1.0 = 1.5`, pushing the final position to potentially 3% of capital — 50% above the configured `max_single_trade_pct` cap. The downstream `PositionSizer` cap is therefore silently violated for `LOW_VOL` regime at full liquidity.

**Fix:** Either re-apply the `max_single_trade_pct` cap inside `compute_dynamic_size` after applying the multipliers, or document explicitly that `DynamicSizer` is permitted to exceed the base sizer's per-trade cap (and update `SizerConfig` to include a `dynamic_max_single_trade_pct`).

---

### [LOW] Wave 3 Prometheus metric names lack the `leviathan_` namespace prefix

**File:** `engine/src/infra/metrics.py:169-194`

**Issue:**
```python
SLIPPAGE_ADJUSTMENT = Gauge("slippage_adjustment_factor", ...)
SLIPPAGE_ERROR = Histogram("slippage_prediction_error", ...)
STRATEGY_CORRELATION = Gauge("strategy_correlation", ...)
IOC_FILL_RATE = Gauge("ioc_fill_rate", ...)
IOC_VS_MARKET = Histogram("ioc_vs_market_slippage_bps", ...)
```
All existing metrics in the file use the `leviathan_` prefix (e.g. `leviathan_order_latency_seconds`, `leviathan_trades_total`). The Phase G metrics break this pattern too (`shadow_stale_orderbook_rejected_total`, `shadow_trade_loss_capped_total`). The Wave 3 metrics continue the inconsistency. In a shared Prometheus/Grafana deployment with other services, unprefixed metric names like `strategy_correlation` or `ioc_fill_rate` can collide with metrics from other services.

**Fix:** Add `leviathan_` prefix to all 5 new metric names.

```python
SLIPPAGE_ADJUSTMENT = Gauge("leviathan_slippage_adjustment_factor", ...)
SLIPPAGE_ERROR = Histogram("leviathan_slippage_prediction_error", ...)
STRATEGY_CORRELATION = Gauge("leviathan_strategy_correlation", ...)
IOC_FILL_RATE = Gauge("leviathan_ioc_fill_rate", ...)
IOC_VS_MARKET = Histogram("leviathan_ioc_vs_market_slippage_bps", ...)
```
Note: this is a breaking change to any existing Grafana dashboards targeting these metric names. If Wave 3 dashboards have not yet been deployed, this is the right time to fix.

---

## Positive Observations

- **Double-slippage guard is solid.** `SlippageFeedbackLoop.get_adjusted_slippage()` has a prominent docstring warning, zero callers outside its own module (grep confirmed), and a dedicated test `test_no_apply_fill_price_method` asserting the API surface does not expose price manipulation. This is the right design.

- **`CorrelationMonitor.pearson()` handles all degenerate cases.** Returns `None` for n<2, zero-variance series, and empty inputs. All edge cases are tested.

- **`DynamicSizer` is fully composable.** The delegation to `PositionSizer.compute_size` as the base step means all existing Kelly/capital-tier constraints are preserved before the dynamic multipliers are applied. Clean layering.

- **`AtomicOrderExecutor` uses `Protocol` for exchange dependency.** Avoids coupling to any specific exchange implementation. Testable via `AsyncMock` without a real exchange.

- **Test coverage is comprehensive.** All 5 new modules have dedicated unit test files. `SlippageFeedbackLoop` tests include a specific guard against `apply_fill_price` methods existing on the class.

---

## Verdict

**REQUEST CHANGES**

2 HIGH issues must be resolved before merge:

1. `sizer.py:134` — Move `import math` to top of file.
2. `telegram_bot.py:83-85` — Replace private attribute access with public `TelegramAlerter` properties.

The remaining MEDIUM issues (atomic.py filled_size accounting, telegram poll error handling, poll backoff) are correctness and resilience concerns that should be addressed before production load. The LOW issues (LOW_VOL cap bypass, metric naming) can be addressed in a follow-up but are worth tracking.

No CRITICAL issues found. No hardcoded secrets. No double-slippage violation detected.
