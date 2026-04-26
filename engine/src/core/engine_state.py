"""EngineState — Phase 5.2.1 Mutable runtime state quarantine (2026-04-26).

Phase 5.0 pre-audit (engine-state-design.md) 기반 16 mutable runtime fields 분리.

설계 원칙:
- Settings (frozen, boot 시 1회) vs EngineState (runtime mutated) vs Singletons (참조)
- 모든 필드는 Engine.__init__에서 EngineState 인스턴스로 위임
- Engine 클래스에는 backward-compat property로 self._total_pnl ↔ state.total_pnl

Phase 5.2.4 listener 분리 시 Listener는 EngineState 인스턴스 받음 → mutation 명시적.
Replay/snapshot tooling은 EngineState 단일 객체 pickle 가능.
"""
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
    """

    # --- Lifecycle flags ---
    running: bool = False
    kill_switch_active: bool = False
    shutdown_event: asyncio.Event = field(default_factory=asyncio.Event)
    background_tasks: list[asyncio.Task[Any]] = field(default_factory=list)

    # --- PnL & equity ---
    total_pnl: Decimal = Decimal("0")
    peak_equity: Decimal | None = None
    """initialised to capital_total on first risk check."""

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

    # --- Phase 5.2.4 listener migration target ---
    # 16 mutable fields total — Phase 5.2.4 listeners take EngineState arg explicitly.

    def reset(self) -> None:
        """Phase 5 test fixture: reset all mutable state to defaults.

        Lifecycle/PnL/Position counters reset. Singletons NOT touched.
        """
        self.running = False
        self.kill_switch_active = False
        self.shutdown_event = asyncio.Event()
        self.background_tasks.clear()
        self.total_pnl = Decimal("0")
        self.peak_equity = None
        self.position_sizes.clear()
        self.cross_exchange_positions.clear()
        self.cross_gross_exposure = Decimal("0")
        self.exchange_health.clear()
        self.position_tracking_errors = 0
        self.pm_drain_errors = 0
        self.prev_reconciler_orphans.clear()
        self.regime_pnl_history.clear()
        self.regime_last_pnl = 0.0

    def snapshot(self) -> dict[str, Any]:
        """Phase 5 audit: immutable snapshot for replay tooling."""
        return {
            "running": self.running,
            "kill_switch_active": self.kill_switch_active,
            "total_pnl": str(self.total_pnl),
            "peak_equity": str(self.peak_equity) if self.peak_equity else None,
            "position_sizes": {k: str(v) for k, v in self.position_sizes.items()},
            "cross_exchange_positions": sorted(self.cross_exchange_positions),
            "cross_gross_exposure": str(self.cross_gross_exposure),
            "exchange_health": {k: str(v) for k, v in self.exchange_health.items()},
            "position_tracking_errors": self.position_tracking_errors,
            "pm_drain_errors": self.pm_drain_errors,
            "prev_reconciler_orphans": sorted(self.prev_reconciler_orphans),
            "regime_pnl_history_len": len(self.regime_pnl_history),
            "regime_last_pnl": self.regime_last_pnl,
        }
