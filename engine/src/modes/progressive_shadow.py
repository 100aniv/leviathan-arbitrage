"""LEVIATHAN Progressive Shadow Orchestrator — 6-Stage Automatic Extension.

Wraps ShadowMode with a staged gate evaluation system:
  Stage 1 (1H):  crash=0, signals>0, trades>0
  Stage 2 (2H):  WR>50%, total_pnl>0
  Stage 3 (6H):  per-strategy metrics separation verified
  Stage 4 (12H): RSS increase <100MB/hr, trades>50
  Stage 5 (24H): Sharpe>2.0 (self-computed), MDD<5%, daily PnL>0
  Stage 6 (72H): LiveGate.evaluate() 6-check ALL PASS

ShadowMode is started once, runs continuously through all stages.
"""
from __future__ import annotations

import asyncio
import os
import time
import json
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog
from prometheus_client import Gauge

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Prometheus gauges
# ---------------------------------------------------------------------------

_RSS_GAUGE = Gauge(
    "progressive_shadow_rss_bytes",
    "Current RSS memory usage of the progressive shadow process",
)
_STAGE_GAUGE = Gauge(
    "progressive_shadow_stage_current",
    "Current progressive shadow stage (1-6, 0=not started, 7=complete)",
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class StageDefinition:
    """Single stage in the progressive shadow run."""

    name: str              # "1H", "2H", "6H", "12H", "24H", "72H"
    duration_seconds: int  # seconds to wait before evaluating gate
    gate_checks: list[str]  # names of checks performed at this stage


@dataclass
class StageResult:
    """Gate evaluation result for a single stage."""

    stage: StageDefinition
    passed: bool
    started_at: datetime
    ended_at: datetime
    stats_snapshot: dict[str, Any]
    gate_results: dict[str, Any]
    resource_snapshot: dict[str, Any]


# ---------------------------------------------------------------------------
# Default STAGES — overridable via environment variables
# ---------------------------------------------------------------------------

def _safe_int(env_key: str, default: int) -> int:
    """Parse env var as int with fallback on malformed values."""
    raw = os.getenv(env_key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("progressive_shadow.bad_env", key=env_key, value=raw, default=default)
        return default


def _build_stages() -> list[StageDefinition]:
    """Build stage definitions, allowing env var overrides for testing."""
    return [
        StageDefinition(
            name="1H",
            duration_seconds=_safe_int("PROGRESSIVE_STAGE_1H_SECONDS", 3600),
            gate_checks=["crash", "signals_detected", "trades_executed"],
        ),
        StageDefinition(
            name="2H",
            duration_seconds=_safe_int("PROGRESSIVE_STAGE_2H_SECONDS", 7200),
            gate_checks=["win_rate", "total_pnl"],
        ),
        StageDefinition(
            name="6H",
            duration_seconds=_safe_int("PROGRESSIVE_STAGE_6H_SECONDS", 21600),
            gate_checks=["strategy_separation"],
        ),
        StageDefinition(
            name="12H",
            duration_seconds=_safe_int("PROGRESSIVE_STAGE_12H_SECONDS", 43200),
            gate_checks=["rss_increase", "trades_count"],
        ),
        StageDefinition(
            name="24H",
            duration_seconds=_safe_int("PROGRESSIVE_STAGE_24H_SECONDS", 86400),
            gate_checks=["sharpe", "max_drawdown", "daily_pnl"],
        ),
        StageDefinition(
            name="72H",
            duration_seconds=_safe_int("PROGRESSIVE_STAGE_72H_SECONDS", 259200),
            gate_checks=["live_gate"],
        ),
    ]


STAGES: list[StageDefinition] = _build_stages()


# ---------------------------------------------------------------------------
# ProgressiveShadowOrchestrator
# ---------------------------------------------------------------------------


class ProgressiveShadowOrchestrator:
    """6-stage progressive shadow gate orchestrator.

    Wraps an existing ShadowMode instance. Calls start() once, runs 6
    sequential stages with gate evaluation at each boundary, then stop().
    If any stage fails, execution halts immediately.

    Args:
        shadow_mode: Initialized ShadowMode instance (not yet started).
        live_gate:   LiveGate instance for Stage 6 evaluation (optional).
        telegram:    TelegramAlerter for stage notifications (optional).
        db_pool:     DB pool for saving snapshots (optional).
        stages:      Override stage list (for testing; defaults to STAGES).
    """

    RSS_LIMIT_MB_PER_HR: float = float(
        _safe_int("PROGRESSIVE_RSS_LIMIT_MB_PER_HR", 100)
    )

    def __init__(
        self,
        shadow_mode: Any,
        live_gate: Any | None = None,
        telegram: Any | None = None,
        db_pool: Any | None = None,
        stages: list[StageDefinition] | None = None,
    ) -> None:
        self._shadow_mode = shadow_mode
        self._live_gate = live_gate
        self._telegram = telegram
        self._db_pool = db_pool
        self._stages: list[StageDefinition] = stages if stages is not None else STAGES

        self._results: list[StageResult] = []
        self._running = False

        # PnL snapshots for Sharpe calculation: deque capped at 168 (7 days hourly)
        self._pnl_snapshots: deque[tuple[float, float]] = deque(maxlen=168)

        # RSS baseline (bytes) recorded at start
        self._baseline_rss_bytes: int = 0
        self._start_time: float = 0.0

        # Background tasks
        self._snapshot_task: asyncio.Task | None = None
        self._resource_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> list[StageResult]:
        """Run 6 stages sequentially. Returns all StageResults collected."""
        logger.info(
            "progressive_shadow.starting",
            stages=len(self._stages),
            stage_names=[s.name for s in self._stages],
        )

        if self._telegram is not None:
            try:
                await self._telegram.send_alert(
                    "Progressive Shadow started: 6 stages (1H→2H→6H→12H→24H→72H)",
                    level="INFO",
                )
            except Exception as exc:
                logger.warning("progressive_shadow.telegram_error", error=str(exc))

        # Record baseline resource usage
        self._baseline_rss_bytes = self._get_current_rss()
        self._start_time = time.monotonic()

        # Start ShadowMode once
        await self._shadow_mode.start()
        self._running = True

        _STAGE_GAUGE.set(0)

        # Start background monitoring loops
        self._snapshot_task = asyncio.create_task(
            self._snapshot_loop(), name="progressive_pnl_snapshot"
        )
        self._resource_task = asyncio.create_task(
            self._resource_monitor_loop(), name="progressive_resource_monitor"
        )

        try:
            for idx, stage in enumerate(self._stages, start=1):
                _STAGE_GAUGE.set(idx)
                started_at = datetime.now(timezone.utc)

                logger.info(
                    "progressive_shadow.stage_started",
                    stage=stage.name,
                    duration_seconds=stage.duration_seconds,
                    stage_num=idx,
                    total_stages=len(self._stages),
                )

                await asyncio.sleep(stage.duration_seconds)

                result = await self._evaluate_gate(stage, started_at)
                self._results.append(result)
                await self._notify_stage_result(result, idx)
                await self._save_snapshot(result)

                if not result.passed:
                    logger.warning(
                        "progressive_shadow.stage_failed",
                        stage=stage.name,
                        gate_results=result.gate_results,
                    )
                    break  # exit loop, fall through to finally + post-finally

                logger.info(
                    "progressive_shadow.stage_passed",
                    stage=stage.name,
                    stage_num=idx,
                )

        except asyncio.CancelledError:
            logger.warning("progressive_shadow.cancelled")
            raise
        finally:
            self._running = False
            # Cancel background tasks
            for task in (self._snapshot_task, self._resource_task):
                if task and not task.done():
                    task.cancel()
            # Stop shadow once
            try:
                await self._shadow_mode.stop()
            except Exception as exc:
                logger.warning("progressive_shadow.stop_error", error=str(exc))

        # Post-finally: always reachable (both all-pass and fail-fast paths)
        all_passed = all(r.passed for r in self._results) and len(self._results) == len(self._stages)
        _STAGE_GAUGE.set(7 if all_passed else 0)

        if self._telegram is not None:
            try:
                msg = (
                    "Progressive Shadow COMPLETE: 6/6 PASS. LiveGate eligible."
                    if all_passed
                    else f"Progressive Shadow STOPPED at Stage {len(self._results)}/6. See logs."
                )
                level = "INFO" if all_passed else "WARNING"
                await self._telegram.send_alert(msg, level=level)
            except Exception as exc:
                logger.warning("progressive_shadow.telegram_error", error=str(exc))

        logger.info(
            "progressive_shadow.complete",
            stages_passed=sum(1 for r in self._results if r.passed),
            total_stages=len(self._stages),
            all_passed=all_passed,
        )
        return self._results

    # ------------------------------------------------------------------
    # Gate evaluation
    # ------------------------------------------------------------------

    async def _evaluate_gate(
        self, stage: StageDefinition, started_at: datetime
    ) -> StageResult:
        """Evaluate all gate checks for the given stage."""
        ended_at = datetime.now(timezone.utc)
        stats = self._shadow_mode._stats
        gate_results: dict[str, Any] = {}
        passed = True

        # Collect resource snapshot
        current_rss = self._get_current_rss()
        elapsed_hours = max((time.monotonic() - self._start_time) / 3600.0, 0.001)
        rss_increase_mb = (current_rss - self._baseline_rss_bytes) / (1024 * 1024)
        rss_increase_mb_per_hr = rss_increase_mb / elapsed_hours

        resource_snapshot = {
            "current_rss_bytes": current_rss,
            "baseline_rss_bytes": self._baseline_rss_bytes,
            "rss_increase_mb": rss_increase_mb,
            "rss_increase_mb_per_hr": rss_increase_mb_per_hr,
            "elapsed_hours": elapsed_hours,
        }

        # Stats snapshot
        stats_snapshot = {
            "signals_detected": stats.signals_detected,
            "trades_executed": stats.trades_executed,
            "trades_won": stats.trades_won,
            "trades_lost": stats.trades_lost,
            "total_pnl": stats.total_pnl,
            "max_drawdown": stats.max_drawdown,
            "trades_rejected": stats.trades_rejected,
            "by_strategy_keys": list(stats.by_strategy.keys()),
        }

        # --- Stage 1: crash=0, signals>0, trades>0 ---
        if stage.name == "1H":
            running_ok = self._shadow_mode._running
            gate_results["crash"] = {"passed": running_ok, "value": running_ok}
            gate_results["signals_detected"] = {
                "passed": stats.signals_detected > 0,
                "value": stats.signals_detected,
            }
            gate_results["trades_executed"] = {
                "passed": stats.trades_executed > 0,
                "value": stats.trades_executed,
            }
            passed = all(v["passed"] for v in gate_results.values())

        # --- Stage 2: WR>50%, total_pnl>0 ---
        elif stage.name == "2H":
            wr = (
                stats.trades_won / stats.trades_executed
                if stats.trades_executed > 0
                else 0.0
            )
            wr_ok = wr > 0.50
            pnl_ok = stats.total_pnl > 0
            gate_results["win_rate"] = {"passed": wr_ok, "value": wr, "threshold": 0.50}
            gate_results["total_pnl"] = {
                "passed": pnl_ok,
                "value": stats.total_pnl,
                "threshold": 0,
            }
            passed = wr_ok and pnl_ok

        # --- Stage 3: at least 2 strategies actively producing trades ---
        elif stage.name == "6H":
            # Active strategies = those with trades > 0
            active_keys = {
                sid for sid, ss in stats.by_strategy.items() if ss.trades > 0
            }
            min_required = 2
            strategy_ok = len(active_keys) >= min_required
            gate_results["strategy_separation"] = {
                "passed": strategy_ok,
                "active_strategies": list(active_keys),
                "active_count": len(active_keys),
                "min_required": min_required,
            }
            passed = strategy_ok

        # --- Stage 4: RSS increase <100MB/hr, trades>50 ---
        elif stage.name == "12H":
            rss_ok = rss_increase_mb_per_hr < self.RSS_LIMIT_MB_PER_HR
            trades_ok = stats.trades_executed > 50
            gate_results["rss_increase"] = {
                "passed": rss_ok,
                "value_mb_per_hr": rss_increase_mb_per_hr,
                "limit_mb_per_hr": self.RSS_LIMIT_MB_PER_HR,
            }
            gate_results["trades_count"] = {
                "passed": trades_ok,
                "value": stats.trades_executed,
                "threshold": 50,
            }
            passed = rss_ok and trades_ok

        # --- Stage 5: Sharpe>2.0 (self-computed), MDD<5%, daily PnL>0 ---
        elif stage.name == "24H":
            sharpe = self._compute_sharpe()
            sharpe_ok = sharpe > 2.0
            # max_drawdown is absolute USD — convert to fraction of initial balance
            initial_balance = float(_safe_int("SHADOW_INITIAL_BALANCE_USDT", 10000000))
            mdd_fraction = stats.max_drawdown / max(initial_balance, 1.0)
            mdd_ok = mdd_fraction < 0.05
            pnl_ok = stats.total_pnl > 0
            gate_results["sharpe"] = {
                "passed": sharpe_ok,
                "value": sharpe,
                "threshold": 2.0,
            }
            gate_results["max_drawdown"] = {
                "passed": mdd_ok,
                "value_usd": stats.max_drawdown,
                "value_fraction": mdd_fraction,
                "threshold": 0.05,
            }
            gate_results["daily_pnl"] = {
                "passed": pnl_ok,
                "value": stats.total_pnl,
                "threshold": 0,
            }
            passed = sharpe_ok and mdd_ok and pnl_ok

        # --- Stage 6: LiveGate.evaluate() ALL PASS (EVALUATION_DAYS=3) ---
        elif stage.name == "72H":
            if self._live_gate is None:
                logger.warning(
                    "progressive_shadow.no_live_gate",
                    detail="LiveGate not provided; Stage 6 auto-FAIL",
                )
                gate_results["live_gate"] = {
                    "passed": False,
                    "detail": "LiveGate not configured",
                }
                passed = False
            else:
                try:
                    self._live_gate.EVALUATION_DAYS = 3  # 72H = 3 days of data
                    lg_result = await self._live_gate.evaluate()
                    passed = lg_result.eligible
                    gate_results["live_gate"] = {
                        "passed": passed,
                        "eligible": lg_result.eligible,
                        "block_reasons": lg_result.block_reasons,
                        "checks": [
                            {"name": c.name, "passed": c.passed, "value": c.value}
                            for c in lg_result.checks
                        ],
                    }
                except Exception as exc:
                    logger.error(
                        "progressive_shadow.live_gate_error", error=str(exc)
                    )
                    gate_results["live_gate"] = {
                        "passed": False,
                        "error": str(exc),
                    }
                    passed = False

        return StageResult(
            stage=stage,
            passed=passed,
            started_at=started_at,
            ended_at=ended_at,
            stats_snapshot=stats_snapshot,
            gate_results=gate_results,
            resource_snapshot=resource_snapshot,
        )

    # ------------------------------------------------------------------
    # Background loops
    # ------------------------------------------------------------------

    async def _snapshot_loop(self) -> None:
        """Collect hourly PnL snapshots for Sharpe calculation."""
        try:
            while self._running:
                await asyncio.sleep(3600)
                ts = time.monotonic()
                pnl = self._shadow_mode._stats.total_pnl
                self._pnl_snapshots.append((ts, pnl))
                logger.debug(
                    "progressive_shadow.pnl_snapshot",
                    ts=ts,
                    total_pnl=pnl,
                    snapshot_count=len(self._pnl_snapshots),
                )
        except asyncio.CancelledError:
            pass

    async def _resource_monitor_loop(self) -> None:
        """Log RSS/CPU every 5 minutes and update Prometheus gauge."""
        try:
            while self._running:
                await asyncio.sleep(300)
                rss = self._get_current_rss()
                cpu = self._get_cpu_percent()
                _RSS_GAUGE.set(rss)
                logger.debug(
                    "progressive_shadow.resource_snapshot",
                    rss_bytes=rss,
                    rss_mb=rss / (1024 * 1024),
                    cpu_percent=cpu,
                )
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------
    # Sharpe calculation
    # ------------------------------------------------------------------

    def _compute_sharpe(self) -> float:
        """Compute annualized Sharpe from hourly PnL deltas.

        Returns 0.0 if fewer than 2 snapshots available.
        Returns inf if mean>0 and std==0 (perfectly consistent gains).
        """
        if len(self._pnl_snapshots) < 2:
            return 0.0
        deltas = [
            b[1] - a[1]
            for a, b in zip(self._pnl_snapshots, self._pnl_snapshots[1:])
        ]
        mean = sum(deltas) / len(deltas)
        variance = sum((d - mean) ** 2 for d in deltas) / (len(deltas) - 1)
        std = variance ** 0.5
        if std == 0:
            return float("inf") if mean > 0 else 0.0
        # Annualize: 8760 hours/year
        return (mean / std) * (8760 ** 0.5)

    # ------------------------------------------------------------------
    # Notifications & persistence
    # ------------------------------------------------------------------

    async def _notify_stage_result(self, result: StageResult, stage_num: int) -> None:
        """Send Telegram alert for stage PASS/FAIL."""
        if self._telegram is None:
            return
        try:
            stats = result.stats_snapshot
            if result.passed:
                wr = (
                    stats["trades_won"] / stats["trades_executed"]
                    if stats["trades_executed"] > 0
                    else 0.0
                )
                msg = (
                    f"Stage {stage_num}/6 ({result.stage.name}) PASSED: "
                    f"WR={wr:.1%}, PnL={stats['total_pnl']:+.2f}"
                )
                level = "INFO"
            else:
                msg = (
                    f"Stage {stage_num}/6 ({result.stage.name}) FAILED: "
                    f"{result.gate_results}"
                )
                level = "CRITICAL"
            await self._telegram.send_alert(msg, level=level)
        except Exception as exc:
            logger.warning("progressive_shadow.telegram_error", error=str(exc))

    async def _save_snapshot(self, result: StageResult) -> None:
        """Persist stage result as structlog JSON. DB save if pool available."""
        logger.info(
            "progressive_shadow.stage_result",
            stage_name=result.stage.name,
            passed=result.passed,
            started_at=result.started_at.isoformat(),
            ended_at=result.ended_at.isoformat(),
            stats_snapshot=result.stats_snapshot,
            gate_results=result.gate_results,
            resource_snapshot=result.resource_snapshot,
        )

        if self._db_pool is None:
            return

        try:
            pool = self._db_pool.pool if hasattr(self._db_pool, "pool") else self._db_pool
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO shadow_stage_results
                        (stage_name, passed, started_at, ended_at,
                         stats_snapshot, gate_results, resource_snapshot)
                    VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7::jsonb)
                    ON CONFLICT DO NOTHING
                    """,
                    result.stage.name,
                    result.passed,
                    result.started_at,
                    result.ended_at,
                    json.dumps(result.stats_snapshot),
                    json.dumps(result.gate_results),
                    json.dumps(result.resource_snapshot),
                )
        except Exception as exc:
            logger.warning(
                "progressive_shadow.db_save_error",
                stage=result.stage.name,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Resource helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_current_rss() -> int:
        """Return current process RSS in bytes using psutil."""
        try:
            import psutil
            return psutil.Process().memory_info().rss
        except Exception:
            return 0

    @staticmethod
    def _get_cpu_percent() -> float:
        """Return current process CPU percent using psutil."""
        try:
            import psutil
            return psutil.Process().cpu_percent(interval=1.0)
        except Exception:
            return 0.0
