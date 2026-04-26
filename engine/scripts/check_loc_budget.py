#!/usr/bin/env python3
"""LOC budget enforcer — Phase 5.5 (2026-04-26).

Path-B v2 §17 monotonic shrink rule:
- engine/src/runtime/*.py: ≤ 400 LOC (Phase 5 target, 산업 표준 Nautilus crate ~200-300)
- engine/src/main.py: ≤ 700 LOC (현재 689, monotonically shrink)
- engine/src/modes/live.py: ≤ 3500 LOC (현재 3250, Phase L extraction 후 2000)
- engine/src/modes/paper.py: ≤ 2800 LOC (현재 2734, Phase 3 deprecate 후 0)

usage:
    python engine/scripts/check_loc_budget.py [--strict]

exit code:
    0: all within budget
    1: budget violation (CI/pre-commit fail)
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent  # engine/

BUDGETS: list[tuple[str, int]] = [
    # path glob → max LOC
    ("src/main.py", 700),
    ("src/runtime/__init__.py", 50),
    ("src/runtime/ml_loops.py", 400),
    ("src/runtime/bootstrap.py", 500),
    ("src/runtime/exchange_init.py", 200),
    ("src/runtime/risk_execution.py", 1000),  # Phase 5.2.6 dispatcher 위임 후 ≤ 200 목표
    ("src/runtime/pipeline_init.py", 600),
    ("src/runtime/background_loops.py", 1000),  # Phase 5.3 LifecycleManager 통합 후 ≤ 400 목표
    ("src/runtime/mode_loops.py", 900),  # Phase 5.4 ModeRunner 통합 후 deprecate
    ("src/runtime/lifecycle_manager.py", 250),
    ("src/runtime/mode_runner.py", 200),
    ("src/modes/live.py", 3500),
    ("src/modes/paper.py", 2800),
    ("src/modes/shadow.py", 60),  # backward-compat shim only (currently 58 LOC)
]


def check() -> int:
    violations: list[str] = []
    for path_str, max_loc in BUDGETS:
        path = ROOT / path_str
        if not path.exists():
            continue
        loc = sum(1 for _ in path.read_text(encoding="utf-8").splitlines())
        status = "OK" if loc <= max_loc else "FAIL"
        marker = "✓" if status == "OK" else "✗"
        print(f"{marker} {path_str:50s} {loc:5d} / {max_loc:5d} {status}")
        if status == "FAIL":
            violations.append(f"{path_str}: {loc} > {max_loc}")

    if violations:
        print("\nBUDGET VIOLATIONS:", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(check())
