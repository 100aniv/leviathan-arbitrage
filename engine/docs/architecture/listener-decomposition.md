# `on_execution_result` Listener Decomposition

**Audit Date**: 2026-04-26
**Phase**: 5.0 pre-audit (input to Phase 5.2.4)
**Source**: `engine/src/runtime/risk_execution.py:519-877` (358 LOC)

This document decomposes the existing `on_execution_result(engine, trade_request, execution_result)` god-function into **14 single-responsibility listeners**. (The original Phase 5 plan said 12 — a careful read of the source revealed 14 distinct concerns.)

The decomposition target: `engine/src/listeners/*.py`, each implementing a `Listener` Protocol from `engine/src/ports/listener_port.py`.

---

## 1. Listener Protocol (proposed)

```python
# engine/src/ports/listener_port.py
from typing import Protocol, runtime_checkable
from src.core.models import TradeRequest
from src.execution.types import ExecutionResult

@runtime_checkable
class ExecutionResultListener(Protocol):
    """Single-responsibility post-trade callback.

    Listeners run sequentially in registration order. A failure in one listener
    must not prevent later listeners from running (decorate with try/except at
    the dispatcher).

    Listeners SHOULD be idempotent (the same result may be replayed by
    journal recovery). Listeners MUST NOT mutate the TradeRequest or
    ExecutionResult objects.
    """

    name: str  # for logging + ordering

    def on_execution_result(
        self,
        request: TradeRequest,
        result: ExecutionResult,
    ) -> None: ...
```

Async listeners use `async def on_execution_result`. The dispatcher detects this via `inspect.iscoroutinefunction` and either awaits or schedules via `asyncio.ensure_future`. (Today the function uses `asyncio.ensure_future` for fire-and-forget side-effects like Telegram + CB record_loss.)

---

## 2. Listener catalogue (14)

Listed in execution order with their **current** `risk_execution.py` line ranges.

### 2.1 `LogListener` — header trace

- **Lines**: 521-525
- **Responsibility**: Single `logger.info("Execution result: strategy=%s status=%s", ...)` line at function entry.
- **Dependencies**: stdlib `logging` only.
- **Mock interface**: trivial; record arg pairs for assertion.
- **Idempotency**: trivial.
- **Reads**: `trade_request.strategy_id`, `execution_result.status.value`.
- **Writes**: log only.

### 2.2 `PositionSizeLeakListener` — net BUY/SELL aggregation

- **Lines**: 527-548
- **Responsibility**: Update `engine._position_sizes[symbol]` by netting BUY/SELL of each filled leg. On final flat (`updated == 0`), pop the key. Used by `RiskGuardian` Check #1 (directional exposure).
- **Dependencies**: mutable state `EngineState.position_sizes` (dict[symbol→Decimal]).
- **Mock interface**: in-memory dict equivalent; assert post-mutation contents.
- **Idempotency**: **NOT** idempotent today. Replay would double-count. Phase 5.2.4 should add fill_id dedup OR fold this into `PositionManager` updates (which IS idempotent via WAL).
- **Reads**: `execution_result.status.value`, `execution_result.legs[*].trade.{price,amount}`, `execution_result.legs[*].order.{symbol,side}`.
- **Writes**: `EngineState.position_sizes`.

### 2.3 `PositionManagerListener` — async open/close dispatch

- **Lines**: 549-594
- **Responsibility**: For each filled leg, enqueue (`open_position` | `close_position`) op into `engine._pm_queue` (bounded asyncio.Queue, maxsize=1024). Determines `_is_close_exec` from `metadata.reduceOnly` or `leg_type ∈ {settlement_close, timeout_close}`. Synchronously updates in-memory `PositionManager.update_index_sync` first (so the reconciler sees latest state in the same tick), then async dispatches WAL/Redis writes via `pm_drain_loop`.
- **Dependencies**: `PositionManagerPort.update_index_sync`, `EngineState.pm_queue`.
- **Mock interface**: queue stub + sync-update stub; assert dispatch order.
- **Idempotency**: **idempotent** via PositionManager WAL dedup.
- **Reads**: `legs_info`, `_is_close_exec`.
- **Writes**: `EngineState.pm_queue`, `PositionManager` (sync index then async queue).

