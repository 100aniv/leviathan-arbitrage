"""LEVIATHAN Blueprint Compliance Audit.

Verifies engine adherence to the 5 core design principles,
safety requirements, and operational standards.

Produces a ComplianceReport with PASS/FAIL/PARTIAL per item.
FAIL items should be converted to Phase 4 work items.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import structlog

# Load .env so os.getenv() can find LEG_TIMEOUT_MS, TELEGRAM_BOT_TOKEN, etc.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Status enum
# ---------------------------------------------------------------------------


class ComplianceStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    PARTIAL = "PARTIAL"
    SKIPPED = "SKIPPED"  # when prerequisite not available


# ---------------------------------------------------------------------------
# ComplianceItem dataclass
# ---------------------------------------------------------------------------


@dataclass
class ComplianceItem:
    """Single compliance check result."""

    category: str          # e.g. "core_principle", "kill_switch", "wal", "slippage", "race_condition"
    name: str              # e.g. "revenue_first", "tier1_latency"
    status: ComplianceStatus
    description: str       # what was checked
    detail: str = ""       # additional context / measurement
    recommendation: str = ""  # action if FAIL/PARTIAL


# ---------------------------------------------------------------------------
# ComplianceReport dataclass
# ---------------------------------------------------------------------------


@dataclass
class ComplianceReport:
    """Aggregated compliance audit report."""

    timestamp: datetime
    items: list[ComplianceItem] = field(default_factory=list)

    @property
    def pass_count(self) -> int:
        return sum(1 for i in self.items if i.status == ComplianceStatus.PASS)

    @property
    def fail_count(self) -> int:
        return sum(1 for i in self.items if i.status == ComplianceStatus.FAIL)

    @property
    def partial_count(self) -> int:
        return sum(1 for i in self.items if i.status == ComplianceStatus.PARTIAL)

    @property
    def skipped_count(self) -> int:
        return sum(1 for i in self.items if i.status == ComplianceStatus.SKIPPED)

    @property
    def total_count(self) -> int:
        return len(self.items)

    @property
    def score_pct(self) -> float:
        """PASS as percentage of total (excluding SKIPPED)."""
        non_skipped = [i for i in self.items if i.status != ComplianceStatus.SKIPPED]
        if not non_skipped:
            return 0.0
        return round(self.pass_count / len(non_skipped) * 100, 1)

    def summary(self) -> str:
        """Human-readable summary string."""
        non_skipped = self.total_count - self.skipped_count
        return (
            f"Compliance Score: {self.score_pct}% "
            f"({self.pass_count}/{non_skipped} PASS, "
            f"{self.partial_count} PARTIAL, "
            f"{self.fail_count} FAIL, "
            f"{self.skipped_count} SKIPPED)"
        )

    def failures(self) -> list[ComplianceItem]:
        """Return only FAIL items."""
        return [i for i in self.items if i.status == ComplianceStatus.FAIL]

    def to_markdown(self) -> str:
        """Generate markdown report for docs/COMPLIANCE_REPORT.md."""
        ts = self.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
        non_skipped = self.total_count - self.skipped_count

        lines: list[str] = [
            "# LEVIATHAN Blueprint Compliance Report",
            "",
            f"**Date:** {ts}",
            f"**Score:** {self.score_pct}% "
            f"({self.pass_count}/{non_skipped} PASS, "
            f"{self.partial_count} PARTIAL, "
            f"{self.fail_count} FAIL)",
            "",
            "## Summary",
            "",
            "| Status | Count |",
            "|--------|-------|",
            f"| PASS   | {self.pass_count}    |",
            f"| FAIL   | {self.fail_count}     |",
            f"| PARTIAL| {self.partial_count}  |",
            f"| SKIPPED| {self.skipped_count}  |",
            "",
        ]

        # Group items by category
        categories: dict[str, list[ComplianceItem]] = {}
        for item in self.items:
            categories.setdefault(item.category, []).append(item)

        _CATEGORY_TITLES = {
            "core_principle": "Core Principles",
            "kill_switch": "Kill Switch",
            "wal": "WAL / Dual-Write",
            "slippage": "Slippage Model",
            "race_condition": "Race Condition Mitigations",
            "observability": "Observability",
            "data_integrity": "Data Integrity",
        }

        for cat, items in categories.items():
            title = _CATEGORY_TITLES.get(cat, cat.replace("_", " ").title())
            lines.append(f"## {title}")
            lines.append("")
            lines.append("| # | Check | Status | Detail |")
            lines.append("|---|-------|--------|--------|")
            for idx, item in enumerate(items, 1):
                detail = item.detail.replace("|", "\\|") if item.detail else ""
                lines.append(f"| {idx} | {item.name} | {item.status.value} | {detail} |")
            lines.append("")

        # Failures & Recommendations section
        fail_and_partial = [i for i in self.items if i.status in (ComplianceStatus.FAIL, ComplianceStatus.PARTIAL)]
        if fail_and_partial:
            lines.append("## Failures & Recommendations")
            lines.append("")
            lines.append("| Check | Status | Recommendation |")
            lines.append("|-------|--------|----------------|")
            for item in fail_and_partial:
                rec = item.recommendation.replace("|", "\\|") if item.recommendation else ""
                lines.append(f"| {item.name} | {item.status.value} | {rec} |")
            lines.append("")

        lines.append("---")
        lines.append(f"*Generated by `engine/src/infra/compliance.py` at {ts}*")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _try_import(module_path: str) -> tuple[bool, Any]:
    """Attempt to import a module. Returns (success, module_or_None)."""
    try:
        mod = importlib.import_module(module_path)
        return True, mod
    except Exception:
        return False, None


def _try_import_attr(module_path: str, attr: str) -> tuple[bool, Any]:
    """Attempt to import a specific attribute from a module."""
    ok, mod = _try_import(module_path)
    if not ok or mod is None:
        return False, None
    obj = getattr(mod, attr, None)
    return obj is not None, obj


# ---------------------------------------------------------------------------
# ComplianceChecker
# ---------------------------------------------------------------------------


class ComplianceChecker:
    """Blueprint compliance auditor.

    Checks are organized into categories:
    1. Core Principles (5 checks)
    2. Kill Switch (3 tier checks)
    3. WAL / Dual-Write (2 checks)
    4. Slippage Model (2 checks)
    5. Race Condition Mitigations (key checks)
    6. Observability (metrics + alerting)
    7. Data Integrity (TimescaleDB, recording)
    """

    def __init__(
        self,
        db_pool: Any | None = None,
        kill_switch: Any | None = None,
        circuit_breaker: Any | None = None,
        telegram: Any | None = None,
    ) -> None:
        self._db_pool = db_pool
        self._kill_switch = kill_switch
        self._circuit_breaker = circuit_breaker
        self._telegram = telegram

    async def run_audit(self) -> ComplianceReport:
        """Run all compliance checks and return a ComplianceReport."""
        logger.info("compliance_audit_started")
        items: list[ComplianceItem] = []
        items.extend(self._check_core_principles())
        items.extend(await self._check_kill_switch())
        items.extend(await self._check_wal())
        items.extend(self._check_slippage_model())
        items.extend(self._check_race_conditions())
        items.extend(self._check_observability())
        items.extend(await self._check_data_integrity())
        report = ComplianceReport(timestamp=datetime.now(timezone.utc), items=items)
        logger.info(
            "compliance_audit_complete",
            score_pct=report.score_pct,
            pass_count=report.pass_count,
            fail_count=report.fail_count,
            partial_count=report.partial_count,
        )
        return report

    # ------------------------------------------------------------------
    # 1. Core Principles
    # ------------------------------------------------------------------

    def _check_core_principles(self) -> list[ComplianceItem]:
        """5 core design principle checks."""
        items: list[ComplianceItem] = []

        # 1a. Revenue-First: real data collectors exist
        collectors = [
            "src.collectors.binance_collector",
            "src.collectors.bybit_collector",
            "src.collectors.okx_collector",
            "src.collectors.bitget_collector",
        ]
        available = []
        for c in collectors:
            ok, _ = _try_import(c)
            if ok:
                available.append(c.split(".")[-1])
        if len(available) >= 2:
            items.append(ComplianceItem(
                category="core_principle",
                name="Revenue-First",
                status=ComplianceStatus.PASS,
                description="Real data collectors available for live market data ingestion",
                detail=f"Collectors present: {', '.join(available)}",
            ))
        elif available:
            items.append(ComplianceItem(
                category="core_principle",
                name="Revenue-First",
                status=ComplianceStatus.PARTIAL,
                description="Real data collectors available for live market data ingestion",
                detail=f"Only {len(available)} collector(s) found: {', '.join(available)}",
                recommendation="Implement collectors for remaining exchanges (Binance, Bybit, OKX, Bitget)",
            ))
        else:
            items.append(ComplianceItem(
                category="core_principle",
                name="Revenue-First",
                status=ComplianceStatus.FAIL,
                description="Real data collectors available for live market data ingestion",
                detail="No exchange collectors found",
                recommendation="Implement at least 2 exchange collectors in src/collectors/",
            ))

        # 1b. Incremental Migration: KillSwitchTarget protocol separate from ExchangeAdapter
        ok_ks, ks_mod = _try_import("src.risk.kill_switch")
        if ok_ks and ks_mod is not None:
            has_protocol = hasattr(ks_mod, "KillSwitchTarget")
            # Verify it's defined separately (not in exchange module)
            ok_ex, ex_mod = _try_import("src.infra.exchange")
            ks_in_exchange = ok_ex and ex_mod is not None and hasattr(ex_mod, "KillSwitchTarget")
            if has_protocol and not ks_in_exchange:
                items.append(ComplianceItem(
                    category="core_principle",
                    name="Incremental-Migration",
                    status=ComplianceStatus.PASS,
                    description="KillSwitchTarget protocol defined separately from ExchangeAdapter",
                    detail="KillSwitchTarget in src.risk.kill_switch (dependency inversion maintained)",
                ))
            elif has_protocol:
                items.append(ComplianceItem(
                    category="core_principle",
                    name="Incremental-Migration",
                    status=ComplianceStatus.PARTIAL,
                    description="KillSwitchTarget protocol defined separately from ExchangeAdapter",
                    detail="KillSwitchTarget exists but may overlap with exchange adapter",
                    recommendation="Ensure KillSwitchTarget is ONLY in src.risk.kill_switch",
                ))
            else:
                items.append(ComplianceItem(
                    category="core_principle",
                    name="Incremental-Migration",
                    status=ComplianceStatus.FAIL,
                    description="KillSwitchTarget protocol defined separately from ExchangeAdapter",
                    detail="KillSwitchTarget protocol not found in src.risk.kill_switch",
                    recommendation="Add KillSwitchTarget Protocol to src/risk/kill_switch.py",
                ))
        else:
            items.append(ComplianceItem(
                category="core_principle",
                name="Incremental-Migration",
                status=ComplianceStatus.SKIPPED,
                description="KillSwitchTarget protocol defined separately from ExchangeAdapter",
                detail="src.risk.kill_switch module not importable",
            ))

        # 1c. Real-Data-Only: DataMode enum with REAL_PUBLIC option
        ok_main, main_mod = _try_import("src.main")
        if ok_main and main_mod is not None:
            data_mode_cls = getattr(main_mod, "DataMode", None)
            if data_mode_cls is not None:
                has_real = hasattr(data_mode_cls, "REAL_PUBLIC")
                if has_real:
                    items.append(ComplianceItem(
                        category="core_principle",
                        name="Real-Data-Only",
                        status=ComplianceStatus.PASS,
                        description="DataMode enum exists with REAL_PUBLIC option",
                        detail=f"DataMode.REAL_PUBLIC = {getattr(data_mode_cls, 'REAL_PUBLIC', None)!r}",
                    ))
                else:
                    items.append(ComplianceItem(
                        category="core_principle",
                        name="Real-Data-Only",
                        status=ComplianceStatus.PARTIAL,
                        description="DataMode enum exists with REAL_PUBLIC option",
                        detail="DataMode class found but REAL_PUBLIC attribute missing",
                        recommendation="Add REAL_PUBLIC = 'real_public' to DataMode in src/main.py",
                    ))
            else:
                items.append(ComplianceItem(
                    category="core_principle",
                    name="Real-Data-Only",
                    status=ComplianceStatus.FAIL,
                    description="DataMode enum exists with REAL_PUBLIC option",
                    detail="DataMode class not found in src.main",
                    recommendation="Add DataMode class with REAL_PUBLIC option to src/main.py",
                ))
        else:
            items.append(ComplianceItem(
                category="core_principle",
                name="Real-Data-Only",
                status=ComplianceStatus.SKIPPED,
                description="DataMode enum exists with REAL_PUBLIC option",
                detail="src.main module not importable",
            ))

        # 1d. Rust-Hot-Path-Only: feature flags exist (USE_RUST_*), default false
        ok_rb, rb_mod = _try_import("src.core.rust_bridge")
        if ok_rb and rb_mod is not None:
            get_flags = getattr(rb_mod, "get_feature_flags", None)
            _parse = getattr(rb_mod, "_parse_feature_flag", None)
            expected_flags = ["USE_RUST_ORDERBOOK", "USE_RUST_SIGNAL", "USE_RUST_KILLSWITCH"]
            if get_flags is not None:
                try:
                    flags = get_flags()
                    present = [f for f in expected_flags if f in flags]
                    if len(present) == len(expected_flags):
                        items.append(ComplianceItem(
                            category="core_principle",
                            name="Rust-Hot-Path-Only",
                            status=ComplianceStatus.PASS,
                            description="Rust feature flags exist (USE_RUST_*) with safe defaults",
                            detail=f"Flags: {flags}",
                        ))
                    else:
                        missing = set(expected_flags) - set(present)
                        items.append(ComplianceItem(
                            category="core_principle",
                            name="Rust-Hot-Path-Only",
                            status=ComplianceStatus.PARTIAL,
                            description="Rust feature flags exist (USE_RUST_*) with safe defaults",
                            detail=f"Missing flags: {missing}",
                            recommendation="Add missing USE_RUST_* flags to src/core/rust_bridge.py",
                        ))
                except Exception as exc:
                    items.append(ComplianceItem(
                        category="core_principle",
                        name="Rust-Hot-Path-Only",
                        status=ComplianceStatus.PARTIAL,
                        description="Rust feature flags exist (USE_RUST_*) with safe defaults",
                        detail=f"get_feature_flags() raised: {exc}",
                        recommendation="Fix get_feature_flags() in src/core/rust_bridge.py",
                    ))
            elif _parse is not None:
                # Module has _parse_feature_flag at minimum
                items.append(ComplianceItem(
                    category="core_principle",
                    name="Rust-Hot-Path-Only",
                    status=ComplianceStatus.PARTIAL,
                    description="Rust feature flags exist (USE_RUST_*) with safe defaults",
                    detail="rust_bridge module present but get_feature_flags() missing",
                    recommendation="Add get_feature_flags() to src/core/rust_bridge.py",
                ))
            else:
                items.append(ComplianceItem(
                    category="core_principle",
                    name="Rust-Hot-Path-Only",
                    status=ComplianceStatus.FAIL,
                    description="Rust feature flags exist (USE_RUST_*) with safe defaults",
                    detail="rust_bridge module has no feature flag infrastructure",
                    recommendation="Implement USE_RUST_* feature flags in src/core/rust_bridge.py",
                ))
        else:
            items.append(ComplianceItem(
                category="core_principle",
                name="Rust-Hot-Path-Only",
                status=ComplianceStatus.FAIL,
                description="Rust feature flags exist (USE_RUST_*) with safe defaults",
                detail="src.core.rust_bridge not importable",
                recommendation="Implement src/core/rust_bridge.py with USE_RUST_* feature flags",
            ))

        # 1e. Observable-then-Live: Prometheus metrics + Telegram alerter exist
        ok_metrics, _ = _try_import("src.infra.metrics")
        ok_telegram, _ = _try_import("src.infra.telegram")
        if ok_metrics and ok_telegram:
            items.append(ComplianceItem(
                category="core_principle",
                name="Observable-then-Live",
                status=ComplianceStatus.PASS,
                description="Prometheus metrics and Telegram alerter modules exist",
                detail="src.infra.metrics and src.infra.telegram both importable",
            ))
        elif ok_metrics or ok_telegram:
            missing = []
            if not ok_metrics:
                missing.append("src.infra.metrics")
            if not ok_telegram:
                missing.append("src.infra.telegram")
            items.append(ComplianceItem(
                category="core_principle",
                name="Observable-then-Live",
                status=ComplianceStatus.PARTIAL,
                description="Prometheus metrics and Telegram alerter modules exist",
                detail=f"Missing: {', '.join(missing)}",
                recommendation=f"Implement missing observability module(s): {', '.join(missing)}",
            ))
        else:
            items.append(ComplianceItem(
                category="core_principle",
                name="Observable-then-Live",
                status=ComplianceStatus.FAIL,
                description="Prometheus metrics and Telegram alerter modules exist",
                detail="Both src.infra.metrics and src.infra.telegram are missing",
                recommendation="Implement Prometheus metrics and Telegram alerter",
            ))

        return items

    # ------------------------------------------------------------------
    # 2. Kill Switch
    # ------------------------------------------------------------------

    async def _check_kill_switch(self) -> list[ComplianceItem]:
        """3-tier kill switch checks."""
        items: list[ComplianceItem] = []

        # 2a. Tier 1 Latency: halt_local() should complete in < 1ms
        ok_ks, ks_mod = _try_import("src.risk.kill_switch")
        if ok_ks and ks_mod is not None:
            halt_fn = getattr(ks_mod, "halt_local", None)
            clear_fn = getattr(ks_mod, "clear_halt", None)
            if halt_fn is not None and clear_fn is not None:
                try:
                    # Measure latency (avoid leaving halt flag set)
                    t_start = time.perf_counter()
                    halt_fn()
                    elapsed_ms = (time.perf_counter() - t_start) * 1000
                    clear_fn()  # reset immediately
                    if elapsed_ms < 1.0:
                        items.append(ComplianceItem(
                            category="kill_switch",
                            name="Tier1-Latency",
                            status=ComplianceStatus.PASS,
                            description="halt_local() completes in < 1ms (Tier 1 target)",
                            detail=f"Measured latency: {elapsed_ms:.4f} ms",
                        ))
                    else:
                        items.append(ComplianceItem(
                            category="kill_switch",
                            name="Tier1-Latency",
                            status=ComplianceStatus.FAIL,
                            description="halt_local() completes in < 1ms (Tier 1 target)",
                            detail=f"Measured latency: {elapsed_ms:.4f} ms (exceeds 1ms target)",
                            recommendation="Investigate halt_local() implementation — should be threading.Event.set()",
                        ))
                except Exception as exc:
                    items.append(ComplianceItem(
                        category="kill_switch",
                        name="Tier1-Latency",
                        status=ComplianceStatus.FAIL,
                        description="halt_local() completes in < 1ms (Tier 1 target)",
                        detail=f"halt_local() raised: {exc}",
                        recommendation="Fix halt_local() in src/risk/kill_switch.py",
                    ))
            elif self._kill_switch is not None:
                # Fall back to runtime kill switch measurement
                try:
                    t_start = time.perf_counter()
                    self._kill_switch.halt_local() if hasattr(self._kill_switch, "halt_local") else None
                    elapsed_ms = (time.perf_counter() - t_start) * 1000
                    status = ComplianceStatus.PASS if elapsed_ms < 1.0 else ComplianceStatus.FAIL
                    items.append(ComplianceItem(
                        category="kill_switch",
                        name="Tier1-Latency",
                        status=status,
                        description="halt_local() completes in < 1ms (Tier 1 target)",
                        detail=f"Measured via runtime instance: {elapsed_ms:.4f} ms",
                        recommendation="" if status == ComplianceStatus.PASS else "halt_local() exceeds 1ms SLA",
                    ))
                except Exception as exc:
                    items.append(ComplianceItem(
                        category="kill_switch",
                        name="Tier1-Latency",
                        status=ComplianceStatus.SKIPPED,
                        description="halt_local() completes in < 1ms (Tier 1 target)",
                        detail=f"Runtime measurement failed: {exc}",
                    ))
            else:
                items.append(ComplianceItem(
                    category="kill_switch",
                    name="Tier1-Latency",
                    status=ComplianceStatus.SKIPPED,
                    description="halt_local() completes in < 1ms (Tier 1 target)",
                    detail="halt_local() function not found and no kill_switch instance provided",
                ))
        else:
            items.append(ComplianceItem(
                category="kill_switch",
                name="Tier1-Latency",
                status=ComplianceStatus.FAIL,
                description="halt_local() completes in < 1ms (Tier 1 target)",
                detail="src.risk.kill_switch not importable",
                recommendation="Implement src/risk/kill_switch.py with halt_local()",
            ))

        # 2b. Tier 2 Availability: cancel_all_orders method on KillSwitchTarget
        if ok_ks and ks_mod is not None:
            ks_target = getattr(ks_mod, "KillSwitchTarget", None)
            if ks_target is not None:
                # Check protocol defines cancel_all_orders
                has_cancel = "cancel_all_orders" in dir(ks_target) or hasattr(ks_target, "cancel_all_orders")
                if has_cancel:
                    items.append(ComplianceItem(
                        category="kill_switch",
                        name="Tier2-Availability",
                        status=ComplianceStatus.PASS,
                        description="KillSwitchTarget protocol defines cancel_all_orders method",
                        detail="cancel_all_orders present in KillSwitchTarget protocol",
                    ))
                else:
                    items.append(ComplianceItem(
                        category="kill_switch",
                        name="Tier2-Availability",
                        status=ComplianceStatus.FAIL,
                        description="KillSwitchTarget protocol defines cancel_all_orders method",
                        detail="cancel_all_orders NOT in KillSwitchTarget",
                        recommendation="Add cancel_all_orders(timeout_ms: int) -> list[str] to KillSwitchTarget",
                    ))
            else:
                items.append(ComplianceItem(
                    category="kill_switch",
                    name="Tier2-Availability",
                    status=ComplianceStatus.FAIL,
                    description="KillSwitchTarget protocol defines cancel_all_orders method",
                    detail="KillSwitchTarget class not found in src.risk.kill_switch",
                    recommendation="Add KillSwitchTarget Protocol to src/risk/kill_switch.py",
                ))
        else:
            items.append(ComplianceItem(
                category="kill_switch",
                name="Tier2-Availability",
                status=ComplianceStatus.SKIPPED,
                description="KillSwitchTarget protocol defines cancel_all_orders method",
                detail="src.risk.kill_switch not importable",
            ))

        # 2c. Tier 3 Availability: close_all_positions method on KillSwitchTarget
        if ok_ks and ks_mod is not None:
            ks_target = getattr(ks_mod, "KillSwitchTarget", None)
            if ks_target is not None:
                has_close = "close_all_positions" in dir(ks_target) or hasattr(ks_target, "close_all_positions")
                if has_close:
                    items.append(ComplianceItem(
                        category="kill_switch",
                        name="Tier3-Availability",
                        status=ComplianceStatus.PASS,
                        description="KillSwitchTarget protocol defines close_all_positions method",
                        detail="close_all_positions present in KillSwitchTarget protocol",
                    ))
                else:
                    items.append(ComplianceItem(
                        category="kill_switch",
                        name="Tier3-Availability",
                        status=ComplianceStatus.FAIL,
                        description="KillSwitchTarget protocol defines close_all_positions method",
                        detail="close_all_positions NOT in KillSwitchTarget",
                        recommendation="Add close_all_positions(timeout_ms: int) -> list[str] to KillSwitchTarget",
                    ))
            else:
                items.append(ComplianceItem(
                    category="kill_switch",
                    name="Tier3-Availability",
                    status=ComplianceStatus.FAIL,
                    description="KillSwitchTarget protocol defines close_all_positions method",
                    detail="KillSwitchTarget class not found",
                    recommendation="Add KillSwitchTarget Protocol to src/risk/kill_switch.py",
                ))
        else:
            items.append(ComplianceItem(
                category="kill_switch",
                name="Tier3-Availability",
                status=ComplianceStatus.SKIPPED,
                description="KillSwitchTarget protocol defines close_all_positions method",
                detail="src.risk.kill_switch not importable",
            ))

        # 2d. OR Logic: Rust kill switch checks both Python AND Rust flags
        if ok_ks and ks_mod is not None:
            is_halted_fn = getattr(ks_mod, "is_halted", None)
            if is_halted_fn is not None:
                import inspect
                try:
                    src = inspect.getsource(is_halted_fn)
                    has_or_logic = "USE_RUST_KILLSWITCH" in src or "rust_ks" in src
                    if has_or_logic:
                        items.append(ComplianceItem(
                            category="kill_switch",
                            name="OR-Logic-Rust",
                            status=ComplianceStatus.PASS,
                            description="is_halted() checks both Python AND Rust flags (OR logic)",
                            detail="is_halted() includes Rust AtomicBool check when USE_RUST_KILLSWITCH enabled",
                        ))
                    else:
                        items.append(ComplianceItem(
                            category="kill_switch",
                            name="OR-Logic-Rust",
                            status=ComplianceStatus.PARTIAL,
                            description="is_halted() checks both Python AND Rust flags (OR logic)",
                            detail="is_halted() found but OR logic for Rust flag not detected",
                            recommendation="Ensure is_halted() checks Rust AtomicBool when USE_RUST_KILLSWITCH=true",
                        ))
                except Exception:
                    items.append(ComplianceItem(
                        category="kill_switch",
                        name="OR-Logic-Rust",
                        status=ComplianceStatus.PARTIAL,
                        description="is_halted() checks both Python AND Rust flags (OR logic)",
                        detail="Could not inspect is_halted() source",
                        recommendation="Manually verify OR logic in is_halted()",
                    ))
            else:
                items.append(ComplianceItem(
                    category="kill_switch",
                    name="OR-Logic-Rust",
                    status=ComplianceStatus.FAIL,
                    description="is_halted() checks both Python AND Rust flags (OR logic)",
                    detail="is_halted() not found in src.risk.kill_switch",
                    recommendation="Add is_halted() to src/risk/kill_switch.py",
                ))
        else:
            items.append(ComplianceItem(
                category="kill_switch",
                name="OR-Logic-Rust",
                status=ComplianceStatus.SKIPPED,
                description="is_halted() checks both Python AND Rust flags (OR logic)",
                detail="src.risk.kill_switch not importable",
            ))

        return items

    # ------------------------------------------------------------------
    # 3. WAL / Dual-Write
    # ------------------------------------------------------------------

    async def _check_wal(self) -> list[ComplianceItem]:
        """2 WAL checks."""
        items: list[ComplianceItem] = []

        # 3a. WAL Module importable
        ok, mod = _try_import("src.infra.db.dual_write")
        if ok and mod is not None:
            # Check it has TradeRejectedError (key indicator of full implementation)
            has_error = hasattr(mod, "TradeRejectedError")
            if has_error:
                items.append(ComplianceItem(
                    category="wal",
                    name="WAL-Module",
                    status=ComplianceStatus.PASS,
                    description="src.infra.db.dual_write module is importable with WAL implementation",
                    detail="dual_write module present with TradeRejectedError",
                ))
            else:
                items.append(ComplianceItem(
                    category="wal",
                    name="WAL-Module",
                    status=ComplianceStatus.PARTIAL,
                    description="src.infra.db.dual_write module is importable with WAL implementation",
                    detail="dual_write module importable but TradeRejectedError not found",
                    recommendation="Ensure TradeRejectedError is defined in src/infra/db/dual_write.py",
                ))
        else:
            items.append(ComplianceItem(
                category="wal",
                name="WAL-Module",
                status=ComplianceStatus.FAIL,
                description="src.infra.db.dual_write module is importable with WAL implementation",
                detail="src.infra.db.dual_write not importable",
                recommendation="Implement dual-write WAL at src/infra/db/dual_write.py",
            ))

        # 3b. WAL Health: verify DB connectivity and table access
        if self._db_pool is not None:
            try:
                conn = await self._db_pool.acquire()
                try:
                    # Verify DB is reachable and execution_log table exists (core WAL target)
                    result = await conn.fetchval(
                        "SELECT COUNT(*) FROM execution_log"
                    )
                    healthy = result is not None
                finally:
                    await self._db_pool.release(conn)

                status = ComplianceStatus.PASS if healthy else ComplianceStatus.PARTIAL
                items.append(ComplianceItem(
                    category="wal",
                    name="WAL-Health",
                    status=status,
                    description="Database WAL target tables accessible and queryable",
                    detail=f"execution_log table accessible, {result} rows" if healthy else "Query returned None",
                    recommendation="" if healthy else "Verify dual-write is active during trading",
                ))
            except Exception as exc:
                items.append(ComplianceItem(
                    category="wal",
                    name="WAL-Health",
                    status=ComplianceStatus.PARTIAL,
                    description="Database WAL target tables accessible and queryable",
                    detail=f"WAL health check failed: {exc}",
                    recommendation="Connect TimescaleDB and verify dual-write is operational",
                ))
        else:
            items.append(ComplianceItem(
                category="wal",
                name="WAL-Health",
                status=ComplianceStatus.SKIPPED,
                description="Recent WAL writes exist in position_wal table",
                detail="No db_pool provided — cannot verify WAL writes",
                recommendation="Provide db_pool to ComplianceChecker for WAL health verification",
            ))

        return items

    # ------------------------------------------------------------------
    # 4. Slippage Model
    # ------------------------------------------------------------------

    def _check_slippage_model(self) -> list[ComplianceItem]:
        """2 slippage model checks."""
        items: list[ComplianceItem] = []

        # 4a. CEXOrderbookSlippage importable
        ok, cls = _try_import_attr("src.friction.slippage_model", "CEXOrderbookSlippage")
        if ok and cls is not None:
            items.append(ComplianceItem(
                category="slippage",
                name="CEX-Orderbook-Slippage",
                status=ComplianceStatus.PASS,
                description="src.friction.slippage_model.CEXOrderbookSlippage is importable",
                detail="CEXOrderbookSlippage class available",
            ))
        else:
            items.append(ComplianceItem(
                category="slippage",
                name="CEX-Orderbook-Slippage",
                status=ComplianceStatus.FAIL,
                description="src.friction.slippage_model.CEXOrderbookSlippage is importable",
                detail="CEXOrderbookSlippage not found in src.friction.slippage_model",
                recommendation="Implement CEXOrderbookSlippage in src/friction/slippage_model.py",
            ))

        # 4b. Power-law gamma parameter exists on CEXOrderbookSlippage
        if ok and cls is not None:
            gamma = getattr(cls, "GAMMA", None)
            gamma_calibrated = getattr(cls, "GAMMA_CALIBRATED", False)
            gamma_from_env = os.getenv("SLIPPAGE_GAMMA") is not None
            if gamma is not None and (gamma_calibrated or gamma_from_env):
                items.append(ComplianceItem(
                    category="slippage",
                    name="Power-Law-Gamma",
                    status=ComplianceStatus.PASS,
                    description="CEXOrderbookSlippage.GAMMA is configurable and set",
                    detail=f"GAMMA = {gamma} (configurable via SLIPPAGE_GAMMA env; calibrated={gamma_calibrated})",
                ))
            elif gamma is not None:
                items.append(ComplianceItem(
                    category="slippage",
                    name="Power-Law-Gamma",
                    status=ComplianceStatus.PARTIAL,
                    description="CEXOrderbookSlippage.GAMMA parameter exists (need runtime calibration)",
                    detail=f"GAMMA = {gamma} (using default; set SLIPPAGE_GAMMA env to configure)",
                    recommendation="Set SLIPPAGE_GAMMA env var or calibrate against live execution data",
                ))
            else:
                items.append(ComplianceItem(
                    category="slippage",
                    name="Power-Law-Gamma",
                    status=ComplianceStatus.FAIL,
                    description="CEXOrderbookSlippage.GAMMA parameter exists (need runtime calibration)",
                    detail="GAMMA attribute not found on CEXOrderbookSlippage",
                    recommendation="Add GAMMA class attribute (default 0.5) to CEXOrderbookSlippage",
                ))
        else:
            items.append(ComplianceItem(
                category="slippage",
                name="Power-Law-Gamma",
                status=ComplianceStatus.SKIPPED,
                description="CEXOrderbookSlippage.GAMMA parameter exists (need runtime calibration)",
                detail="CEXOrderbookSlippage not importable — skipping gamma check",
            ))

        return items

    # ------------------------------------------------------------------
    # 5. Race Condition Mitigations
    # ------------------------------------------------------------------

    def _check_race_conditions(self) -> list[ComplianceItem]:
        """Key race condition mitigation checks."""
        items: list[ComplianceItem] = []

        # 5a. asyncio.Lock in KillSwitch
        ok_ks, ks_mod = _try_import("src.risk.kill_switch")
        if ok_ks and ks_mod is not None:
            ks_cls = getattr(ks_mod, "KillSwitch", None)
            if ks_cls is not None:
                # Check via the runtime instance if provided, else inspect __init__
                if self._kill_switch is not None:
                    has_lock = isinstance(getattr(self._kill_switch, "_lock", None), asyncio.Lock)
                else:
                    import inspect
                    try:
                        src = inspect.getsource(ks_cls.__init__)
                        has_lock = "asyncio.Lock()" in src and "_lock" in src
                    except Exception:
                        has_lock = False

                if has_lock:
                    items.append(ComplianceItem(
                        category="race_condition",
                        name="KillSwitch-Lock",
                        status=ComplianceStatus.PASS,
                        description="KillSwitch has asyncio.Lock (_lock) for thread-safe state transitions",
                        detail="asyncio.Lock present in KillSwitch",
                    ))
                else:
                    items.append(ComplianceItem(
                        category="race_condition",
                        name="KillSwitch-Lock",
                        status=ComplianceStatus.FAIL,
                        description="KillSwitch has asyncio.Lock (_lock) for thread-safe state transitions",
                        detail="asyncio.Lock not found in KillSwitch.__init__",
                        recommendation="Add self._lock = asyncio.Lock() to KillSwitch.__init__",
                    ))
            else:
                items.append(ComplianceItem(
                    category="race_condition",
                    name="KillSwitch-Lock",
                    status=ComplianceStatus.FAIL,
                    description="KillSwitch has asyncio.Lock (_lock) for thread-safe state transitions",
                    detail="KillSwitch class not found in src.risk.kill_switch",
                    recommendation="Implement KillSwitch class in src/risk/kill_switch.py",
                ))
        else:
            items.append(ComplianceItem(
                category="race_condition",
                name="KillSwitch-Lock",
                status=ComplianceStatus.SKIPPED,
                description="KillSwitch has asyncio.Lock (_lock) for thread-safe state transitions",
                detail="src.risk.kill_switch not importable",
            ))

        # 5b. asyncio.Lock in CircuitBreaker
        ok_cb, cb_mod = _try_import("src.risk.circuit_breaker")
        if ok_cb and cb_mod is not None:
            cb_cls = getattr(cb_mod, "CircuitBreaker", None)
            if cb_cls is not None:
                if self._circuit_breaker is not None:
                    has_lock = isinstance(getattr(self._circuit_breaker, "_lock", None), asyncio.Lock)
                else:
                    import inspect
                    try:
                        src = inspect.getsource(cb_cls.__init__)
                        has_lock = "asyncio.Lock()" in src and "_lock" in src
                    except Exception:
                        has_lock = False

                if has_lock:
                    items.append(ComplianceItem(
                        category="race_condition",
                        name="CircuitBreaker-Lock",
                        status=ComplianceStatus.PASS,
                        description="CircuitBreaker has asyncio.Lock (_lock) for thread-safe state transitions",
                        detail="asyncio.Lock present in CircuitBreaker",
                    ))
                else:
                    items.append(ComplianceItem(
                        category="race_condition",
                        name="CircuitBreaker-Lock",
                        status=ComplianceStatus.FAIL,
                        description="CircuitBreaker has asyncio.Lock (_lock) for thread-safe state transitions",
                        detail="asyncio.Lock not found in CircuitBreaker.__init__",
                        recommendation="Add self._lock = asyncio.Lock() to CircuitBreaker.__init__",
                    ))
            else:
                items.append(ComplianceItem(
                    category="race_condition",
                    name="CircuitBreaker-Lock",
                    status=ComplianceStatus.FAIL,
                    description="CircuitBreaker has asyncio.Lock (_lock) for thread-safe state transitions",
                    detail="CircuitBreaker class not found",
                    recommendation="Implement CircuitBreaker in src/risk/circuit_breaker.py",
                ))
        else:
            items.append(ComplianceItem(
                category="race_condition",
                name="CircuitBreaker-Lock",
                status=ComplianceStatus.SKIPPED,
                description="CircuitBreaker has asyncio.Lock (_lock) for thread-safe state transitions",
                detail="src.risk.circuit_breaker not importable",
            ))

        # 5c. threading.Event for halt flag (_HALT_FLAG)
        if ok_ks and ks_mod is not None:
            halt_flag = getattr(ks_mod, "_HALT_FLAG", None)
            if halt_flag is not None and isinstance(halt_flag, threading.Event):
                items.append(ComplianceItem(
                    category="race_condition",
                    name="HALT-FLAG-threading-Event",
                    status=ComplianceStatus.PASS,
                    description="_HALT_FLAG is threading.Event (Redis-independent, < 0.01ms)",
                    detail="_HALT_FLAG = threading.Event() confirmed",
                ))
            elif halt_flag is not None:
                items.append(ComplianceItem(
                    category="race_condition",
                    name="HALT-FLAG-threading-Event",
                    status=ComplianceStatus.FAIL,
                    description="_HALT_FLAG is threading.Event (Redis-independent, < 0.01ms)",
                    detail=f"_HALT_FLAG is {type(halt_flag).__name__}, expected threading.Event",
                    recommendation="Change _HALT_FLAG to threading.Event() in src/risk/kill_switch.py",
                ))
            else:
                items.append(ComplianceItem(
                    category="race_condition",
                    name="HALT-FLAG-threading-Event",
                    status=ComplianceStatus.FAIL,
                    description="_HALT_FLAG is threading.Event (Redis-independent, < 0.01ms)",
                    detail="_HALT_FLAG not found in src.risk.kill_switch",
                    recommendation="Add _HALT_FLAG = threading.Event() to src/risk/kill_switch.py",
                ))
        else:
            items.append(ComplianceItem(
                category="race_condition",
                name="HALT-FLAG-threading-Event",
                status=ComplianceStatus.SKIPPED,
                description="_HALT_FLAG is threading.Event (Redis-independent, < 0.01ms)",
                detail="src.risk.kill_switch not importable",
            ))

        # 5d. Atomic executor leg timeout (LEG_TIMEOUT_MS env var)
        leg_timeout = os.getenv("LEG_TIMEOUT_MS")
        if leg_timeout is not None:
            items.append(ComplianceItem(
                category="race_condition",
                name="Leg-Timeout-Config",
                status=ComplianceStatus.PASS,
                description="LEG_TIMEOUT_MS env var configured for atomic executor leg timeout",
                detail=f"LEG_TIMEOUT_MS = {leg_timeout}",
            ))
        else:
            # Check if it's referenced in source
            ok_ex, ex_mod = _try_import("src.execution.executor")
            if ok_ex and ex_mod is not None:
                import inspect
                try:
                    src_text = inspect.getsource(ex_mod)
                    has_timeout = "LEG_TIMEOUT_MS" in src_text
                    if has_timeout:
                        items.append(ComplianceItem(
                            category="race_condition",
                            name="Leg-Timeout-Config",
                            status=ComplianceStatus.PARTIAL,
                            description="LEG_TIMEOUT_MS env var configured for atomic executor leg timeout",
                            detail="LEG_TIMEOUT_MS referenced in executor but env var not set",
                            recommendation="Set LEG_TIMEOUT_MS env var in production deployment config",
                        ))
                    else:
                        items.append(ComplianceItem(
                            category="race_condition",
                            name="Leg-Timeout-Config",
                            status=ComplianceStatus.FAIL,
                            description="LEG_TIMEOUT_MS env var configured for atomic executor leg timeout",
                            detail="LEG_TIMEOUT_MS not set and not referenced in executor",
                            recommendation="Add LEG_TIMEOUT_MS support to executor and set in deployment config",
                        ))
                except Exception:
                    items.append(ComplianceItem(
                        category="race_condition",
                        name="Leg-Timeout-Config",
                        status=ComplianceStatus.PARTIAL,
                        description="LEG_TIMEOUT_MS env var configured for atomic executor leg timeout",
                        detail="Executor module present but LEG_TIMEOUT_MS env var not set",
                        recommendation="Set LEG_TIMEOUT_MS env var in production deployment config",
                    ))
            else:
                items.append(ComplianceItem(
                    category="race_condition",
                    name="Leg-Timeout-Config",
                    status=ComplianceStatus.FAIL,
                    description="LEG_TIMEOUT_MS env var configured for atomic executor leg timeout",
                    detail="LEG_TIMEOUT_MS env var not set and executor module not importable",
                    recommendation="Set LEG_TIMEOUT_MS env var and implement leg timeout in executor",
                ))

        # 5e. Reconciliation loop: reconcile_interval config exists
        ok_eng, eng_mod = _try_import("src.core.engine")
        if ok_eng and eng_mod is not None:
            engine_config = getattr(eng_mod, "EngineConfig", None)
            if engine_config is not None:
                try:
                    cfg = engine_config()
                    has_reconcile = hasattr(cfg, "reconcile_interval")
                    if has_reconcile:
                        items.append(ComplianceItem(
                            category="race_condition",
                            name="Reconciliation-Loop",
                            status=ComplianceStatus.PASS,
                            description="EngineConfig.reconcile_interval exists for reconciliation loop",
                            detail=f"reconcile_interval = {cfg.reconcile_interval}s",
                        ))
                    else:
                        items.append(ComplianceItem(
                            category="race_condition",
                            name="Reconciliation-Loop",
                            status=ComplianceStatus.FAIL,
                            description="EngineConfig.reconcile_interval exists for reconciliation loop",
                            detail="reconcile_interval not in EngineConfig",
                            recommendation="Add reconcile_interval field to EngineConfig dataclass",
                        ))
                except Exception as exc:
                    items.append(ComplianceItem(
                        category="race_condition",
                        name="Reconciliation-Loop",
                        status=ComplianceStatus.PARTIAL,
                        description="EngineConfig.reconcile_interval exists for reconciliation loop",
                        detail=f"EngineConfig instantiation failed: {exc}",
                        recommendation="Fix EngineConfig dataclass in src/core/engine.py",
                    ))
            else:
                items.append(ComplianceItem(
                    category="race_condition",
                    name="Reconciliation-Loop",
                    status=ComplianceStatus.FAIL,
                    description="EngineConfig.reconcile_interval exists for reconciliation loop",
                    detail="EngineConfig not found in src.core.engine",
                    recommendation="Add EngineConfig dataclass to src/core/engine.py",
                ))
        else:
            items.append(ComplianceItem(
                category="race_condition",
                name="Reconciliation-Loop",
                status=ComplianceStatus.SKIPPED,
                description="EngineConfig.reconcile_interval exists for reconciliation loop",
                detail="src.core.engine not importable",
            ))

        return items

    # ------------------------------------------------------------------
    # 6. Observability
    # ------------------------------------------------------------------

    def _check_observability(self) -> list[ComplianceItem]:
        """Metrics + alerting observability checks."""
        items: list[ComplianceItem] = []

        # 6a. Prometheus Metrics: key metrics importable
        ok_m, metrics_mod = _try_import("src.infra.metrics")
        if ok_m and metrics_mod is not None:
            required_metrics = ["TRADES_TOTAL", "PNL_TOTAL", "KILL_SWITCH_LATENCY", "ERRORS_TOTAL"]
            present = [m for m in required_metrics if hasattr(metrics_mod, m)]
            missing = [m for m in required_metrics if m not in present]
            if not missing:
                items.append(ComplianceItem(
                    category="observability",
                    name="Prometheus-Metrics",
                    status=ComplianceStatus.PASS,
                    description="Key Prometheus metrics are defined and importable",
                    detail=f"All required metrics present: {', '.join(present)}",
                ))
            elif present:
                items.append(ComplianceItem(
                    category="observability",
                    name="Prometheus-Metrics",
                    status=ComplianceStatus.PARTIAL,
                    description="Key Prometheus metrics are defined and importable",
                    detail=f"Present: {present}. Missing: {missing}",
                    recommendation=f"Add missing metrics to src/infra/metrics.py: {missing}",
                ))
            else:
                items.append(ComplianceItem(
                    category="observability",
                    name="Prometheus-Metrics",
                    status=ComplianceStatus.FAIL,
                    description="Key Prometheus metrics are defined and importable",
                    detail="No required metrics found in src.infra.metrics",
                    recommendation="Define TRADES_TOTAL, PNL_TOTAL, KILL_SWITCH_LATENCY, ERRORS_TOTAL in metrics.py",
                ))
        else:
            items.append(ComplianceItem(
                category="observability",
                name="Prometheus-Metrics",
                status=ComplianceStatus.FAIL,
                description="Key Prometheus metrics are defined and importable",
                detail="src.infra.metrics not importable",
                recommendation="Implement src/infra/metrics.py with Prometheus metrics",
            ))

        # 6b. Telegram Alerter: importable and class exists
        ok_t, tg_mod = _try_import("src.infra.telegram")
        if ok_t and tg_mod is not None:
            alerter_cls = getattr(tg_mod, "TelegramAlerter", None)
            if alerter_cls is not None:
                # Check if runtime instance or env var indicates configuration
                if self._telegram is not None:
                    is_configured = getattr(self._telegram, "_enabled", False) or bool(
                        os.getenv("TELEGRAM_BOT_TOKEN")
                    )
                else:
                    is_configured = bool(os.getenv("TELEGRAM_BOT_TOKEN"))
                status = ComplianceStatus.PASS if is_configured else ComplianceStatus.PARTIAL
                items.append(ComplianceItem(
                    category="observability",
                    name="Telegram-Alerter",
                    status=status,
                    description="TelegramAlerter importable and configured via env vars",
                    detail=(
                        "TelegramAlerter found and TELEGRAM_BOT_TOKEN set"
                        if is_configured
                        else "TelegramAlerter found but TELEGRAM_BOT_TOKEN not set"
                    ),
                    recommendation=(
                        ""
                        if is_configured
                        else "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars for production alerting"
                    ),
                ))
            else:
                items.append(ComplianceItem(
                    category="observability",
                    name="Telegram-Alerter",
                    status=ComplianceStatus.FAIL,
                    description="TelegramAlerter importable and configured via env vars",
                    detail="TelegramAlerter class not found in src.infra.telegram",
                    recommendation="Implement TelegramAlerter class in src/infra/telegram.py",
                ))
        else:
            items.append(ComplianceItem(
                category="observability",
                name="Telegram-Alerter",
                status=ComplianceStatus.FAIL,
                description="TelegramAlerter importable and configured via env vars",
                detail="src.infra.telegram not importable",
                recommendation="Implement src/infra/telegram.py with TelegramAlerter",
            ))

        # 6c. Structured Logging: structlog is used
        try:
            import structlog as _structlog  # noqa: F401
            ok_structlog = True
        except ImportError:
            ok_structlog = False

        if ok_structlog:
            ok_logger, logger_mod = _try_import("src.infra.logger")
            if ok_logger:
                items.append(ComplianceItem(
                    category="observability",
                    name="Structured-Logging",
                    status=ComplianceStatus.PASS,
                    description="structlog used for structured logging throughout engine",
                    detail="structlog available and src.infra.logger module present",
                ))
            else:
                items.append(ComplianceItem(
                    category="observability",
                    name="Structured-Logging",
                    status=ComplianceStatus.PARTIAL,
                    description="structlog used for structured logging throughout engine",
                    detail="structlog installed but src.infra.logger module not found",
                    recommendation="Create src/infra/logger.py with structlog configuration",
                ))
        else:
            items.append(ComplianceItem(
                category="observability",
                name="Structured-Logging",
                status=ComplianceStatus.FAIL,
                description="structlog used for structured logging throughout engine",
                detail="structlog package not installed",
                recommendation="Install structlog: pip install structlog",
            ))

        # 6d. Health Check Loop: Engine has _health_check_loop
        if ok_eng := True:
            ok_eng2, eng_mod = _try_import("src.core.engine")
            if ok_eng2 and eng_mod is not None:
                engine_cls = getattr(eng_mod, "LEVIATHANEngine", None)
                if engine_cls is not None:
                    has_health = hasattr(engine_cls, "_health_check_loop")
                    if has_health:
                        items.append(ComplianceItem(
                            category="observability",
                            name="Health-Check-Loop",
                            status=ComplianceStatus.PASS,
                            description="LEVIATHANEngine has _health_check_loop method",
                            detail="_health_check_loop present on LEVIATHANEngine",
                        ))
                    else:
                        # Also check src.main
                        ok_main, main_mod = _try_import("src.main")
                        if ok_main and main_mod is not None:
                            main_cls = None
                            for attr_name in dir(main_mod):
                                obj = getattr(main_mod, attr_name, None)
                                if obj and hasattr(obj, "_health_check_loop"):
                                    main_cls = attr_name
                                    break
                            if main_cls:
                                items.append(ComplianceItem(
                                    category="observability",
                                    name="Health-Check-Loop",
                                    status=ComplianceStatus.PASS,
                                    description="Engine has _health_check_loop method",
                                    detail=f"_health_check_loop found on {main_cls} in src.main",
                                ))
                            else:
                                items.append(ComplianceItem(
                                    category="observability",
                                    name="Health-Check-Loop",
                                    status=ComplianceStatus.FAIL,
                                    description="Engine has _health_check_loop method",
                                    detail="_health_check_loop not found on LEVIATHANEngine or src.main engine class",
                                    recommendation="Add _health_check_loop() to engine class",
                                ))
                        else:
                            items.append(ComplianceItem(
                                category="observability",
                                name="Health-Check-Loop",
                                status=ComplianceStatus.FAIL,
                                description="LEVIATHANEngine has _health_check_loop method",
                                detail="_health_check_loop not found on LEVIATHANEngine",
                                recommendation="Add _health_check_loop() to LEVIATHANEngine",
                            ))
                else:
                    items.append(ComplianceItem(
                        category="observability",
                        name="Health-Check-Loop",
                        status=ComplianceStatus.FAIL,
                        description="LEVIATHANEngine has _health_check_loop method",
                        detail="LEVIATHANEngine class not found in src.core.engine",
                        recommendation="Implement LEVIATHANEngine with _health_check_loop",
                    ))
            else:
                items.append(ComplianceItem(
                    category="observability",
                    name="Health-Check-Loop",
                    status=ComplianceStatus.SKIPPED,
                    description="LEVIATHANEngine has _health_check_loop method",
                    detail="src.core.engine not importable",
                ))

        return items

    # ------------------------------------------------------------------
    # 7. Data Integrity
    # ------------------------------------------------------------------

    async def _check_data_integrity(self) -> list[ComplianceItem]:
        """TimescaleDB schema + MarketRecorder checks."""
        items: list[ComplianceItem] = []

        # 7a. TimescaleDB Schema: tables exist
        required_tables = ["orderbook_snapshots", "execution_log", "ohlcv_1m"]
        if self._db_pool is not None:
            found_tables: list[str] = []
            missing_tables: list[str] = []
            try:
                async with self._db_pool.acquire() as conn:
                    for table in required_tables:
                        exists = await conn.fetchval(
                            "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
                            "WHERE table_name = $1)",
                            table,
                        )
                        if exists:
                            found_tables.append(table)
                        else:
                            missing_tables.append(table)

                if not missing_tables:
                    items.append(ComplianceItem(
                        category="data_integrity",
                        name="TimescaleDB-Schema",
                        status=ComplianceStatus.PASS,
                        description="Required TimescaleDB tables exist",
                        detail=f"Tables confirmed: {', '.join(found_tables)}",
                    ))
                elif found_tables:
                    items.append(ComplianceItem(
                        category="data_integrity",
                        name="TimescaleDB-Schema",
                        status=ComplianceStatus.PARTIAL,
                        description="Required TimescaleDB tables exist",
                        detail=f"Found: {found_tables}. Missing: {missing_tables}",
                        recommendation=f"Run migrations to create missing tables: {missing_tables}",
                    ))
                else:
                    items.append(ComplianceItem(
                        category="data_integrity",
                        name="TimescaleDB-Schema",
                        status=ComplianceStatus.FAIL,
                        description="Required TimescaleDB tables exist",
                        detail=f"No required tables found. Missing: {required_tables}",
                        recommendation="Run database migrations: src/infra/db/migrations/",
                    ))
            except Exception as exc:
                items.append(ComplianceItem(
                    category="data_integrity",
                    name="TimescaleDB-Schema",
                    status=ComplianceStatus.FAIL,
                    description="Required TimescaleDB tables exist",
                    detail=f"Database query failed: {exc}",
                    recommendation="Connect TimescaleDB and run migrations",
                ))
        else:
            # Auto-connect via DATABASE_URL when no db_pool provided
            db_url = os.getenv("DATABASE_URL", "")
            asyncpg_url = db_url.replace("postgresql+asyncpg://", "postgresql://") if db_url else ""
            auto_connected = False
            if asyncpg_url:
                try:
                    import asyncpg
                    conn = await asyncpg.connect(asyncpg_url, timeout=5.0)
                    try:
                        found_tables = []
                        missing_tables = []
                        for table in required_tables:
                            exists = await conn.fetchval(
                                "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
                                "WHERE table_name = $1)",
                                table,
                            )
                            if exists:
                                found_tables.append(table)
                            else:
                                missing_tables.append(table)
                        if not missing_tables:
                            items.append(ComplianceItem(
                                category="data_integrity",
                                name="TimescaleDB-Schema",
                                status=ComplianceStatus.PASS,
                                description="Required TimescaleDB tables exist",
                                detail=f"Tables confirmed (auto-connect): {', '.join(found_tables)}",
                            ))
                        else:
                            items.append(ComplianceItem(
                                category="data_integrity",
                                name="TimescaleDB-Schema",
                                status=ComplianceStatus.PARTIAL,
                                description="Required TimescaleDB tables exist",
                                detail=f"Found: {found_tables}. Missing: {missing_tables}",
                                recommendation=f"Run migrations for: {missing_tables}",
                            ))
                        auto_connected = True
                    finally:
                        await conn.close()
                except Exception:
                    pass  # fall through to schema module check

            if not auto_connected:
                ok_schema, _ = _try_import("src.infra.db.schema")
                if ok_schema:
                    items.append(ComplianceItem(
                        category="data_integrity",
                        name="TimescaleDB-Schema",
                        status=ComplianceStatus.PARTIAL,
                        description="Required TimescaleDB tables exist",
                        detail="No db_pool and DATABASE_URL unreachable — schema module present",
                        recommendation="Provide db_pool or set DATABASE_URL to verify tables",
                    ))
                else:
                    items.append(ComplianceItem(
                        category="data_integrity",
                        name="TimescaleDB-Schema",
                        status=ComplianceStatus.SKIPPED,
                        description="Required TimescaleDB tables exist",
                        detail="No db_pool provided and schema module not importable",
                        recommendation="Provide db_pool and implement src/infra/db/schema.py",
                ))

        # 7b. MarketRecorder: module importable with batch insert
        ok_mr, mr_mod = _try_import("src.infra.db.market_recorder")
        if ok_mr and mr_mod is not None:
            # Look for batch insert capability
            recorder_cls = None
            for attr_name in dir(mr_mod):
                obj = getattr(mr_mod, attr_name, None)
                if obj and isinstance(obj, type):
                    # Check for batch insert method signature
                    if any("batch" in m.lower() or "insert" in m.lower() for m in dir(obj)):
                        recorder_cls = attr_name
                        break
            if recorder_cls:
                items.append(ComplianceItem(
                    category="data_integrity",
                    name="MarketRecorder",
                    status=ComplianceStatus.PASS,
                    description="MarketRecorder module importable with batch insert capability",
                    detail=f"Recorder class with batch insert found: {recorder_cls}",
                ))
            else:
                items.append(ComplianceItem(
                    category="data_integrity",
                    name="MarketRecorder",
                    status=ComplianceStatus.PARTIAL,
                    description="MarketRecorder module importable with batch insert capability",
                    detail="market_recorder module importable but batch insert class not detected",
                    recommendation="Ensure MarketRecorder class has batch_insert or insert_orderbook methods",
                ))
        else:
            items.append(ComplianceItem(
                category="data_integrity",
                name="MarketRecorder",
                status=ComplianceStatus.FAIL,
                description="MarketRecorder module importable with batch insert capability",
                detail="src.infra.db.market_recorder not importable",
                recommendation="Implement MarketRecorder in src/infra/db/market_recorder.py",
            ))

        return items


# ---------------------------------------------------------------------------
# __main__ entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio

    async def _main() -> None:
        checker = ComplianceChecker()
        report = await checker.run_audit()
        print(report.summary())
        # Write markdown report
        from pathlib import Path
        report_path = (
            Path(__file__).parent.parent.parent.parent / "docs" / "COMPLIANCE_REPORT.md"
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report.to_markdown())
        print(f"Report written to {report_path}")

    asyncio.run(_main())
