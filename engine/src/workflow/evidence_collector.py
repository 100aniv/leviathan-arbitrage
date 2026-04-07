"""PHOENIX Phase 2 Evidence Collector (§8.5).

각 Step 종료 시 DB/Redis/로그 측정값을 수집해서
``.omc/state/phase2/step-{N}-evidence.json`` 으로 저장한다.

Usage::
    from src.workflow.evidence_collector import collect_step_evidence
    evidence = await collect_step_evidence("2-1", db_pool, redis_client)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# 절대 경로 — 워크트리 루트의 .omc/state/phase2 디렉터리
_DEFAULT_OUT_DIR = Path(".omc/state/phase2")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def collect_step_evidence(
    step: str,
    db_pool: Any,
    redis_client: Any,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    """Step 종료 시 모든 측정값 수집 + JSON 파일 저장.

    Args:
        step: Step 식별자 (예: "2-1", "2-8")
        db_pool: asyncpg connection pool
        redis_client: redis.asyncio client
        out_dir: 저장 디렉터리 (기본: ``.omc/state/phase2``)

    Returns:
        evidence dict (저장된 파일과 동일 내용)
    """
    evidence: dict[str, Any] = {
        "step": step,
        "collected_at": _utcnow_iso(),
        "db_metrics": {},
        "redis_metrics": {},
        "latency_metrics": {},
    }

    # ── DB 메트릭 ─────────────────────────────────────────────────────
    if db_pool is not None:
        try:
            async with db_pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT strategy_id, COUNT(*) AS trades, "
                    "COALESCE(SUM(pnl), 0) AS total_pnl, "
                    "COALESCE(AVG(pnl), 0) AS avg_pnl "
                    "FROM execution_log WHERE mode = 'live' "
                    "GROUP BY strategy_id"
                )
                evidence["db_metrics"]["by_strategy"] = [
                    {
                        "strategy_id": r["strategy_id"],
                        "trades": int(r["trades"]),
                        "total_pnl": float(r["total_pnl"]),
                        "avg_pnl": float(r["avg_pnl"]),
                    }
                    for r in rows
                ]

                total = await conn.fetchrow(
                    "SELECT COUNT(*) AS total_trades, "
                    "COALESCE(SUM(pnl), 0) AS total_pnl "
                    "FROM execution_log WHERE mode = 'live'"
                )
                if total:
                    evidence["db_metrics"]["total"] = {
                        "total_trades": int(total["total_trades"]),
                        "total_pnl": float(total["total_pnl"]),
                    }

                crash_total = await conn.fetchval(
                    "SELECT COUNT(*) FROM execution_log "
                    "WHERE mode = 'live' AND status = 'rollback_failed'"
                ) or 0
                evidence["db_metrics"]["crash_count"] = int(crash_total)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "evidence_collector.db_error step=%s error=%s", step, exc
            )
            evidence["db_metrics"]["error"] = str(exc)

    # ── Redis 메트릭 ──────────────────────────────────────────────────
    if redis_client is not None:
        for key in (
            "leviathan:killswitch:fire_count",
            "leviathan:cb:open_count",
            "leviathan:halt",
            "leviathan:heartbeat",
        ):
            try:
                value = await redis_client.get(key)
                # Bytes -> str if needed
                if isinstance(value, bytes):
                    value = value.decode("utf-8", errors="replace")
                evidence["redis_metrics"][key] = value if value is not None else None
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "evidence_collector.redis_error key=%s error=%s", key, exc
                )
                evidence["redis_metrics"][key] = f"error:{exc}"

    # ── 파일 저장 ─────────────────────────────────────────────────────
    target_dir = out_dir if out_dir is not None else _DEFAULT_OUT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    out_file = target_dir / f"step-{step}-evidence.json"
    out_file.write_text(json.dumps(evidence, indent=2, default=str))
    logger.info(
        "evidence_collector.saved step=%s file=%s",
        step, out_file,
    )

    return evidence
