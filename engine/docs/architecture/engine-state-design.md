# `EngineState` Design — Phase 5.2.1 Mutable Field Quarantine

**Audit Date**: 2026-04-26
**Phase**: 5.0 pre-audit (input to Phase 5.2.1)
**Source files audited**: `engine/src/main.py:96-202` (Engine.__init__), plus mutating sites across `engine/src/runtime/*.py` and `engine/src/main.py`.

This document inventories every `engine._X` attribute set in `Engine.__init__`, classifies each by lifecycle, and proposes the `EngineState` dataclass that replaces them.

The goal of Phase 5.2.1: **separate immutable `Settings` from mutable `EngineState` from runtime singletons** so that:
- `Settings` is `frozen=True` and passed by value semantics.
- `EngineState` is the single object holding mutable counters/maps; `EngineState` field accesses become explicit (`state.peak_equity` not `engine._peak_equity`).
- Runtime singletons (adapters, executors, ports) are obtained via DI rather than `engine._*` god-object pull.

---

## 1. Inventory of `engine._*` attributes (from `main.py:96-202`)

Every mutable attribute set in `Engine.__init__` is listed below, classified by lifecycle phase.

### 1.1 Lifecycle — control flow

| Attribute | Default in __init__ | Mutated by | Group |
|-----------|----------------------|------------|-------|
| `_shutdown_event` | `asyncio.Event()` | `stop()`, `_handle_signal`, `redis_halt_watch_loop` | mutable state |
| `state` (NOT prefixed) | `EngineState()` (legacy local dataclass at main.py:72-77 with `running`, `kill_switch_active`, `background_tasks`) | `run() / stop()` | mutable state — **already encapsulated**, used as model for the larger refactor |

The existing `state: EngineState` field on Engine (main.py:72-77) is the *seed* of this refactor. Phase 5.2.1 grows it to absorb everything below.

### 1.2 Configuration — set once, read many

| Attribute | Default | Source | Group |
|-----------|---------|--------|-------|
| `_settings` | `None` (set in `_init_config`) | `get_settings()` from `src.core.config` | **immutable Settings** |
| `_active_exchanges` | `load_engine_config().get("exchanges").get("active", [])` | `engine.json` | **immutable Settings** (computed) |
| `_data_mode` | `DataMode.SYNTHETIC` (overridden in `start_background_tasks`) | resolved from EngineMode | **immutable Settings** (per-run) |
| `_engine_mode` | (not set in __init__; set in `start_background_tasks`) | resolved by `resolve_engine_mode` | **immutable Settings** (per-run) |

### 1.3 Infrastructure — runtime singletons

| Attribute | Default | Set by | Group |
|-----------|---------|--------|-------|
| `_event_bus` | `None` | `init_infrastructure` | runtime singleton |
| `_db_pool` | `None` | `init_database` | runtime singleton |
| `_redis_client` | `None` | `init_infrastructure` | runtime singleton |
| `_telegram` | `None` | `init_telegram` | runtime singleton |
| `_http_client` | `None` | `init_infrastructure` | runtime singleton |
| `_collector_manager` | `None` | `real_data_feed_loop` / `paper_mode_loop` | runtime singleton |
| `_market_recorder` | `None` | `init_infrastructure` | runtime singleton |
| `_data_quality_manager` | `None` | `init_risk` | runtime singleton |
| `_kill_switch` | `None` | `paper_mode_loop` / `progressive_shadow_loop` | runtime singleton |
| `_recovery_manager` | `None` | `init_execution` | runtime singleton |
| `_position_recovery` | `None` | `init_execution` | runtime singleton |
| `_position_reconciler` | `None` | `init_execution` | runtime singleton |
| `_supervisor` | `None` | `Engine.run()` opt-in | runtime singleton |
| `_scheduled_tuner` | `None` | `init_tuner` | runtime singleton |
| `_min_notional_registry` | (not in __init__; set in `live_mode_loop`/`paper_mode_loop`) | mode loops | runtime singleton |
| `_pnl_snapshot` / `_pnl_ledger` / `_pnl_reconciler` | (not in __init__; set in `live_mode_loop`) | live mode | runtime singleton |
| `_backtest_result` | `None` | `backtest_mode_task` | runtime singleton |

