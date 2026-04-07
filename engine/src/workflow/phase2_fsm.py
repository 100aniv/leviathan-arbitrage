"""PHOENIX Phase 2 FSM — Step 자동 진행 종료 조건 정의 (§8.5).

각 Step의 종료 조건을 머신 판독 가능 형태로 정의해서 자동운영 진행 또는 정지를
머신이 판단할 수 있게 한다.

Usage::
    from src.workflow.phase2_fsm import check_step_exit_condition, STEP_CONDITIONS
    result = await check_step_exit_condition("2-1", started_at, db_pool, redis_client)
    if result["passed"]:
        # advance to next step
        ...
    else:
        # send telegram alert + halt
        ...

설계 원칙:
- 시간 + 안정성 + PnL 3축 게이트 (`min_duration_hours` AND 모든 안정성 조건)
- 조건 미달 시 자동 정지 (사장님 깨움 X, 텔레그램 알림만)
- 모든 종료 조건은 `evidence` dict에 수치로 기록 (감사 가능)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StepCondition:
    """Phase 2 단일 Step의 종료 조건 묶음.

    Attributes:
        step_name: Step 식별자 (예: "2-1", "2-1.5")
        min_duration_hours: 최소 관찰 시간 (시간)
        max_pnl_loss: 허용 최대 손실 (음수 USD). PnL 이 이하면 실패.
        max_crash_count: 허용 최대 크래시 횟수 (rollback_failed 카운트)
        max_ks_fires: 허용 최대 KillSwitch 발동 횟수
        max_cb_opens: 허용 최대 CircuitBreaker OPEN 횟수
        max_dd_pct: 허용 최대 드로다운 (%)
    """

    step_name: str
    min_duration_hours: float
    max_pnl_loss: float
    max_crash_count: int
    max_ks_fires: int
    max_cb_opens: int
    max_dd_pct: float


# §8.4 재설계 표 그대로 코드화
STEP_CONDITIONS: Mapping[str, StepCondition] = {
    "2-1": StepCondition(
        step_name="2-1",
        min_duration_hours=48.0,
        max_pnl_loss=-1.0,
        max_crash_count=0,
        max_ks_fires=0,
        max_cb_opens=5,
        max_dd_pct=5.0,
    ),
    "2-1.5": StepCondition(
        step_name="2-1.5",
        min_duration_hours=24.0,
        max_pnl_loss=-2.0,
        max_crash_count=0,
        max_ks_fires=0,
        max_cb_opens=5,
        max_dd_pct=5.0,
    ),
    "2-2": StepCondition(
        step_name="2-2",
        min_duration_hours=24.0,
        max_pnl_loss=-3.0,
        max_crash_count=0,
        max_ks_fires=0,
        max_cb_opens=10,
        max_dd_pct=7.0,
    ),
    "2-3": StepCondition(
        step_name="2-3",
        min_duration_hours=24.0,
        max_pnl_loss=-4.0,
        max_crash_count=0,
        max_ks_fires=0,
        max_cb_opens=10,
        max_dd_pct=7.0,
    ),
    "2-4": StepCondition(
        step_name="2-4",
        min_duration_hours=24.0,
        max_pnl_loss=-5.0,
        max_crash_count=0,
        max_ks_fires=0,
        max_cb_opens=10,
        max_dd_pct=7.0,
    ),
    "2-5": StepCondition(
        step_name="2-5",
        min_duration_hours=24.0,
        max_pnl_loss=-6.0,
        max_crash_count=0,
        max_ks_fires=0,
        max_cb_opens=10,
        max_dd_pct=7.0,
    ),
    "2-6": StepCondition(
        step_name="2-6",
        min_duration_hours=24.0,
        max_pnl_loss=-8.0,
        max_crash_count=0,
        max_ks_fires=0,
        max_cb_opens=10,
        max_dd_pct=7.0,
    ),
    "2-8": StepCondition(
        step_name="2-8",
        min_duration_hours=72.0,
        max_pnl_loss=-10.0,
        max_crash_count=0,
        max_ks_fires=0,
        max_cb_opens=20,
        max_dd_pct=10.0,
    ),
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def check_step_exit_condition(
    step: str,
    started_at: datetime,
    db_pool: Any,
    redis_client: Any,
) -> dict[str, Any]:
    """Step 종료 조건 검사. 모든 조건 충족 시 ``passed=True``.

    Args:
        step: Step 식별자 (STEP_CONDITIONS 키)
        started_at: Step 시작 시각 (UTC, timezone-aware 권장)
        db_pool: asyncpg connection pool (None이면 DB 메트릭 스킵)
        redis_client: redis.asyncio client (None이면 Redis 메트릭 스킵)

    Returns:
        ``{"passed": bool, "reason": str, "evidence": dict}`` 형태.
        evidence는 모든 측정값을 포함해서 감사/디버그 가능.
    """
    cond = STEP_CONDITIONS.get(step)
    if cond is None:
        return {
            "passed": False,
            "reason": f"Unknown step: {step}",
            "evidence": {"step": step},
        }

    # timezone-aware 비교 보정
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)

    elapsed = _utcnow() - started_at
    elapsed_hours = elapsed.total_seconds() / 3600.0

    evidence: dict[str, Any] = {
        "step": step,
        "started_at": started_at.isoformat(),
        "elapsed_hours": round(elapsed_hours, 3),
        "condition": {
            "min_duration_hours": cond.min_duration_hours,
            "max_pnl_loss": cond.max_pnl_loss,
            "max_crash_count": cond.max_crash_count,
            "max_ks_fires": cond.max_ks_fires,
            "max_cb_opens": cond.max_cb_opens,
            "max_dd_pct": cond.max_dd_pct,
        },
    }

    # 1. 최소 관찰 시간 게이트
    if elapsed_hours < cond.min_duration_hours:
        return {
            "passed": False,
            "reason": (
                f"min_duration_not_reached elapsed={elapsed_hours:.2f}h "
                f"required={cond.min_duration_hours:.2f}h"
            ),
            "evidence": evidence,
        }

    # 2. DB 측정값 (PnL + crash count)
    if db_pool is not None:
        try:
            async with db_pool.acquire() as conn:
                total_pnl = await conn.fetchval(
                    "SELECT COALESCE(SUM(pnl), 0) FROM execution_log "
                    "WHERE mode = 'live' AND created_at >= $1",
                    started_at,
                )
                pnl_value = float(total_pnl or 0)
                evidence["total_pnl"] = pnl_value
                if pnl_value < cond.max_pnl_loss:
                    return {
                        "passed": False,
                        "reason": (
                            f"pnl_below_threshold pnl={pnl_value:.2f} "
                            f"threshold={cond.max_pnl_loss:.2f}"
                        ),
                        "evidence": evidence,
                    }

                crash_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM execution_log "
                    "WHERE mode = 'live' AND status = 'rollback_failed' "
                    "AND created_at >= $1",
                    started_at,
                ) or 0
                evidence["crash_count"] = int(crash_count)
                if int(crash_count) > cond.max_crash_count:
                    return {
                        "passed": False,
                        "reason": (
                            f"crash_count_exceeded count={crash_count} "
                            f"max={cond.max_crash_count}"
                        ),
                        "evidence": evidence,
                    }
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("phase2_fsm.db_metric_error step=%s error=%s", step, exc)
            evidence["db_metric_error"] = str(exc)

    # 3. Redis 측정값 (KillSwitch + CB OPEN counters)
    if redis_client is not None:
        try:
            ks_fires_raw = await redis_client.get("leviathan:killswitch:fire_count")
            ks_fires = int(ks_fires_raw or 0)
            evidence["ks_fires"] = ks_fires
            if ks_fires > cond.max_ks_fires:
                return {
                    "passed": False,
                    "reason": (
                        f"killswitch_fired count={ks_fires} max={cond.max_ks_fires}"
                    ),
                    "evidence": evidence,
                }

            cb_opens_raw = await redis_client.get("leviathan:cb:open_count")
            cb_opens = int(cb_opens_raw or 0)
            evidence["cb_opens"] = cb_opens
            if cb_opens > cond.max_cb_opens:
                return {
                    "passed": False,
                    "reason": (
                        f"cb_opens_exceeded count={cb_opens} max={cond.max_cb_opens}"
                    ),
                    "evidence": evidence,
                }
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("phase2_fsm.redis_metric_error step=%s error=%s", step, exc)
            evidence["redis_metric_error"] = str(exc)

    evidence["passed"] = True
    return {
        "passed": True,
        "reason": "all_conditions_met",
        "evidence": evidence,
    }