### 2.4 `CrossHedgeListener` — delta-neutral position tracking

- **Lines**: 595-627
- **Responsibility**: When a TradeRequest has BUY on one exchange + SELL on another (cross-exchange hedged), tracks `engine._cross_exchange_positions: set[symbol]` and `engine._cross_gross_exposure: Decimal`. On close (reduceOnly or settlement_close), removes/decreases. RiskGuardian Check #3 (total exposure) and Check #10 (max concurrent positions) rely on this.
- **Dependencies**: `EngineState.{cross_exchange_positions, cross_gross_exposure}`.
- **Mock interface**: same.
- **Idempotency**: **NOT** idempotent (additive `+= _leg_gross`). Same fix as PositionSizeLeakListener.
- **Reads**: legs_info BUY/SELL exchange diff, `_is_close`.
- **Writes**: `EngineState.cross_exchange_positions`, `EngineState.cross_gross_exposure`.

### 2.5 `PnLPeakListener` — PnL + peak equity update

- **Lines**: 628-655
- **Responsibility**: Estimates `pnl_raw` from sell proceeds − buy costs if `execution_result.pnl is None`, then `engine._total_pnl += pnl_raw`. Recomputes `current_equity = capital_total + total_pnl` and bumps `_peak_equity` if greater. RiskGuardian Check #2 (drawdown) uses peak.
- **Dependencies**: `EngineState.{total_pnl, peak_equity}`, `Settings.capital.initial_capital`, `len(_exchanges)`.
- **Mock interface**: same.
- **Idempotency**: **NOT** idempotent. Should fold into PnLLedger (Path-B Day-1) once it covers paper mode too.
- **Reads**: `execution_result.pnl`, `execution_result.legs[*].trade.{price,amount}`, `execution_result.legs[*].order.side`.
- **Writes**: `EngineState.total_pnl`, `EngineState.peak_equity`.

### 2.6 `MarketRecorderListener` — TimescaleDB execution record

- **Lines**: 656-692
- **Responsibility**: Build `(buy_exchange, sell_exchange, symbol, buy_price, sell_price, size, net_pnl, status, mode)` from legs and call `engine._market_recorder.record_execution(...)`. Skips if no `_market_recorder` or no legs. Used for dashboard + WFA backtest input.
- **Dependencies**: `MarketRecorderPort.record_execution`.
- **Mock interface**: spy/stub; assert dict argument.
- **Idempotency**: depends on `record_execution` impl; assume yes (DB-side dedup OK).
- **Reads**: `_buy_legs`, `_sell_legs`, `getattr(execution_result, "pnl", 0)`, `engine._live_mode._execution_mode`.
- **Writes**: TimescaleDB.

### 2.7 `ExposureListener` — async exposure tracker update

- **Lines**: 694-719
- **Responsibility**: For each filled leg, `asyncio.create_task(engine._exposure_tracker.update_exposure(exchange_id, base_asset, delta))`. Logs (does not propagate) task exceptions via `add_done_callback`. RiskGuardian Check #4e (net exposure per asset, Amendment 7) consumes this.
- **Dependencies**: `ExposureTrackerPort.update_exposure` (async, Redis-backed).
- **Mock interface**: AsyncMock; assert call args.
- **Idempotency**: depends on tracker impl; assume yes (Redis SET semantics).
- **Reads**: `execution_result.legs[*].order.{symbol, side, exchange_id}`, `execution_result.legs[*].trade.amount`.
- **Writes**: ExposureTracker (Redis).

### 2.8 `SlippageListener` — feedback loop