### 1.4 Pipeline — runtime singletons

| Attribute | Default | Set by | Group |
|-----------|---------|--------|-------|
| `_exchanges` | `{}` | `init_exchanges` | runtime singleton (dict but written-once) |
| `_price_hub` | `None` | `init_signal_pipeline` | runtime singleton |
| `_cost_calculator` | `None` | `init_signal_pipeline` | runtime singleton |
| `_cost_feedback` | (not in __init__) | `init_signal_pipeline` | runtime singleton |
| `_signal_generator` | `None` | `init_signal_pipeline` | runtime singleton |
| `_strategy_manager` | `None` | `init_strategies` | runtime singleton |
| `_triangular_scanner` | `None` | `init_signal_pipeline` | runtime singleton |
| `_multi_signal_producer` | `None` | mode loops | runtime singleton |
| `_executor` | `None` | `init_execution` | runtime singleton |
| `_trade_consumer` | `None` | `init_execution` | runtime singleton |
| `_position_manager` | `None` | `init_execution` | runtime singleton |
| `_pm_queue` | (not in __init__; set in `init_execution`) | bounded asyncio.Queue | mutable infra |
| `_pm_drain_task` | `None` | `init_execution` | mutable infra |
| `_balance_tracker` | `None` | `init_execution` | runtime singleton |
| `_paper_mode` | `None` | `paper_mode_loop` | runtime singleton |
| `_live_mode` | (not in __init__; set in `live_mode_loop`) | live mode | runtime singleton |
| `_live_gate` | `None` | `paper_mode_loop` | runtime singleton |
| `_trade_bot` | `None` | `init_telegram` | runtime singleton |
| `_telegram_cmd_handler` | `None` | (legacy, removed Phase S21) | runtime singleton |

### 1.5 Risk — runtime singletons

| Attribute | Default | Set by | Group |
|-----------|---------|--------|-------|
| `_risk_guardian` | `None` | `init_risk` | runtime singleton |
| `_circuit_breaker` | `None` | `init_risk` | runtime singleton |
| `_per_strategy_cb` | (not in __init__; set in `init_risk`) | `init_risk` | runtime singleton |
| `_correlation_monitor` | `None` | `init_risk` | runtime singleton |
| `_slippage_feedback` | `None` | `init_execution` | runtime singleton |
| `_dynamic_sizer` | `None` | `init_execution` | runtime singleton |
| `_tca_analyzer` | `None` | `init_execution` | runtime singleton |
| `_rebalancer` | `None` | `init_execution` | runtime singleton |
| `_attribution` | `None` | `populate_context` | runtime singleton |
| `_capital_allocator` | `None` | `populate_context` | runtime singleton |
| `_portfolio_risk` | `None` | `init_risk` | runtime singleton |
| `_flash_guard` | `None` | `init_risk` | runtime singleton |
| `_exposure_tracker` | `None` | `init_risk` | runtime singleton |
| `_regime_detector` | `None` | `init_signal_pipeline` | runtime singleton |
| `_adaptive_threshold` | `None` | `init_signal_pipeline` | runtime singleton |
| `_slippage_fb_collector` | (not in __init__) | `init_signal_pipeline` | runtime singleton |

### 1.6 **Mutable runtime state** — the actual EngineState target

These are the attributes that change during operation (not just at boot/shutdown):

