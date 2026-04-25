"""Pre-canary 4-item dry-run check (OPERATOR_RUNBOOK §0.5).

Runs `python -m src.main` for N seconds (default 300=5min), kills it,
then greps the log for 4 mandatory pass criteria:

1. universe_matrix.built entries > 0
2. paper_mode.trade_request_executed >= 1
3. CRITICAL/FATAL/Traceback count == 0
4. total_pnl > 0

Exits 0 if 4/4 PASS, exit 1 otherwise. JSON report written to
engine/.omc/evidence/pre_canary_YYYYMMDD_HHMMSS.json.

Usage:
    cd engine && python scripts/pre_canary_check.py [--seconds 300]

Trigger: must run before any paper canary >= 1 hour starts (RUNBOOK §0.5).
14h carriage 2026-04-21 헛수고 재발 방지 룰.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parent.parent
EVIDENCE_DIR = ENGINE_ROOT / ".omc" / "evidence"


def _run_engine(seconds: int, log_path: Path) -> int:
    """Spawn engine subprocess, run for `seconds`, kill, return exit code."""
    proc = subprocess.Popen(
        ["python", "-m", "src.main"],
        cwd=str(ENGINE_ROOT),
        stdout=open(log_path, "w"),
        stderr=subprocess.STDOUT,
        preexec_fn=os.setsid if hasattr(os, "setsid") else None,
    )
    print(f"[pre_canary] spawned PID={proc.pid}, running for {seconds}s")
    time.sleep(seconds)

    # Graceful SIGTERM, then SIGKILL after 5s
    try:
        if hasattr(os, "killpg"):
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        else:
            proc.terminate()
        proc.wait(timeout=5)
    except (subprocess.TimeoutExpired, ProcessLookupError):
        if hasattr(os, "killpg"):
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            proc.kill()
    return proc.returncode or -1


def _grep_count(log_path: Path, pattern: str) -> int:
    """Count regex matches in log file. Returns 0 on file not found."""
    if not log_path.exists():
        return 0
    rgx = re.compile(pattern)
    count = 0
    with log_path.open("r", errors="ignore") as f:
        for line in f:
            if rgx.search(line):
                count += 1
    return count


def _grep_last_match(log_path: Path, pattern: str) -> str | None:
    """Return last regex match group(0) in log file, or None."""
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


def analyze(log_path: Path) -> dict:
    """Extract 4 pass criteria from engine log."""
    universe_line = _grep_last_match(log_path, r"universe_matrix\.built entries=\d+ strategies=\d+ exchanges=\d+")
    entries = 0
    if universe_line:
        m = re.search(r"entries=(\d+)", universe_line)
        if m:
            entries = int(m.group(1))

    fill_count = _grep_count(log_path, r"paper_mode\.trade_request_executed")
    crash_count = _grep_count(log_path, r"CRITICAL|FATAL|Traceback")

    last_pnl = _grep_last_match(log_path, r"total_pnl=[+-]?\d+\.?\d*")
    pnl = 0.0
    if last_pnl:
        m = re.search(r"total_pnl=([+-]?\d+\.?\d*)", last_pnl)
        if m:
            pnl = float(m.group(1))

    checks = {
        "universe_matrix_entries": entries,
        "paper_trade_fills": fill_count,
        "crash_count": crash_count,
        "total_pnl": pnl,
    }
    pass_flags = {
        "ac1_universe_matrix_gt_0": entries > 0,
        "ac2_fill_ge_1": fill_count >= 1,
        "ac3_crash_eq_0": crash_count == 0,
        "ac4_pnl_gt_0": pnl > 0,
    }
    return {
        "log_path": str(log_path),
        "universe_matrix_line": universe_line,
        "checks": checks,
        "pass_flags": pass_flags,
        "all_pass": all(pass_flags.values()),
        "checked_at": datetime.utcnow().isoformat() + "Z",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=300, help="dry-run duration (default 300s = 5min)")
    ap.add_argument("--log-path", type=str, default="", help="reuse existing log instead of running engine")
    args = ap.parse_args()

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    if args.log_path:
        log_path = Path(args.log_path)
        if not log_path.exists():
            print(f"[pre_canary] ERROR: log not found at {log_path}")
            return 1
        print(f"[pre_canary] reusing existing log: {log_path}")
    else:
        log_path = ENGINE_ROOT / "logs" / f"pre_canary_{timestamp}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        rc = _run_engine(args.seconds, log_path)
        print(f"[pre_canary] engine exited rc={rc}")

    report = analyze(log_path)
    report["seconds"] = args.seconds

    out_path = EVIDENCE_DIR / f"pre_canary_{timestamp}.json"
    with out_path.open("w") as f:
        json.dump(report, f, indent=2)

    print(f"\n[pre_canary] report → {out_path}")
    print(f"  universe_matrix_entries: {report['checks']['universe_matrix_entries']}")
    print(f"  paper_trade_fills:       {report['checks']['paper_trade_fills']}")
    print(f"  crash_count:             {report['checks']['crash_count']}")
    print(f"  total_pnl:               {report['checks']['total_pnl']}")
    print(f"  pass_flags:              {report['pass_flags']}")
    print(f"\n[pre_canary] ALL_PASS = {report['all_pass']}")

    return 0 if report["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
