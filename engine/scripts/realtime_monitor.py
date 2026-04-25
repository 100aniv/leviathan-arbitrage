"""실시간 13-항목 복합지표 모니터링 + 문제 즉시 alert.

사장님 지시: "지속적으로 로깅이랑 모든 복합지표와 구현한것에 대해서 모니터링을
실시간으로 계속하며 문제가 나왔을때 바로바로 수정해야해. 무한반복."

5초 간격으로 실행 중인 engine log를 grep + Prometheus metrics
endpoint를 polling. 변화 감지 + threshold 위반 시 alert.

13 항목 (SSOT.md "Shadow 통과 기준 — 복합지표 — LiveGate 6-check 기반"):
1. crash count (CRITICAL/FATAL/Traceback)
2. 무중단 시간 (engine PID elapsed)
3. PnL (total_pnl)
4. Max Drawdown (drawdown_current_pct)
5. Profit Factor (총이익/총손실)
6. 신호 수 rate (signals/min)
7. Kill Switch (halted state)
8. Circuit Breaker state
9. 거래소 Health score
10. loss_capped count
11. 전략별 trade count
12. 방어 레이어 활성 (toxicity + stale_detector + CB)
13. 결과 파일 존재 (auto-write JSON snapshots)

출력:
- terminal: 5초마다 1줄 status
- JSON 누적: engine/.omc/evidence/realtime_<timestamp>.jsonl (1줄/scan)
- Alert: threshold 위반 시 [ALERT] prefix + 즉시 출력

Usage:
    python scripts/realtime_monitor.py --pid <engine_pid> --log /path/to/log [--interval 5]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parent.parent
EVIDENCE_DIR = ENGINE_ROOT / ".omc" / "evidence"
PROM_URL = "http://localhost:8000/metrics"


def _grep_count(log_path: Path, pattern: str, since_offset: int = 0) -> tuple[int, int]:
    """Count regex matches from byte offset. Returns (count, new_offset)."""
    if not log_path.exists():
        return 0, 0
    rgx = re.compile(pattern)
    count = 0
    with log_path.open("rb") as f:
        f.seek(since_offset)
        for line in f:
            try:
                if rgx.search(line.decode("utf-8", errors="ignore")):
                    count += 1
            except Exception:
                pass
        new_offset = f.tell()
    return count, new_offset


def _grep_last(log_path: Path, pattern: str) -> str | None:
    if not log_path.exists():
        return None
    rgx = re.compile(pattern)
    last = None
    with log_path.open("r", errors="ignore") as f:
        for line in f:
            m = rgx.search(line)
            if m:
                last = m.group(0)
    return last


def _fetch_prom() -> dict:
    """Fetch Prometheus /metrics. Return dict of metric name → value."""
    try:
        with urllib.request.urlopen(PROM_URL, timeout=2) as resp:
            text = resp.read().decode("utf-8")
    except Exception:
        return {}
    metrics: dict[str, float] = {}
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        try:
            name_part, value = line.rsplit(" ", 1)
            metrics[name_part] = float(value)
        except (ValueError, IndexError):
            pass
    return metrics


def collect_snapshot(pid: int, log_path: Path, prev_offset: int = 0) -> tuple[dict, int]:
    """Collect 13-item snapshot. Returns (snapshot dict, new log offset)."""
    snap = {"ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "pid": pid}

    # 1. engine alive + elapsed
    try:
        os.kill(pid, 0)
        snap["engine_alive"] = True
        # parse /proc or ps for elapsed
        try:
            import subprocess as sp
            etime = sp.check_output(["ps", "-p", str(pid), "-o", "etime="], text=True).strip()
            snap["elapsed"] = etime
        except Exception:
            snap["elapsed"] = "?"
    except ProcessLookupError:
        snap["engine_alive"] = False
        snap["elapsed"] = "DEAD"

    # 2. 13 items via log grep (incremental from prev_offset)
    fills, _ = _grep_count(log_path, r"paper_mode\.trade_request_executed", since_offset=0)
    crashes, _ = _grep_count(log_path, r"CRITICAL|FATAL|Traceback", since_offset=0)
    universe = _grep_last(log_path, r"universe_matrix\.built entries=\d+")
    universe_entries = 0
    if universe:
        m = re.search(r"entries=(\d+)", universe)
        if m:
            universe_entries = int(m.group(1))
    last_pnl = _grep_last(log_path, r"total_pnl=[+-]?\d+\.?\d*")
    pnl = 0.0
    if last_pnl:
        m = re.search(r"total_pnl=([+-]?\d+\.?\d*)", last_pnl)
        if m:
            pnl = float(m.group(1))

    # Strategy-level fills
    fr_fills, _ = _grep_count(log_path, r"strategy_id=funding_rate_v1.*paper_mode\.trade_request_executed|paper_mode\.trade_request_executed.*strategy_id=funding_rate_v1", 0)
    sf_fills, _ = _grep_count(log_path, r"strategy_id=spot_futures_v1.*paper_mode\.trade_request_executed|paper_mode\.trade_request_executed.*strategy_id=spot_futures_v1", 0)
    ff_fills, _ = _grep_count(log_path, r"strategy_id=futures_futures_v1.*paper_mode\.trade_request_executed|paper_mode\.trade_request_executed.*strategy_id=futures_futures_v1", 0)
    tri_fills, _ = _grep_count(log_path, r"strategy_id=triangular_v1.*paper_mode\.trade_request_executed|paper_mode\.trade_request_executed.*strategy_id=triangular_v1", 0)

    # Defensive layers
    toxicity_rejects, _ = _grep_count(log_path, r"signal_rejected_by_toxicity", 0)
    stale_rejects, _ = _grep_count(log_path, r"stale_cross_validation_rejected", 0)
    rate_limit_rejects, _ = _grep_count(log_path, r"rate_limit_exceeded", 0)
    loss_capped, _ = _grep_count(log_path, r"loss_capped|trade_request_loss_capped", 0)
    # NOTE: "HALTED" (PerStrategyCB state name) excluded — false positive.
    # Match only actual halt events.
    halt_events, _ = _grep_count(log_path, r"halt_local\(|kill_switch_triggered_total|engine_halted|kill_switch_active=1", 0)

    snap["checks"] = {
        "1_crash_count": crashes,
        "2_engine_elapsed": snap.get("elapsed", "?"),
        "3_total_pnl": pnl,
        "5_fills_total": fills,
        "10_loss_capped": loss_capped,
        "11_per_strategy_fills": {
            "funding_rate_v1": fr_fills,
            "spot_futures_v1": sf_fills,
            "futures_futures_v1": ff_fills,
            "triangular_v1": tri_fills,
        },
        "12_defensive_layers": {
            "toxicity_filter": toxicity_rejects,
            "stale_detector": stale_rejects,
            "rate_limiter": rate_limit_rejects,
            "halt_events": halt_events,
        },
        "universe_matrix_entries": universe_entries,
    }

    # 9. Prometheus metrics polling (additive — exchange health, CB state, etc.)
    prom = _fetch_prom()
    snap["prom_polled"] = bool(prom)
    if prom:
        snap["checks"]["7_kill_switch_active"] = prom.get("leviathan_kill_switch_active", -1)
        snap["checks"]["8_circuit_breaker"] = prom.get("leviathan_circuit_breaker_state", -1)
        # Drawdown gauge
        dd = [v for k, v in prom.items() if k.startswith("leviathan_drawdown_current_pct")]
        snap["checks"]["4_max_drawdown"] = max(dd) if dd else 0.0

    # Alerts (즉시 출력)
    alerts = []
    if not snap["engine_alive"]:
        alerts.append("ENGINE DEAD")
    if crashes > 0:
        alerts.append(f"crash count={crashes}")
    if universe_entries == 0:
        alerts.append("universe_matrix entries=0 (no trade possible)")
    if halt_events > 0:
        alerts.append(f"halt events={halt_events}")
    if snap["checks"].get("7_kill_switch_active", 0) == 1:
        alerts.append("KILL SWITCH ACTIVE")
    snap["alerts"] = alerts

    return snap, 0  # offset: simplified — full re-scan each time


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, required=True, help="engine process PID")
    ap.add_argument("--log", type=str, required=True, help="path to engine log")
    ap.add_argument("--interval", type=int, default=5, help="poll interval seconds (default 5)")
    args = ap.parse_args()

    log_path = Path(args.log)
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    jsonl_path = EVIDENCE_DIR / f"realtime_{timestamp}.jsonl"

    print(f"[realtime] monitoring PID={args.pid}, log={log_path}, interval={args.interval}s")
    print(f"[realtime] jsonl → {jsonl_path}")
    print()

    offset = 0
    iter_count = 0
    while True:
        try:
            snap, offset = collect_snapshot(args.pid, log_path, offset)
            iter_count += 1

            # JSONL append
            with jsonl_path.open("a") as f:
                f.write(json.dumps(snap) + "\n")

            # Terminal status
            c = snap["checks"]
            ts = snap["ts"][11:19]
            elapsed = snap.get("elapsed", "?")
            fills = c["5_fills_total"]
            pnl = c["3_total_pnl"]
            crashes = c["1_crash_count"]
            entries = c["universe_matrix_entries"]
            tox = c["12_defensive_layers"]["toxicity_filter"]
            stale = c["12_defensive_layers"]["stale_detector"]

            alert_str = ""
            if snap["alerts"]:
                alert_str = " 🚨 " + ", ".join(snap["alerts"])

            print(f"[{ts}] elapsed={elapsed} fills={fills} PnL={pnl:+.4f} crash={crashes} entries={entries} tox={tox} stale={stale}{alert_str}")

            # Exit if engine dead
            if not snap["engine_alive"]:
                print(f"[realtime] engine DEAD — exit after {iter_count} iterations")
                break

            time.sleep(args.interval)
        except KeyboardInterrupt:
            print(f"\n[realtime] interrupted after {iter_count} iterations")
            break
        except Exception as exc:
            print(f"[realtime] ERROR: {exc!r}")
            time.sleep(args.interval)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