| Attribute | Default | Mutated by | Lifecycle |
|-----------|---------|------------|-----------|
| `_position_sizes` | `{}` (dict[str, Decimal]) | `on_execution_result.PositionSizeLeakListener` (BUY adds, SELL nets), rollback path | running |
| `_cross_exchange_positions` | `set()` | `on_execution_result.CrossHedgeListener` | running |
| `_cross_gross_exposure` | `Decimal("0")` | `on_execution_result.CrossHedgeListener` | running |
| `_peak_equity` | `None` (init to capital_total on first risk check) | `on_execution_result.PnLPeakListener`, `peak_equity_persist_loop` | running |
| `_total_pnl` | `Decimal("0")` | `on_execution_result.PnLPeakListener` | running |
| `_exchange_health` | `{}` (dict[str, Decimal]) | `run_health_check` | running |
| `_position_tracking_errors` | (not in __init__; lazy attr from on_execution_result error path) | `on_execution_result` exception path | running |
| `_prev_reconciler_orphans` | (not in __init__; lazy from `_on_reconcile_discrepancy`) | reconciler 60s cycle | running |
| `_pm_drain_errors` | `0` (set in `init_execution`) | `pm_drain_loop` exception | running |
| `_regime_pnl_history` | (not in __init__; lazy from `regime_detect_loop`) | ml_loops.regime_detect_loop | running |
| `_regime_last_pnl` | (not in __init__; lazy from `regime_detect_loop`) | ml_loops.regime_detect_loop | running |
| `state.running` | `False` | `run()` sets True, `stop()` sets False | running |
| `state.kill_switch_active` | `False` | kill switch trigger | running |
| `state.background_tasks` | `[]` | `start_background_tasks` (extend), `stop()` (clear) | running |
| `context.trade_history` | `[]` | `on_execution_result.TradeHistoryListener` | running |
| `context.alert_history` | `[]` | `record_alert` | running |
| `context.realized_pnl` / `context.unrealized_pnl` | (set in dashboard_feed_loop) | dashboard feed | running |

**Total mutable runtime state**: 16 fields. These are the entire scope of Phase 5.2.1's `EngineState`.

---

## 2. Proposed `EngineState` dataclass

```python
# engine/src/core/engine_state.py (NEW)
from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

@dataclass
class EngineState:
    """Mutable runtime state for the LEVIATHAN engine.

    Distinct from ``Settings`` (frozen, set once at boot) and runtime
    singletons (constructed once, reference held). Every field below is
    mutated DURING operation by listeners, loops, or kill-switch paths.

    Phase 5.2.1 scope: this class quarantines the 16 mutable fields that
    were previously direct ``engine._X`` attributes. After migration:

    - Listeners receive ``EngineState`` and mutate its fields explicitly.
    - Read paths (RiskGuardian.check, dashboard_feed_loop) take an
      ``EngineState`` and access via field name, not god-object pull.
    - Replay/snapshot tooling can pickle this single object.

    NOTE: the existing ``Engine.state`` field (main.py:72-77 dataclass with
    running/kill_switch_active/background_tasks) is the seed of this class.
    Phase 5.2.1 absorbs that dataclass into this one.
    """

    # --- Lifecycle flags ---
    running: bool = False
    kill_switch_active: bool = False
    shutdown_event: asyncio.Event = field(default_factory=asyncio.Event)
    background_tasks: list[asyncio.Task[Any]] = field(default_factory=list)

    # --- PnL & equity ---
    total_pnl: Decimal = Decimal("0")
    peak_equity: Decimal | None = None  # initialised to capital_total on first risk check

    # --- Position tracking (RiskGuardian Check #1, #3, #10) ---
    position_sizes: dict[str, Decimal] = field(default_factory=dict)
    """symbol -> net directional exposure (BUY adds, SELL nets)."""

    cross_exchange_positions: set[str] = field(default_factory=set)
    """symbols with active cross-exchange delta-neutral hedges."""

    cross_gross_exposure: Decimal = Decimal("0")
    """Total capital deployed in cross-exchange hedges (both legs)."""

    # --- Health & quality ---
    exchange_health: dict[str, Decimal] = field(default_factory=dict)
    """exchange_id -> health score (0.0-1.0)."""

    # --- Error counters (drive Telegram escalation) ---
    position_tracking_errors: int = 0
    pm_drain_errors: int = 0

    # --- Reconciler state ---
    prev_reconciler_orphans: set[str] = field(default_factory=set)
    """Cross-cycle persistence detector for orphan positions (BUG-164)."""

    # --- ML feedback ---
    regime_pnl_history: list[float] = field(default_factory=list)
    regime_last_pnl: float = 0.0


@dataclass
class EngineHistory:
    """Bounded rolling history surfaces for dashboard.

    Lives separately from EngineState because these are bounded queues
    that other consumers (API, WebSocket) read but listeners only append.
    """

    trade_history: list[dict[str, Any]] = field(default_factory=list)
    alert_history: list[dict[str, Any]] = field(default_factory=list)
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
```

