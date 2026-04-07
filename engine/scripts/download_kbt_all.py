#!/usr/bin/env python3
"""download_kbt_all.py — K-BT 전체 데이터 일괄 다운로드

backtest_batches.json에서 필요한 모든 (exchange, symbols, period) 조합을 추출하여
순차 다운로드합니다.

Usage:
    cd /Users/100aniv/Development/arbitrage_OMC/engine
    python scripts/download_kbt_all.py [--dry-run] [--exchanges binance,bybit]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("download_kbt_all")

_ENGINE_ROOT = Path(__file__).resolve().parent.parent
_BATCHES_JSON = _ENGINE_ROOT / "config" / "backtest_batches.json"
_SCRIPT = _ENGINE_ROOT / "scripts" / "download_historical.py"

# KRW 거래소: Binance에는 KRW 페어 없음 → 스킵
_NO_KRW = {"binance", "binance_futures", "bybit", "bybit_futures",
           "okx", "okx_futures", "bitget", "bitget_futures", "mexc", "gateio"}
_KRW_ONLY = {"upbit", "bithumb", "coinone"}


def load_download_plan() -> list[dict]:
    """backtest_batches.json에서 다운로드 플랜 추출."""
    data = json.loads(_BATCHES_JSON.read_text())
    batches = data.get("batches", [])

    # (exchange, start, end) → symbols 집합
    plan: dict[tuple, set] = defaultdict(set)

    for b in batches:
        exchanges = b.get("exchange_ids", [])
        symbols = b.get("symbols", ["BTC/USDT", "ETH/USDT"])
        periods = b.get("periods", {})

        for ex in exchanges:
            for strat, p in periods.items():
                key = (ex, p["start"], p["end"])
                # 거래소별 지원 심볼 필터링
                for sym in symbols:
                    is_krw = sym.endswith("/KRW")
                    if is_krw and ex in _NO_KRW:
                        continue  # 글로벌 거래소에 KRW 페어 없음
                    if not is_krw and ex in _KRW_ONLY:
                        continue  # KRW 거래소에 USDT 페어 없음
                    plan[key].add(sym)

    # 정렬된 리스트로 변환
    result = []
    for (ex, start, end), syms in sorted(plan.items()):
        if syms:
            result.append({
                "exchange": ex,
                "symbols": sorted(syms),
                "start": start,
                "end": end,
            })
    return result


def run_download(item: dict, dry_run: bool) -> bool:
    """단일 다운로드 실행. 성공 시 True."""
    cmd = [
        sys.executable,
        str(_SCRIPT),
        "--exchanges", item["exchange"],
        "--symbols", ",".join(item["symbols"]),
        "--start", item["start"],
        "--end", item["end"],
        "--interval", "1h",
    ]
    if dry_run:
        cmd.append("--dry-run")

    logger.info("▶ %s  %s~%s  %s", item["exchange"], item["start"], item["end"], item["symbols"])
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_ENGINE_ROOT))

    # 결과 로그
    for line in result.stdout.splitlines():
        if any(k in line for k in ["inserted", "Done", "Error", "WARNING", "ERROR"]):
            logger.info("  %s", line.split(" — ", 1)[-1] if " — " in line else line)
    if result.returncode != 0:
        logger.error("  FAILED: %s", result.stderr[-200:] if result.stderr else "unknown")
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description="K-BT 전체 데이터 일괄 다운로드")
    parser.add_argument("--dry-run", action="store_true", help="실제 DB 삽입 없이 테스트")
    parser.add_argument("--exchanges", help="특정 거래소만 실행 (콤마 구분)")
    args = parser.parse_args()

    plan = load_download_plan()

    # 거래소 필터
    if args.exchanges:
        filter_ex = set(args.exchanges.split(","))
        plan = [p for p in plan if p["exchange"] in filter_ex]

    logger.info("=== K-BT 데이터 다운로드 플랜: %d 개 조합 ===", len(plan))
    for i, item in enumerate(plan, 1):
        logger.info("  [%d] %s %s~%s %s", i, item["exchange"], item["start"], item["end"], item["symbols"])

    logger.info("")
    total = len(plan)
    success = 0
    failed = []

    for i, item in enumerate(plan, 1):
        logger.info("[%d/%d] 시작", i, total)
        ok = run_download(item, dry_run=args.dry_run)
        if ok:
            success += 1
        else:
            failed.append(item["exchange"])

    logger.info("")
    logger.info("=== 완료: %d/%d 성공 ===", success, total)
    if failed:
        logger.warning("실패 거래소: %s", failed)
    else:
        logger.info("모든 거래소 다운로드 성공!")

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