- **Lines**: 721-732
- **Responsibility**: For each leg with `expected_price` and `fill_price`, `engine._slippage_feedback.record_fill(expected_price, actual_price, side)`. Pure observation — feeds the SlippageFeedbackLoop EWMA calibration (US-115).
- **Dependencies**: `SlippageFeedbackPort.record_fill`.
- **Mock interface**: spy.
- **Idempotency**: yes (EWMA is monotonic).
- **Reads**: `execution_result.legs[*].{expected_price, fill_price, order.side}`.
- **Writes**: SlippageFeedbackLoop EWMA state.

### 2.9 `CorrelationListener` — pair correlation

- **Lines**: 733-739
- **Responsibility**: `engine._correlation_monitor.record_trade_pnl(strategy_id, pnl)` per execution. RiskGuardian Check #9 (strategy correlation) — currently log-only.
- **Dependencies**: `CorrelationMonitorPort.record_trade_pnl`.
- **Mock interface**: spy.
- **Idempotency**: depends; assume yes.
- **Reads**: `trade_request.strategy_id`, `execution_result.pnl` (fallback to `trade_request.expected_profit_usdt`).
- **Writes**: CorrelationMonitor rolling window.

### 2.10 `TCAListener` — transaction cost analysis

- **Lines**: 740-777
- **Responsibility**: For each filled leg, `engine._tca_analyzer.record_execution(expected_price, fill_price, latency_ms, filled_ratio, strategy_id, signal_ts, fill_ts)`. Pulls `signal_ts` from `trade_request.timestamp` (US-329 timing decomposition).
- **Dependencies**: `TCAAnalyzerPort.record_execution`.
- **Mock interface**: spy.
- **Idempotency**: yes (rolling window).
- **Reads**: `trade_request.legs[*].price`, `execution_result.legs[*].trade.price`, `execution_result.execution_duration_ms`, `trade_request.timestamp`.
- **Writes**: TCAAnalyzer state.

### 2.11 `TradeHistoryListener` — dashboard API surface

- **Lines**: 778-797
- **Responsibility**: Append a flat dict to `engine.context.trade_history`. Bounded list pruning lives elsewhere; this listener just appends. Used by `/api/trades` REST endpoint.
- **Dependencies**: `engine.context.trade_history` (list).
- **Mock interface**: list with `.append` spy.
- **Idempotency**: NOT idempotent (would double-append on replay). For Path-B compatibility, this can be removed and replaced by reads from `ExecutionJournal` + `MarketRecorder`.
- **Reads**: full TradeRequest + ExecutionResult.
- **Writes**: `EngineContext.trade_history`.

### 2.12 `CircuitBreakerListener` — win/loss feedback

- **Lines**: 799-820
- **Responsibility**: On `success`: `record_win()` if `pnl >= 0`; `record_loss(drawdown_pct)` if `pnl < 0`. On `rolled_back | rollback_failed | timeout`: count as loss. On `rejected`: do nothing (US-DW1 — infrastructure rejects must not poison the consecutive-loss counter).
- **Dependencies**: `CircuitBreakerPort.record_win / record_loss`.
- **Mock interface**: AsyncMock; assert call sequence.
- **Idempotency**: NOT idempotent (consecutive-loss counter). Needs fill_id dedup.
- **Reads**: `execution_result.status.value`, `execution_result.pnl`, `engine._total_pnl`, `Settings.capital.initial_capital`, `len(_exchanges)`.
- **Writes**: CircuitBreaker state.

### 2.13 `RollbackListener` — strategy + position cleanup

- **Lines**: 822-860
- **Responsibility**: On `rolled_back | rejected` status:
  1. Determine entry vs exit rollback via `metadata.reduceOnly` / `leg_type` (BUG-95 fix).
  2. Call `strategy.handle_entry_rollback(symbol)` or `strategy.handle_exit_rollback(symbol)` to release `_open_positions[symbol]` (BUG-J + BUG-31).
  3. Reverse `engine._position_sizes[symbol]` (WS-3.3 leak fix).
  4. NOTE: `rollback_failed` is intentionally NOT cleared — stranded position still exists.