---

## 3. Companion: immutable `Settings` (already exists, formalise scope)

`src/core/config.py:Settings` is already pydantic-frozen (`Settings(BaseSettings)`). Phase 5.2.1's contribution is *what* belongs there vs. EngineState:

| Belongs in `Settings` (immutable, set at boot) | Belongs in `EngineState` (mutable, runtime) |
|------------------------------------------------|----------------------------------------------|
| `capital.initial_capital` | `total_pnl` |
| `operational.execution_mode`, `operational.data_mode`, `engine_mode` | `running`, `kill_switch_active` |
| `operational.api_host`, `api_port` | (n/a) |
| `trading.symbols` | (n/a) |
| `risk.*` thresholds | (computed values like `peak_equity`, `position_sizes`) |
| `RECONCILE_INTERVAL`, `HEALTH_CHECK_INTERVAL`, `HEARTBEAT_INTERVAL`, `SHUTDOWN_TIMEOUT` (Engine class constants) | (n/a) |
| `_active_exchanges` (computed from engine.json) | (n/a — should be a `Settings.active_exchanges` field) |
| `_BTC_REFERENCE_PRICE` (module-level mutable global!) | should move into `EngineState.btc_reference_price` |

The `_BTC_REFERENCE_PRICE` global is a leak today — `btc_price_update_loop` mutates a module-level variable read by `risk_execution._btc_ref_price()`. Phase 5.2.1 should move it onto `EngineState.btc_reference_price` so it follows the same encapsulation rule.

---

## 4. Migration plan for Phase 5.2.1

### 4.1 Step 1 — introduce `EngineState` without removing existing fields

```python
# engine/src/main.py
from src.core.engine_state import EngineState as RuntimeState

class Engine:
    def __init__(self, context=None):
        self.context = context or EngineContext()
        # Bridge: legacy `self.state` and new `self.runtime_state` co-exist.
        self.state = ...  # legacy local dataclass (kept)
        self.runtime_state = RuntimeState()
        # Existing `self._total_pnl` etc. STAY for one commit, are aliased.
```

### 4.2 Step 2 — alias each migrated field with property descriptor

```python
@property
def _total_pnl(self) -> Decimal:
    return self.runtime_state.total_pnl

@_total_pnl.setter
def _total_pnl(self, v: Decimal) -> None:
    self.runtime_state.total_pnl = v
```

**One commit per field** (16 commits — small, reviewable, paper canary verifiable).

### 4.3 Step 3 — flip read sites in priority order

After each property is in place, change call sites to read directly:
- `engine._total_pnl` → `engine.runtime_state.total_pnl`
- `engine._position_sizes` → `engine.runtime_state.position_sizes`
- etc.

(The property remains as a backward-compat shim until all 565 access sites are migrated.)

### 4.4 Step 4 — listener decomposition (Phase 5.2.4) injects `EngineState`

Phase 5.2.4 already takes `EngineState` as a constructor arg per listener-decomposition.md §4. Once listeners are extracted, the property aliases on Engine can be deleted.

### 4.5 Step 5 — `_BTC_REFERENCE_PRICE` global cleanup

Move from `main.py` module level into `EngineState.btc_reference_price`. Update `risk_execution._btc_ref_price()` to take `EngineState` as arg, and `btc_price_update_loop` to mutate `engine.runtime_state.btc_reference_price`.

---

## 5. Industry pattern alignment

### 5.1 NautilusTrader — `Component.fsm_state`

Nautilus has a strict component lifecycle FSM (`PRE_INITIALIZED → READY → STARTED → ...`) per Component, not per engine. Their model: state is component-local, not centralized. `Cache` holds market data, `Portfolio` holds positions, `MessageBus` holds events. Each is bounded.

