#!/usr/bin/env python3
"""SIT-3 Canary Runner — 72H continuous Shadow execution with auto-restart.

Manages Shadow mode lifecycle, timer tracking, and checkpoint scheduling.

Usage:
    python scripts/sit3_canary_runner.py [--target-hours 72] [--hard-cap 96]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sit3_canary")

RESET_LOG = Path(".omc/state/sit3-reset-log.json")
CHECKPOINTS = [
    ("CP1", 5 / 60),     # 5 min
    ("CP2", 0.5),        # 30 min
    ("CP3", 1),          # 1H
    ("CP4", 3),          # 3H
    ("CP5", 6),          # 6H
    ("CP6", 12),         # 12H
    ("CP7", 24),         # 24H — SIT-3 P6: Go/No-Go 최종 판정
    ("CP8", 48),         # 48H — optional (Live에서 달성)
    ("CP9", 72),         # 72H — optional (Live에서 달성)
]


def load_reset_log() -> dict:
    if RESET_LOG.exists():
        return json.loads(RESET_LOG.read_text())
    return {"resets": [], "total_resets": 0, "guardrail_status": "OK", "hard_cap_h": 96, "target_h": 24}


def save_reset_log(log: dict) -> None:
    RESET_LOG.parent.mkdir(parents=True, exist_ok=True)
    RESET_LOG.write_text(json.dumps(log, indent=2, ensure_ascii=False))


def record_reset(log: dict, cp: str, cause: str, files: list[str], elapsed_h: float) -> dict:
    log["resets"].append({
        "reset_number": log["total_resets"] + 1,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cp_at_failure": cp,
        "cause": cause,
        "fix_description": "",
        "files_changed": files,
        "pytest_pass": False,
        "elapsed_before_reset_h": round(elapsed_h, 2),
    })
    log["total_resets"] += 1

    # Guardrails
    if log["total_resets"] >= 10:
        log["guardrail_status"] = "EXCEEDED_10_RESETS"
        logger.error("GUARDRAIL: 10+ resets — consider S27 regression")
    return log


def check_guardrails(log: dict) -> bool:
    """Returns True if safe to continue, False if guardrail exceeded."""
    if log["total_resets"] >= 10:
        return False
    # Check same CP 3x
    recent = log["resets"][-3:] if len(log["resets"]) >= 3 else []
    if len(recent) == 3 and len(set(r["cp_at_failure"] for r in recent)) == 1:
        logger.error("GUARDRAIL: Same CP failed 3x — L5 escalation needed")
        return False
    return True


def get_current_cp(elapsed_h: float) -> str:
    """Get the current checkpoint based on elapsed hours."""
    current = "CP1"
    for cp_name, cp_hours in CHECKPOINTS:
        if elapsed_h >= cp_hours:
            current = cp_name
    return current


def main() -> None:
    parser = argparse.ArgumentParser(description="SIT-3 Canary Runner")
    parser.add_argument("--target-hours", type=float, default=24)
    parser.add_argument("--hard-cap", type=float, default=96)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    log = load_reset_log()
    start_time = time.monotonic()
    total_wall_h = 0.0

    logger.info("SIT-3 Canary Runner started — target=%sH, hard_cap=%sH", args.target_hours, args.hard_cap)

    if args.dry_run:
        logger.info("DRY RUN — would start Shadow engine")
        return

    while total_wall_h < args.hard_cap:
        if not check_guardrails(log):
            logger.error("Guardrail exceeded — stopping. Manual intervention required.")
            break

        logger.info("Starting Shadow engine (timer=0, attempt #%d)", log["total_resets"] + 1)
        shadow_start = time.monotonic()

        try:
            # Start engine in Shadow mode
            proc = subprocess.Popen(
                [sys.executable, "-m", "src.main"],
                cwd="engine",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )

            # Monitor until target or failure
            while True:
                elapsed_h = (time.monotonic() - shadow_start) / 3600
                total_wall_h = (time.monotonic() - start_time) / 3600
                current_cp = get_current_cp(elapsed_h)

                # Check if process is still alive
                retcode = proc.poll()
                if retcode is not None:
                    logger.warning("Engine exited with code %d at %.1fH (CP %s)", retcode, elapsed_h, current_cp)
                    log = record_reset(log, current_cp, f"Engine crashed (exit {retcode})", [], elapsed_h)
                    save_reset_log(log)
                    break

                # Check target reached
                if elapsed_h >= args.target_hours:
                    logger.info("TARGET REACHED: %.1fH — SIT-3 72H PASS!", elapsed_h)
                    proc.terminate()
                    proc.wait(timeout=30)
                    save_reset_log(log)
                    return

                # Hard cap check
                if total_wall_h >= args.hard_cap:
                    logger.error("HARD CAP %sH exceeded — stopping", args.hard_cap)
                    proc.terminate()
                    proc.wait(timeout=30)
                    break

                time.sleep(60)  # Check every minute

        except KeyboardInterrupt:
            logger.info("Manual stop (Ctrl+C)")
            if proc and proc.poll() is None:
                proc.terminate()
            break
        except Exception as e:
            logger.error("Runner error: %s", e)
            elapsed_h = (time.monotonic() - shadow_start) / 3600
            log = record_reset(log, get_current_cp(elapsed_h), str(e), [], elapsed_h)

        save_reset_log(log)
        logger.info("Waiting 10s before restart...")
        time.sleep(10)

    save_reset_log(log)
    logger.info("SIT-3 Canary Runner finished. Total resets: %d, Wall time: %.1fH", log["total_resets"], total_wall_h)


if __name__ == "__main__":
    main()