- **Dependencies**: `StrategyManagerPort.get_strategy`, `EngineState.position_sizes`.
- **Mock interface**: stub strategy with handle_*_rollback spy.
- **Idempotency**: yes (no-op when symbol absent).
- **Reads**: `execution_result.status.value`, `trade_request.legs[*].{symbol, metadata, price, size}`.
- **Writes**: `Strategy._open_positions`, `EngineState.position_sizes`.

### 2.14 `TelegramFillListener` — Korean fill notification

- **Lines**: 862-876
- **Responsibility**: On `success` only: `asyncio.ensure_future(engine._trade_bot.send_fill_kr(fill_data))` with strategy_id, symbol, exchanges, size, pnl, timestamp. Non-fatal on failure (US-DW8).
- **Dependencies**: `TelegramPort.send_fill_kr`.
- **Mock interface**: AsyncMock spy.
- **Idempotency**: NOT idempotent (would double-notify). Phase 5.2.4 should add a `notified_fill_ids` set.
- **Reads**: full TradeRequest + ExecutionResult.
- **Writes**: Telegram side-effect.

---

## 3. Migration sequence (Phase 5.2.4)

The order should minimise paper canary risk and enable progressive verification.

### Stage 1 — pilot (2 listeners, LOW risk)

1. **`SlippageListener`** (722-732) — pure observation, no mutable state, easiest to test. Extract first to validate the dispatcher pattern.
2. **`CorrelationListener`** (733-739) — same shape, second pilot for confidence.

**Verification**: paper canary 5 minute → confirm `engine._slippage_feedback._ewma` and `engine._correlation_monitor._window` still update.

### Stage 2 — sink listeners (3 listeners, LOW risk)

3. **`MarketRecorderListener`** (656-692) — TimescaleDB write only.
4. **`TCAListener`** (740-777) — analyzer state only.
5. **`TradeHistoryListener`** (778-797) — context list append.

**Verification**: paper canary 10 minute → assert `SELECT count(*) FROM trades_executed` increments and `/api/trades` returns rows.

### Stage 3 — async/Telegram (2 listeners, MED risk)

6. **`ExposureListener`** (694-719) — Redis async write.
7. **`TelegramFillListener`** (862-876) — async Telegram side-effect.

**Verification**: paper canary 10 min → confirm Telegram fill messages still send AND `engine._exposure_tracker.snapshot()` reflects positions.

### Stage 4 — circuit breaker (1 listener, MED risk)

8. **`CircuitBreakerListener`** (799-820) — feedback loop must keep all 4 status branches.

**Verification**: synthetic test that injects `success+pnl<0`, `success+pnl>0`, `rolled_back`, `timeout`, `rejected` and asserts CB state. Then paper canary 30 min.

### Stage 5 — state mutation (4 listeners, **HIGH risk**)

9. **`PositionSizeLeakListener`** (527-548) — touches `_position_sizes`.
10. **`CrossHedgeListener`** (595-627) — touches `_cross_exchange_positions` + `_cross_gross_exposure`.
11. **`PnLPeakListener`** (628-655) — touches `_total_pnl` + `_peak_equity`.
12. **`PositionManagerListener`** (549-594) — touches `_pm_queue`.

These four ALL must be migrated together because they share the leg iteration loop and depend on `EngineState`. Best handled by introducing `EngineState` first (Phase 5.2.1), then extracting all four under a shared `EngineStateMutationGroup` listener that calls them in order.

**Verification**: paper canary 1 hour with synthetic-trade injection — assert PnL/peak/position_sizes match expected after 50 fills.

### Stage 6 — rollback + log (2 listeners, MED risk)

13. **`RollbackListener`** (822-860) — strategy state mutation.
14. **`LogListener`** (521-525) — trivial, can go anywhere.