**Translation to LEVIATHAN**: `EngineState` is acceptable as a transitional centralized object. The longer-term refinement (Phase 5.2.6+) is splitting it: `PortfolioState` (positions, cross-hedges, exposure), `PnLState` (total_pnl, peak_equity), `HealthState` (exchange_health, error counters), `MLState` (regime history).

### 5.2 Hummingbot — `Connector.in_flight_orders` + `OrderTracker`

Hummingbot encapsulates per-order state inside `OrderTracker` (one per connector). The strategy queries the tracker, never mutates connector internals.

**Translation**: our `position_sizes` + `cross_exchange_positions` + `cross_gross_exposure` are equivalent to Hummingbot's `OrderTracker`. Phase 5.2.6 extraction would create `PositionTracker` (one per Engine) replacing the 3 `EngineState` fields, with the listener decomposition feeding it.

### 5.3 LEAN — `IAlgorithm.Securities`, `Portfolio`, `Transactions`

LEAN's algorithm class exposes typed managers (`Securities` for symbol metadata, `Portfolio` for positions/cash, `Transactions` for orders). The `IAlgorithm` interface owns these as immutable references; their internals mutate.

**Translation**: our final form should look like:

```python
class Engine:
    settings: Settings              # frozen
    state: EngineState              # mutable runtime state (Phase 5.2.1)
    portfolio: PortfolioPort        # Phase 5.2.6
    pnl: PnLPort                    # Phase 5.2.6
    health: HealthPort              # Phase 5.2.6
    pipeline: SignalPipeline        # Phase 5.2 ports
    # ...no more `engine._X` attributes
```

Phase 5.2.1 lays the foundation by getting the mutable fields *out* of the god-object first; Phase 5.2.6 (post-Phase-5.4 completion) splits `EngineState` into typed managers per LEAN.

---

## 6. Risk + verification

| Risk | Mitigation |
|------|------------|
| Property descriptor performance hit | Hot-path mutations (`_position_sizes[symbol] = X`) become `state.position_sizes[symbol] = X` — same dict access. The property is one extra getattr per *attribute access*, not per element access; benchmark target is <1% slowdown on 50-trade synthetic loop. |
| Listener migration drops mutation atomicity | All 4 stage-5 listeners share a single `EngineState` object. Use `asyncio.Lock` only if multi-task contention surfaces; today everything runs in the consumer task. |
| Replay non-determinism due to `set()` ordering | `EngineState.cross_exchange_positions` uses `set` — replay tests must compare frozenset for stability. |
| `peak_equity_persist_loop` reads stale state during pickle | Add a `state.snapshot()` method returning a frozen copy for the persist loop. |
| `_BTC_REFERENCE_PRICE` global race | Atomic Decimal assignment is fine in single-event-loop. After move, mark with comment that thread-safety is event-loop-bound. |

**Verification gates** (per the §17 Stage A-H workflow):
1. After step 1 (introduce class): `pytest tests/unit/` 5056+ pass + paper canary 5 min.
2. After each step 2 alias commit: pytest pass.
3. After step 3 read flip per attribute: pytest pass + paper canary 10 min for the 5 high-touch fields (`_total_pnl`, `_peak_equity`, `_position_sizes`, `_cross_exchange_positions`, `_exchange_health`).
4. After step 4: full regression including 1H paper canary with synthetic-trade injection asserting PnL/peak/position arithmetic.

---

## 7. Out of scope for Phase 5.2.1

- Splitting EngineState into PortfolioState + PnLState + HealthState (deferred to 5.2.6).
- Removing module-level `_BTC_REFERENCE_PRICE` if too churny — can be Phase 5.2.7.
- Per-strategy state (`Strategy._open_positions`) — that lives on each strategy already, not on Engine.
- Adapter internal state (`PaperExchangeAdapter._balance_tracker`) — not Engine concern.
- `EngineContext` (api/server.py:EngineContext) — separate dashboard surface, refactored as part of Phase H US-072 line, not Phase 5.
