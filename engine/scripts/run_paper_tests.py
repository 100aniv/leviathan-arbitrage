#!/usr/bin/env python3
"""Paper 테스트 누적 실행 스크립트 (US-372/332/382).

각 케이스를 순서대로 실행, 결과를 .omc/state/paper-results.json에 기록.
누적 실행 시간 >= 86400s (24H) → US-332 자동 충족.

사전 조건:
    - engine API가 실행 중이어야 함: cd engine && python -m src.main
    - DB/Redis 실행 중: docker compose up -d timescaledb redis

Usage:
    cd /Users/100aniv/Development/arbitrage_OMC/engine
    python scripts/run_paper_tests.py --cases P-01,P-02 --duration 7200
    python scripts/run_paper_tests.py --all-basic          # P-01~P-16 순차 실행
    python scripts/run_paper_tests.py --all-extended       # P-24~P-31 실행
    python scripts/run_paper_tests.py --status             # 누적 실행 현황 출력
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import pathlib
import sys
import time
from datetime import datetime, timezone

# Ensure engine src/ is importable regardless of cwd
_ENGINE_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ENGINE_ROOT))

# Load root .env
_ROOT_ENV = _ENGINE_ROOT.parent / ".env"
if _ROOT_ENV.exists():
    from dotenv import load_dotenv
    load_dotenv(str(_ROOT_ENV), override=False)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_paper_tests")

# Output paths
_STATE_DIR = _ENGINE_ROOT.parent / ".omc" / "state"
_RESULTS_PATH = _STATE_DIR / "paper-results.json"
_LOGS_DIR = _ENGINE_ROOT / "logs"

# Paper test case definitions
PAPER_CASES: dict[str, dict] = {
    # Tier1: API 보유 거래소 (Binance / Binance Futures)
    "P-01": {"exchanges": ["binance"], "strategies": ["funding_rate_v1"], "duration": 7200,
             "tier": 1, "description": "Binance funding_rate 2H"},
    "P-02": {"exchanges": ["binance"], "strategies": ["triangular_v1"], "duration": 14400,
             "tier": 1, "description": "Binance triangular 4H"},
    "P-03": {"exchanges": ["binance"], "strategies": ["statistical_arb_v1"], "duration": 14400,
             "tier": 1, "description": "Binance stat_arb 4H"},
    "P-04": {"exchanges": ["binance", "binance_futures"], "strategies": ["spot_futures_v1"], "duration": 14400,
             "tier": 1, "description": "Binance spot/futures 4H"},
    # Tier2: Bitget (API 보유)
    "P-05": {"exchanges": ["bitget"], "strategies": ["funding_rate_v1"], "duration": 14400,
             "tier": 2, "description": "Bitget funding_rate 4H"},
    "P-06": {"exchanges": ["bitget"], "strategies": ["triangular_v1"], "duration": 14400,
             "tier": 2, "description": "Bitget triangular 4H"},
    "P-07": {"exchanges": ["bitget"], "strategies": ["statistical_arb_v1"], "duration": 14400,
             "tier": 2, "description": "Bitget stat_arb 4H"},
    # Tier3: KRW 거래소 (API 보유)
    "P-08": {"exchanges": ["coinone"], "strategies": ["triangular_v1"], "duration": 14400,
             "tier": 3, "description": "Coinone triangular 4H (KRW-only — signal 미보장)"},
    "P-09": {"exchanges": ["coinone"], "strategies": ["statistical_arb_v1"], "duration": 14400,
             "tier": 3, "description": "Coinone stat_arb 4H (KRW-only)"},
    "P-10": {"exchanges": ["upbit"], "strategies": ["triangular_v1"], "duration": 14400,
             "tier": 3, "description": "Upbit triangular 4H"},
    "P-11": {"exchanges": ["bithumb"], "strategies": ["triangular_v1"], "duration": 14400,
             "tier": 3, "description": "Bithumb triangular 4H (fake spread guard 확인)"},
    # Multi-exchange
    "P-12": {"exchanges": ["binance", "bitget"], "strategies": ["cross_exchange_v1"], "duration": 14400,
             "tier": 1, "description": "Binance+Bitget cross_exchange 4H"},
    "P-13": {"exchanges": ["binance", "bitget"], "strategies": ["funding_rate_v1"], "duration": 14400,
             "tier": 1, "description": "Binance+Bitget funding_rate 4H"},
    "P-14": {"exchanges": ["binance", "coinone"], "strategies": ["cross_exchange_v1"], "duration": 14400,
             "tier": 1, "description": "Binance+Coinone cross_exchange 4H"},
    "P-15": {"exchanges": ["binance", "upbit"], "strategies": ["cross_exchange_v1"], "duration": 14400,
             "tier": 1, "description": "Binance+Upbit cross_exchange 4H"},
    "P-16": {"exchanges": ["binance_futures", "bitget"], "strategies": ["futures_futures_v1"], "duration": 14400,
             "tier": 1, "description": "BinanceFutures+Bitget futures_futures 4H"},
    # Tier4: API 미발급 (WS 익명 접속 시도)
    "P-17": {"exchanges": ["mexc"], "strategies": ["triangular_v1"], "duration": 14400,
             "tier": 4, "description": "MEXC triangular 4H (API 미발급)"},
    "P-18": {"exchanges": ["mexc"], "strategies": ["statistical_arb_v1"], "duration": 14400,
             "tier": 4, "description": "MEXC stat_arb 4H"},
    "P-19": {"exchanges": ["gateio"], "strategies": ["triangular_v1"], "duration": 14400,
             "tier": 4, "description": "Gate.io triangular 4H"},
    "P-20": {"exchanges": ["gateio"], "strategies": ["statistical_arb_v1"], "duration": 14400,
             "tier": 4, "description": "Gate.io stat_arb 4H"},
    "P-21": {"exchanges": ["bingx"], "strategies": ["triangular_v1"], "duration": 14400,
             "tier": 4, "description": "BingX triangular 4H"},
    "P-22": {"exchanges": ["lbank"], "strategies": ["triangular_v1"], "duration": 14400,
             "tier": 4, "description": "LBank triangular 4H"},
    "P-23": {"exchanges": ["orangex"], "strategies": ["statistical_arb_v1"], "duration": 14400,
             "tier": 4, "description": "OrangeX stat_arb 4H"},
    # Extended: P-24~P-31
    "P-24": {"exchanges": ["bybit"], "strategies": ["triangular_v1"], "duration": 14400,
             "tier": 2, "description": "Bybit triangular 4H"},
    "P-25": {"exchanges": ["okx"], "strategies": ["triangular_v1"], "duration": 14400,
             "tier": 2, "description": "OKX triangular 4H"},
    "P-26": {"exchanges": ["binance", "bybit"], "strategies": ["cross_exchange_v1"], "duration": 14400,
             "tier": 1, "description": "Binance+Bybit cross_exchange 4H"},
    "P-27": {"exchanges": ["binance", "okx"], "strategies": ["cross_exchange_v1"], "duration": 14400,
             "tier": 1, "description": "Binance+OKX cross_exchange 4H"},
    "P-28": {"exchanges": ["bybit_futures", "okx_futures"], "strategies": ["futures_futures_v1"], "duration": 14400,
             "tier": 2, "description": "Bybit+OKX futures_futures 4H"},
    "P-29": {"exchanges": ["mexc"], "strategies": ["triangular_v1"], "duration": 14400,
             "tier": 4, "description": "MEXC triangular 4H (extended)"},
    "P-30": {"exchanges": ["gateio"], "strategies": ["triangular_v1"], "duration": 14400,
             "tier": 4, "description": "Gate.io triangular 4H (extended)"},
    "P-31": {"exchanges": ["bingx"], "strategies": ["triangular_v1"], "duration": 14400,
             "tier": 4, "description": "BingX triangular 4H (extended)"},
}

_BASIC_CASES = [f"P-{i:02d}" for i in range(1, 17)]    # P-01~P-16
_EXTENDED_CASES = [f"P-{i:02d}" for i in range(24, 32)]  # P-24~P-31
_ALL_CASES = list(PAPER_CASES.keys())

# US-332 threshold: 24H cumulative paper runtime
_US332_THRESHOLD_S = 86400


def _load_results() -> dict:
    """Load existing paper results state file."""
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    if _RESULTS_PATH.exists():
        try:
            return json.loads(_RESULTS_PATH.read_text())
        except Exception:
            pass
    return {
        "cases": {},
        "cumulative_runtime_s": 0.0,
        "us332_satisfied": False,
        "last_updated": None,
    }


def _save_results(state: dict) -> None:
    """Persist paper results state file."""
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    _RESULTS_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def _print_status(state: dict) -> None:
    """Print cumulative paper test status."""
    cum_s = state.get("cumulative_runtime_s", 0.0)
    cum_h = cum_s / 3600
    us332 = state.get("us332_satisfied", False)
    cases = state.get("cases", {})

    print("\n" + "=" * 80)
    print("Paper Test Cumulative Status")
    print("=" * 80)
    print(f"Cumulative runtime: {cum_h:.2f}H ({cum_s:.0f}s) / {_US332_THRESHOLD_S / 3600:.0f}H threshold")
    print(f"US-332 satisfied: {'YES ✅' if us332 else f'NO — need {(_US332_THRESHOLD_S - cum_s) / 3600:.1f}H more'}")
    print(f"Cases run: {len(cases)}")
    print("-" * 80)
    for case_id, result in sorted(cases.items()):
        status = "PASS" if result.get("crash") == 0 else "FAIL"
        trades = result.get("trades", 0)
        signals = result.get("signals", 0)
        duration = result.get("duration_s", 0)
        print(
            f"  {case_id:<8} {PAPER_CASES.get(case_id, {}).get('description', ''):<45} "
            f"dur={duration / 3600:.1f}H  trades={trades}  signals={signals}  {status}"
        )
    print("=" * 80)


async def _check_api_running(base_url: str = "http://localhost:8080") -> bool:
    """Check if the engine API is running."""
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}/health", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                return resp.status == 200
    except Exception:
        return False


async def _run_paper_case(
    case_id: str,
    case_config: dict,
    duration_override: int | None = None,
) -> dict:
    """Run a single paper test case by monitoring engine logs.

    Since engine/src/main.py runs a continuous Paper mode, this function:
    1. Verifies engine is running
    2. Reads current log state
    3. Waits for duration
    4. Reads final log state and computes delta metrics

    Returns a result dict with crash, trades, signals, duration_s.
    """
    import re

    duration_s = duration_override or case_config["duration"]
    exchanges = case_config["exchanges"]
    strategies = case_config["strategies"]
    log_path = _LOGS_DIR / f"paper-{case_id}.log"

    logger.info(
        "Starting paper case %s — exchanges=%s strategies=%s duration=%ds",
        case_id, exchanges, strategies, duration_s,
    )

    # Check API
    api_running = await _check_api_running()
    if not api_running:
        logger.warning(
            "Engine API not responding at localhost:8080. "
            "Start engine: cd engine && python -m src.main"
        )
        return {
            "case_id": case_id,
            "exchanges": exchanges,
            "strategies": strategies,
            "duration_s": 0,
            "crash": -1,
            "trades": 0,
            "signals": 0,
            "error": "engine_not_running",
            "ts": datetime.now(timezone.utc).isoformat(),
        }

    # Look for most recent engine log
    log_files = sorted(_LOGS_DIR.glob("leviathan-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    active_log = log_files[0] if log_files else None

    start_time = time.monotonic()
    start_ts = datetime.now(timezone.utc).isoformat()

    # Read initial log size to compute delta
    initial_size = active_log.stat().st_size if active_log else 0

    logger.info("Monitoring for %ds (%.1fH)...", duration_s, duration_s / 3600)
    await asyncio.sleep(min(duration_s, 60))  # At minimum wait 60s for first metrics

    # Poll log for crash/trade indicators every 60s
    crash_count = 0
    trade_count = 0
    signal_count = 0
    error_msg = ""

    elapsed = time.monotonic() - start_time
    remaining = duration_s - elapsed

    poll_interval = 60  # seconds
    while remaining > 0:
        await asyncio.sleep(min(poll_interval, remaining))
        remaining = duration_s - (time.monotonic() - start_time)

        # Parse incremental log content
        if active_log and active_log.exists():
            try:
                content = active_log.read_text(errors="replace")
                new_content = content[initial_size:]
                crash_count = len(re.findall(r"(?i)(crash|critical|fatal|exception|traceback)", new_content))
                trade_count = len(re.findall(r"(?i)(trade_executed|order_filled|paper.*trade)", new_content))
                signal_count = len(re.findall(r"(?i)(signal_generated|new.*signal)", new_content))
                if crash_count > 0:
                    logger.warning("Case %s: detected %d crash indicators in log", case_id, crash_count)
            except Exception as exc:
                logger.debug("Log parse error: %s", exc)

        logger.info(
            "Case %s progress: elapsed=%.0fs/%.0fs trades=%d signals=%d crashes=%d",
            case_id, time.monotonic() - start_time, duration_s, trade_count, signal_count, crash_count,
        )

    actual_duration = time.monotonic() - start_time

    # Final log scan
    if active_log and active_log.exists():
        try:
            content = active_log.read_text(errors="replace")
            new_content = content[initial_size:]
            crash_count = len(re.findall(r"(?i)(crash|critical|fatal|exception|traceback)", new_content))
            trade_count = len(re.findall(r"(?i)(trade_executed|order_filled|paper.*trade)", new_content))
            signal_count = len(re.findall(r"(?i)(signal_generated|new.*signal)", new_content))
        except Exception:
            pass

    result = {
        "case_id": case_id,
        "exchanges": exchanges,
        "strategies": strategies,
        "duration_s": round(actual_duration, 1),
        "crash": crash_count,
        "trades": trade_count,
        "signals": signal_count,
        "error": error_msg,
        "ts": start_ts,
        "log_file": str(active_log) if active_log else None,
    }

    # Save per-case log marker
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))

    logger.info(
        "Case %s complete — duration=%.0fs crash=%d trades=%d signals=%d",
        case_id, actual_duration, crash_count, trade_count, signal_count,
    )
    return result


async def main(
    case_filter: list[str] | None = None,
    duration_override: int | None = None,
    status_only: bool = False,
) -> None:
    """Run paper test cases and update cumulative results."""
    state = _load_results()

    if status_only:
        _print_status(state)
        return

    if not case_filter:
        logger.error("No cases specified. Use --cases, --all-basic, or --all-extended.")
        _print_status(state)
        return

    # Validate case IDs
    unknown = [c for c in case_filter if c not in PAPER_CASES]
    if unknown:
        logger.error("Unknown case IDs: %s. Valid IDs: %s", unknown, list(PAPER_CASES.keys()))
        sys.exit(1)

    logger.info(
        "Running %d paper cases: %s",
        len(case_filter), case_filter,
    )

    for case_id in case_filter:
        case_config = PAPER_CASES[case_id]
        result = await _run_paper_case(
            case_id=case_id,
            case_config=case_config,
            duration_override=duration_override,
        )

        # Update cumulative state
        state["cases"][case_id] = result
        if result.get("error") != "engine_not_running":
            state["cumulative_runtime_s"] = state.get("cumulative_runtime_s", 0.0) + result["duration_s"]

        # Check US-332 threshold
        if state["cumulative_runtime_s"] >= _US332_THRESHOLD_S:
            state["us332_satisfied"] = True
            logger.info(
                "US-332 SATISFIED: cumulative_runtime=%.1fH >= 24H threshold",
                state["cumulative_runtime_s"] / 3600,
            )

        _save_results(state)

    _print_status(state)
    logger.info("Results saved: %s", _RESULTS_PATH)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Paper 테스트 누적 실행 스크립트 (US-372/332/382)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # 특정 케이스 2H 실행
  python scripts/run_paper_tests.py --cases P-01,P-02 --duration 7200

  # P-01~P-16 모두 실행 (기본 케이스)
  python scripts/run_paper_tests.py --all-basic

  # P-24~P-31 확장 케이스 실행
  python scripts/run_paper_tests.py --all-extended

  # 누적 실행 현황 확인
  python scripts/run_paper_tests.py --status

Note: Engine must be running before executing paper tests:
  cd engine && python -m src.main
        """,
    )
    parser.add_argument(
        "--cases",
        type=str,
        default="",
        help="Comma-separated case IDs (e.g. P-01,P-02)",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=None,
        help="Duration override in seconds (overrides per-case default)",
    )
    parser.add_argument(
        "--all-basic",
        action="store_true",
        help="Run P-01~P-16 (Tier1~3 basic cases)",
    )
    parser.add_argument(
        "--all-extended",
        action="store_true",
        help="Run P-24~P-31 (extended cases)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all cases P-01~P-31",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print cumulative status without running",
    )
    args = parser.parse_args()

    case_filter: list[str] | None = None
    if args.all:
        case_filter = _ALL_CASES
    elif args.all_basic:
        case_filter = _BASIC_CASES
    elif args.all_extended:
        case_filter = _EXTENDED_CASES
    elif args.cases:
        case_filter = [c.strip() for c in args.cases.split(",") if c.strip()]

    asyncio.run(main(
        case_filter=case_filter,
        duration_override=args.duration,
        status_only=args.status,
    ))
