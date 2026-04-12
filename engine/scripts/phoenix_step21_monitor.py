"""PHOENIX Phase 2 Step 2-1 — 8H 자동 모니터링 보고 스크립트.

InfraBot으로 8시간마다 보고:
  - 누적 PnL (절대금액 + %)
  - 체결 수 (성공/실패/롤백)
  - 레이턴시 p50/p95/p99
  - risk_check 통계
  - 오픈 포지션

Usage:
    cd engine
    python -m scripts.phoenix_step21_monitor [--interval-hours 8]
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
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

CAPITAL_INITIAL_USD = 120.0
STEP = "2-1"
STRATEGY = "futures_futures_v1"

# InfraBot Telegram
INFRA_TOKEN = os.getenv("INFRA_TELEGRAM_BOT_TOKEN", "")
INFRA_CHAT_ID = os.getenv("INFRA_TELEGRAM_CHAT_ID", "")

# DB
DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://leviathan:leviathan@localhost:5432/leviathan",
).replace("+asyncpg", "")


async def send_infra_message(text: str) -> None:
    """InfraBot으로 텔레그램 메시지 전송."""
    if not INFRA_TOKEN or not INFRA_CHAT_ID:
        logger.warning("InfraBot token/chat_id not set — printing to stdout only")
        print(text)
        return
    url = f"https://api.telegram.org/bot{INFRA_TOKEN}/sendMessage"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json={"chat_id": INFRA_CHAT_ID, "text": text, "parse_mode": "HTML"})
        if resp.status_code != 200:
            logger.error("Telegram send failed: %s", resp.text)


STEP_START_UTC = datetime(2026, 4, 8, 3, 23, 50, tzinfo=timezone.utc)  # 2026-04-08 12:23 KST (v4 재시작 — Bug 25a/b/c 수정 반영)


async def query_db_metrics(conn: asyncpg.Connection) -> dict:
    """TimescaleDB에서 PnL, 체결 수 조회 (컬럼: net_pnl, ts, buy_exchange, sell_exchange, size)."""
    rows = await conn.fetch("""
        SELECT
            COUNT(*) AS total_trades,
            COALESCE(SUM(net_pnl), 0) AS total_pnl,
            COALESCE(MIN(net_pnl), 0) AS min_pnl,
            COALESCE(MAX(net_pnl), 0) AS max_pnl,
            COUNT(*) FILTER (WHERE net_pnl > 0) AS profitable,
            COUNT(*) FILTER (WHERE net_pnl <= 0) AS losing
        FROM execution_log
        WHERE strategy_id = $1 AND mode = 'live' AND ts >= $2
    """, STRATEGY, STEP_START_UTC)
    row = rows[0] if rows else {}

    # Recent trades for context
    recent = await conn.fetch("""
        SELECT buy_exchange, sell_exchange, symbol, size, net_pnl, ts
        FROM execution_log
        WHERE strategy_id = $1 AND mode = 'live' AND ts >= $2
        ORDER BY ts DESC LIMIT 5
    """, STRATEGY, STEP_START_UTC)

    return {
        "total_trades": int(row.get("total_trades") or 0),
        "total_pnl": float(row.get("total_pnl") or 0),
        "min_pnl": float(row.get("min_pnl") or 0),
        "max_pnl": float(row.get("max_pnl") or 0),
        "profitable": int(row.get("profitable") or 0),
        "losing": int(row.get("losing") or 0),
        "recent": [dict(r) for r in recent],
    }


def build_report(metrics: dict, elapsed_hours: float) -> str:
    """8H 보고 메시지 생성."""
    pnl = metrics["total_pnl"]
    pnl_pct = (pnl / CAPITAL_INITIAL_USD) * 100
    dd = abs(min(pnl, 0))
    dd_pct = abs(min(pnl_pct, 0))

    warn_usd = CAPITAL_INITIAL_USD * 0.05
    halt_usd = CAPITAL_INITIAL_USD * 0.07
    ks_usd = CAPITAL_INITIAL_USD * 0.10
    status_icon = "✅" if pnl >= 0 else ("⚠️" if dd < warn_usd else ("🔴" if dd < ks_usd else "🚨"))
    now_kst = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

    total = metrics["total_trades"]
    win = metrics["profitable"]
    lose = metrics["losing"]
    wr = f"{win/total*100:.0f}%" if total > 0 else "N/A"

    report = f"""🤖 <b>PHOENIX Step 2-1 — {elapsed_hours:.0f}H 체크포인트</b>
{now_kst} UTC | 전략: {STRATEGY}

{status_icon} <b>누적 PnL: ${pnl:+.4f} ({pnl_pct:+.2f}%)</b>
   자본: ${CAPITAL_INITIAL_USD} | 현재 DD: ${dd:.4f} ({dd_pct:.2f}%)

📊 <b>체결 현황 (Step 2-1 기준)</b>
   총 {total}건 | 수익 {win}건 / 손실 {lose}건 | 승률 {wr}
   최고: ${metrics['max_pnl']:+.4f} | 최저: ${metrics['min_pnl']:+.4f}

🛡 <b>리스크 임계</b>
   경고(5%): -${warn_usd:.2f} | 비활(7%): -${halt_usd:.2f} | KS(10%): -${ks_usd:.2f}

⏳ 종료까지: {max(0, 48 - elapsed_hours):.0f}H 남음"""

    return report


async def run_report(pool: asyncpg.Pool, started_at: datetime) -> None:
    """단일 보고 실행."""
    elapsed = (datetime.now(timezone.utc) - started_at).total_seconds() / 3600
    async with pool.acquire() as conn:
        metrics = await query_db_metrics(conn)
    report = build_report(metrics, elapsed)
    logger.info("Sending 8H report (%.1fH elapsed)", elapsed)
    await send_infra_message(report)


async def main(interval_hours: float) -> None:
    started_at = datetime.now(timezone.utc)
    logger.info("PHOENIX Step 2-1 Monitor started. Interval=%dH", interval_hours)

    pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=3)
    try:
        while True:
            await asyncio.sleep(interval_hours * 3600)
            try:
                await run_report(pool, started_at)
            except Exception as exc:
                logger.error("Report failed: %s", exc)
                await send_infra_message(f"⚠️ PHOENIX Step 2-1 모니터 보고 실패: {exc}")
    finally:
        await pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval-hours", type=float, default=8.0)
    args = parser.parse_args()
    asyncio.run(main(args.interval_hours))