**Verification**: synthetic test that injects rolled_back / rejected and asserts strategy `_open_positions` cleared.

---

## 4. Listener registration shape (proposed)

```python
# engine/src/runtime/risk_execution.py (after Phase 5.2.4)
from src.listeners import (
    LogListener, PositionSizeLeakListener, PositionManagerListener,
    CrossHedgeListener, PnLPeakListener, MarketRecorderListener,
    ExposureListener, SlippageListener, CorrelationListener,
    TCAListener, TradeHistoryListener, CircuitBreakerListener,
    RollbackListener, TelegramFillListener,
)
from src.listeners.dispatcher import ExecutionResultDispatcher

def build_execution_result_dispatcher(
    state: EngineState,
    *,
    settings: Settings,
    strategy_manager: StrategyManagerPort,
    position_manager: PositionManagerPort | None,
    market_recorder: MarketRecorderPort | None,
    exposure_tracker: ExposureTrackerPort | None,
    slippage_feedback: SlippageFeedbackPort | None,
    correlation_monitor: CorrelationMonitorPort | None,
    tca_analyzer: TCAAnalyzerPort | None,
    trade_history: list,
    circuit_breaker: CircuitBreakerPort | None,
    telegram: TelegramPort | None,
    live_mode: LiveModePort | None,
) -> ExecutionResultDispatcher:
    listeners = [
        LogListener(),
        PositionSizeLeakListener(state),
        PositionManagerListener(state, position_manager),
        CrossHedgeListener(state),
        PnLPeakListener(state, settings),
        MarketRecorderListener(market_recorder, live_mode),
        ExposureListener(exposure_tracker),
        SlippageListener(slippage_feedback),
        CorrelationListener(correlation_monitor),
        TCAListener(tca_analyzer),
        TradeHistoryListener(trade_history),
        CircuitBreakerListener(circuit_breaker, state, settings),
        RollbackListener(strategy_manager, state),
        TelegramFillListener(telegram),
    ]
    return ExecutionResultDispatcher(listeners)
```

The `Engine.__init__` calls `build_execution_result_dispatcher(...)` once and stores the dispatcher. `_on_execution_result` becomes:

```python
def _on_execution_result(self, request, result) -> None:
    self._dispatcher.dispatch(request, result)
```

(2 lines vs 358 LOC today.)

---

## 5. What this enables

| Capability | Today | After Phase 5.2.4 |
|------------|-------|---------------------|
| Unit-testing one concern in isolation | Impossible (must mock 14 attrs on Engine) | `PnLPeakListener(state, settings)` — pure stub, trivial |
| Adding a 15th post-trade listener | Edit 358-LOC function | Append to dispatcher list |
| Replaying ExecutionJournal events | Re-execute the full god-function (broken: non-idempotent) | Run the dispatcher with a synthetic ExecutionResult — idempotent listeners only |
| Disabling Telegram in tests | Patch `engine._trade_bot` (leaks) | Pass `TelegramFillListener(None)` (no-op) |
| Inspecting listener order | Read 358 lines | `[type(l).__name__ for l in dispatcher.listeners]` |

---

## 6. Industry alignment notes

- **Hummingbot's `OrderFilledEvent` → strategy `_emit_*` chain** is structurally identical to the dispatcher pattern. Each strategy registers handlers; the connector emits one event class per state. We can adopt the same naming: `OnFill`, `OnRollback`, `OnReject`, `OnTimeout`. The current single `on_execution_result` covers all states; splitting into typed events is an optional Phase 5.2.5+ refinement.
- **NautilusTrader's `MessageBus` topic-subscribe** — equivalent if we wanted listeners pluggable at runtime. For now, compile-time dispatcher list is sufficient and simpler.
- **LEAN's `OnData → OnOrderEvent → OnEndOfTimeStep`** pipeline — closest analogue. Each listener corresponds to a "post-event hook" in their parlance. They explicitly forbid mutating the event payload, which we replicate via the Protocol contract.
