"""PHOENIX Phase 2 Step 2-1 — 실시간 감시 (Sentinel).

30초마다 폴링:
  - 엔진 프로세스 생존 확인
  - Redis heartbeat 소실 감지
  - CB OPEN 감지
  - CRITICAL/crash 로그 감지
  - halt 키 감지
  - PnL 손실 임계 초과 시 경고

이상 감지 시 즉시 InfraBot 알림.

Usage:
    cd engine
    python -m scripts.phoenix_sentinel --engine-pid 94908
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import httpx
import redis.asyncio as aioredis
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CAPITAL_INITIAL_USD = 41.69
STEP_START_UTC = datetime(2026, 4, 7, 14, 16, 0, tzinfo=timezone.utc)
LOG_FILE = Path(__file__).parent.parent / "logs" / "step2-1_canary_20260407_231621.log"

INFRA_TOKEN = os.getenv("INFRA_TELEGRAM_BOT_TOKEN", "")
INFRA_CHAT_ID = os.getenv("INFRA_TELEGRAM_CHAT_ID", "")
DB_URL = os.getenv("DATABASE_URL", "").replace("+asyncpg", "")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

POLL_INTERVAL_S = 30
LOSS_WARN_PCT = 5.0    # -$2.08 경고
LOSS_DEACT_PCT = 7.0   # -$2.92 전략비활
LOSS_KS_PCT = 10.0     # -$4.17 KillSwitch

# 이전에 전송한 알림 dedupe
_sent_alerts: set[str] = set()


async def send_alert(text: str, key: str | None = None) -> None:
    if key and key in _sent_alerts:
        return
    if key:
        _sent_alerts.add(key)
    logger.warning("ALERT: %s", text[:200])
    if not INFRA_TOKEN or not INFRA_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{INFRA_TOKEN}/sendMessage"
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            await client.post(url, json={"chat_id": INFRA_CHAT_ID, "text": text, "parse_mode": "HTML"})
        except Exception as exc:
            logger.error("Telegram send failed: %s", exc)


def is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


async def check_redis(redis: aioredis.Redis) -> list[str]:
    issues = []
    hb = await redis.get("leviathan:heartbeat")
    if hb is None:
        issues.append("heartbeat_lost")
    halt = await redis.get("leviathan:halt")
    if halt:
        issues.append("halt_detected")
    cb_keys = await redis.keys("circuit_breaker:*")
    for k in cb_keys:
        val = await redis.get(k)
        if val and b"OPEN" in val:
            issues.append(f"cb_open:{k.decode()}")
    return issues


async def check_pnl(pool: asyncpg.Pool) -> tuple[float, list[str]]:
    issues = []
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COALESCE(SUM(net_pnl),0) AS pnl FROM execution_log "
            "WHERE strategy_id='funding_rate_v1' AND mode='live' AND ts >= $1",
            STEP_START_UTC,
        )
    pnl = float(row["pnl"])
    dd_pct = abs(min(pnl, 0)) / CAPITAL_INITIAL_USD * 100
    if dd_pct >= LOSS_KS_PCT:
        issues.append(f"ks_threshold_exceeded:{pnl:.4f}")
    elif dd_pct >= LOSS_DEACT_PCT:
        issues.append(f"deact_threshold:{pnl:.4f}")
    elif dd_pct >= LOSS_WARN_PCT:
        issues.append(f"warn_threshold:{pnl:.4f}")
    return pnl, issues


def check_log_crashes(last_line: int) -> tuple[int, list[str]]:
    if not LOG_FILE.exists():
        return last_line, []
    issues = []
    lines = LOG_FILE.read_text(errors="replace").splitlines()
    new_lines = lines[last_line:]
    for line in new_lines:
        if any(kw in line for kw in ["CRITICAL", "Traceback", "kill_switch.triggered",
                                      "circuit_breaker.*OPEN", "engine.crashed"]):
            issues.append(f"log:{line[:120]}")
    return len(lines), issues


async def run_once(engine_pid: int, pool: asyncpg.Pool, redis: aioredis.Redis,
                   last_log_line: int) -> int:
    all_issues = []

    # 1. 엔진 프로세스 생존
    if not is_pid_alive(engine_pid):
        await send_alert(
            f"🚨 <b>엔진 프로세스 소실!</b>\nPID {engine_pid} 없음\n즉시 확인 필요",
            key="engine_dead"
        )
        return last_log_line

    # 2. Redis 감시
    redis_issues = await check_redis(redis)
    for issue in redis_issues:
        if issue == "heartbeat_lost":
            await send_alert("❤️ <b>Heartbeat 소실!</b>\nRedis leviathan:heartbeat 없음", key="hb_lost")
        elif issue == "halt_detected":
            await send_alert("🛑 <b>Halt 감지!</b>\nRedis leviathan:halt = 1", key="halt")
        elif issue.startswith("cb_open:"):
            cb = issue.split(":", 1)[1]
            await send_alert(f"⚡ <b>CircuitBreaker OPEN</b>\n{cb}", key=f"cb_{cb}")

    # 3. PnL 임계
    pnl, pnl_issues = await check_pnl(pool)
    dd_pct = abs(min(pnl, 0)) / CAPITAL_INITIAL_USD * 100
    for issue in pnl_issues:
        if "ks_threshold" in issue:
            await send_alert(
                f"🚨 <b>KillSwitch 임계 초과!</b>\nPnL=${pnl:+.4f} (DD={dd_pct:.1f}% ≥ 10%)\n즉시 확인!",
                key="ks_pnl"
            )
        elif "deact_threshold" in issue:
            await send_alert(
                f"🔴 <b>전략비활 임계!</b>\nPnL=${pnl:+.4f} (DD={dd_pct:.1f}% ≥ 7%)",
                key="deact_pnl"
            )
        elif "warn_threshold" in issue:
            await send_alert(
                f"⚠️ <b>손실 경고</b>\nPnL=${pnl:+.4f} (DD={dd_pct:.1f}% ≥ 5%)",
                key="warn_pnl"
            )

    # 4. 로그 크래시 감지
    new_last, log_issues = check_log_crashes(last_log_line)
    for issue in log_issues[:3]:  # 최대 3건
        await send_alert(f"💥 <b>크래시/에러 감지</b>\n<code>{issue[4:150]}</code>",
                         key=f"crash_{hash(issue)}")

    elapsed = (datetime.now(timezone.utc) - STEP_START_UTC).total_seconds() / 3600
    logger.info("sentinel OK | PID=%d alive | PnL=$%.4f DD=%.1f%% | elapsed=%.1fH | redis_issues=%d | log_issues=%d",
                engine_pid, pnl, dd_pct, elapsed, len(redis_issues), len(log_issues))

    return new_last


async def main(engine_pid: int, interval: int) -> None:
    logger.info("Sentinel started — engine PID=%d, poll=%ds", engine_pid, interval)
    pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=2)
    redis = aioredis.from_url(REDIS_URL)
    last_log_line = 0
    try:
        while True:
            try:
                last_log_line = await run_once(engine_pid, pool, redis, last_log_line)
            except Exception as exc:
                logger.error("Sentinel poll error: %s", exc)
            await asyncio.sleep(interval)
    finally:
        await pool.close()
        await redis.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine-pid", type=int, required=True)
    parser.add_argument("--interval", type=int, default=POLL_INTERVAL_S)
    args = parser.parse_args()
    asyncio.run(main(args.engine_pid, args.interval))
