"""Shadow Stability Test Script.

Monitors a long-running shadow mode execution and reports on stability metrics:
CPU usage, RSS memory growth, crash detection, and PnL extraction from logs.

Usage::

    cd engine
    python scripts/shadow_stability_test.py --duration 1h
    python scripts/shadow_stability_test.py --duration 6h
    python scripts/shadow_stability_test.py --duration 12h

Requirements::

    pip install psutil

Results are appended to docs/shadow-stability-results.md.
"""
from __future__ import annotations

import argparse
import datetime
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import psutil
except ImportError:
    print("ERROR: psutil is required. Install with: pip install psutil")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_ENGINE_ROOT = Path(__file__).parent.parent
_RESULTS_DOC = _ENGINE_ROOT / "docs" / "shadow-stability-results.md"

DURATION_SECONDS: dict[str, int] = {
    "1h": 3600,
    "6h": 6 * 3600,
    "12h": 12 * 3600,
}

MONITOR_INTERVAL_S = 30          # collect metrics every 30 s
PASS_MAX_RSS_GROWTH_MB = 50.0    # max allowed RSS increase in MB
PASS_MAX_AVG_CPU_PCT = 80.0      # max allowed average CPU %

# PnL log pattern: "PnL: +3.45" or "total_pnl=3.45" or "pnl=3.45"
_PNL_PATTERNS = [
    re.compile(r"(?:total_)?[Pp]n[Ll]\s*[=:]\s*([+-]?\d+\.?\d*)"),
    re.compile(r"pnl=([+-]?\d+\.?\d*)"),
]

# Error log pattern
_ERROR_PATTERN = re.compile(r"\b(ERROR|CRITICAL|Exception|Traceback)\b")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class MetricSample:
    timestamp: float
    rss_mb: float
    cpu_pct: float


@dataclass
class StabilityReport:
    duration_label: str
    start_time: datetime.datetime
    end_time: datetime.datetime
    exit_code: Optional[int]          # None = timeout (expected OK)
    samples: list[MetricSample] = field(default_factory=list)
    error_count: int = 0
    pnl: Optional[float] = None

    # --- computed ---
    @property
    def elapsed_seconds(self) -> float:
        return (self.end_time - self.start_time).total_seconds()

    @property
    def rss_initial_mb(self) -> float:
        return self.samples[0].rss_mb if self.samples else 0.0

    @property
    def rss_final_mb(self) -> float:
        return self.samples[-1].rss_mb if self.samples else 0.0

    @property
    def rss_max_mb(self) -> float:
        return max((s.rss_mb for s in self.samples), default=0.0)

    @property
    def rss_min_mb(self) -> float:
        return min((s.rss_mb for s in self.samples), default=0.0)

    @property
    def rss_avg_mb(self) -> float:
        if not self.samples:
            return 0.0
        return sum(s.rss_mb for s in self.samples) / len(self.samples)

    @property
    def rss_growth_mb(self) -> float:
        return self.rss_final_mb - self.rss_initial_mb

    @property
    def cpu_avg_pct(self) -> float:
        if not self.samples:
            return 0.0
        return sum(s.cpu_pct for s in self.samples) / len(self.samples)

    @property
    def crashed(self) -> bool:
        """True if the process exited with non-zero code (not timeout)."""
        return self.exit_code is not None and self.exit_code != 0

    @property
    def pass_crash(self) -> bool:
        return not self.crashed

    @property
    def pass_memory(self) -> bool:
        return self.rss_growth_mb < PASS_MAX_RSS_GROWTH_MB

    @property
    def pass_cpu(self) -> bool:
        return self.cpu_avg_pct < PASS_MAX_AVG_CPU_PCT

    @property
    def pass_pnl(self) -> bool:
        """PnL check only applies when duration >= 1h and PnL was captured."""
        if self.pnl is None:
            return True  # cannot verify — treat as inconclusive (not fail)
        return self.pnl > 0

    @property
    def overall_pass(self) -> bool:
        return self.pass_crash and self.pass_memory and self.pass_cpu


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a long-duration shadow mode stability test.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--duration",
        choices=["1h", "6h", "12h"],
        default="1h",
        help="Test duration: 1h, 6h, or 12h",
    )
    parser.add_argument(
        "--engine-root",
        type=Path,
        default=_ENGINE_ROOT,
        help="Path to the engine directory (default: parent of this script)",
    )
    parser.add_argument(
        "--env",
        default=None,
        help="ENGINE_ENV override (default: uses current env or 'dev')",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Skip writing results to docs/shadow-stability-results.md",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Monitoring loop
# ---------------------------------------------------------------------------

def _collect_sample(proc: "psutil.Process") -> MetricSample:
    """Collect one RSS + CPU sample from a live process."""
    info = proc.as_dict(attrs=["memory_info", "cpu_percent"])
    rss_mb = info["memory_info"].rss / (1024 * 1024)
    cpu_pct = info.get("cpu_percent") or 0.0
    return MetricSample(timestamp=time.time(), rss_mb=rss_mb, cpu_pct=cpu_pct)


def _extract_pnl(log_output: str) -> Optional[float]:
    """Try to extract a PnL value from captured log output."""
    for pattern in _PNL_PATTERNS:
        matches = pattern.findall(log_output)
        if matches:
            try:
                return float(matches[-1])  # take the last (most recent) match
            except ValueError:
                pass
    return None


def run_stability_test(
    duration_label: str,
    engine_root: Path,
    env_override: Optional[str] = None,
) -> StabilityReport:
    """Execute `python -m src.main` under monitoring for the given duration.

    Args:
        duration_label: "1h", "6h", or "12h"
        engine_root: Path to the engine directory.
        env_override: Optional ENGINE_ENV value.

    Returns:
        A populated StabilityReport.
    """
    duration_s = DURATION_SECONDS[duration_label]

    # Build environment for the subprocess
    env = os.environ.copy()
    if env_override:
        env["ENGINE_ENV"] = env_override
    elif "ENGINE_ENV" not in env:
        env["ENGINE_ENV"] = "dev"

    cmd = [sys.executable, "-m", "src.main"]
    print(
        f"[stability] Starting shadow stability test: duration={duration_label} "
        f"({duration_s}s), cwd={engine_root}"
    )
    print(f"[stability] Command: {' '.join(cmd)}")
    print(f"[stability] ENGINE_ENV={env['ENGINE_ENV']}")
    print(f"[stability] Monitoring every {MONITOR_INTERVAL_S}s …\n")

    start_dt = datetime.datetime.now(datetime.timezone.utc)
    log_lines: list[str] = []
    samples: list[MetricSample] = []
    error_count = 0

    proc = subprocess.Popen(
        cmd,
        cwd=str(engine_root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    ps_proc: Optional["psutil.Process"] = None
    try:
        ps_proc = psutil.Process(proc.pid)
        # Warm up CPU% measurement (first call always returns 0)
        ps_proc.cpu_percent(interval=None)
    except psutil.NoSuchProcess:
        pass

    deadline = time.time() + duration_s
    next_sample_at = time.time() + MONITOR_INTERVAL_S
    exit_code: Optional[int] = None

    # Non-blocking line reader + periodic sampler
    import threading
    stdout_buffer: list[str] = []
    stop_event = threading.Event()

    def _reader():
        assert proc.stdout is not None
        for line in proc.stdout:
            stdout_buffer.append(line)

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    try:
        while time.time() < deadline:
            # Drain stdout buffer
            while stdout_buffer:
                line = stdout_buffer.pop(0)
                log_lines.append(line)
                if _ERROR_PATTERN.search(line):
                    error_count += 1

            # Periodic metric sample
            if time.time() >= next_sample_at:
                if ps_proc is not None:
                    try:
                        sample = _collect_sample(ps_proc)
                        samples.append(sample)
                        elapsed = time.time() - (deadline - duration_s)
                        print(
                            f"[stability] t+{elapsed:.0f}s | "
                            f"RSS={sample.rss_mb:.1f}MB "
                            f"CPU={sample.cpu_pct:.1f}% "
                            f"errors={error_count}"
                        )
                    except psutil.NoSuchProcess:
                        print("[stability] Process exited early.")
                        break
                next_sample_at = time.time() + MONITOR_INTERVAL_S

            # Check if process has already ended
            ret = proc.poll()
            if ret is not None:
                exit_code = ret
                print(f"[stability] Process exited with code={exit_code} before timeout.")
                break

            time.sleep(1)

        else:
            # Timeout reached — this is the normal/expected path
            print(f"\n[stability] Duration {duration_label} elapsed. Terminating process …")
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            exit_code = None  # timeout = expected completion

    except KeyboardInterrupt:
        print("\n[stability] Interrupted. Terminating …")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        exit_code = -2

    reader_thread.join(timeout=5)

    # Drain remaining buffer
    while stdout_buffer:
        line = stdout_buffer.pop(0)
        log_lines.append(line)
        if _ERROR_PATTERN.search(line):
            error_count += 1

    end_dt = datetime.datetime.now(datetime.timezone.utc)
    full_log = "".join(log_lines)
    pnl = _extract_pnl(full_log)

    return StabilityReport(
        duration_label=duration_label,
        start_time=start_dt,
        end_time=end_dt,
        exit_code=exit_code,
        samples=samples,
        error_count=error_count,
        pnl=pnl,
    )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _verdict(passed: bool) -> str:
    return "PASS" if passed else "FAIL"


def print_report(report: StabilityReport) -> None:
    """Print the stability report to stdout."""
    sep = "=" * 60
    print(f"\n{sep}")
    print(f"  SHADOW STABILITY REPORT — {report.duration_label.upper()}")
    print(sep)
    print(f"  Started : {report.start_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  Ended   : {report.end_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  Elapsed : {report.elapsed_seconds:.0f}s ({report.elapsed_seconds/3600:.2f}h)")
    print()
    print("  -- Process --")
    crashed_str = "YES (non-zero exit)" if report.crashed else "No"
    exit_str = str(report.exit_code) if report.exit_code is not None else "timeout (OK)"
    print(f"  Crash        : {crashed_str}  [{_verdict(report.pass_crash)}]")
    print(f"  Exit code    : {exit_str}")
    print(f"  Error lines  : {report.error_count}")
    print()
    print("  -- Memory (RSS) --")
    print(f"  Initial      : {report.rss_initial_mb:.1f} MB")
    print(f"  Final        : {report.rss_final_mb:.1f} MB")
    print(f"  Min          : {report.rss_min_mb:.1f} MB")
    print(f"  Max          : {report.rss_max_mb:.1f} MB")
    print(f"  Avg          : {report.rss_avg_mb:.1f} MB")
    print(
        f"  Growth       : {report.rss_growth_mb:+.1f} MB  "
        f"(limit < {PASS_MAX_RSS_GROWTH_MB:.0f} MB)  [{_verdict(report.pass_memory)}]"
    )
    print()
    print("  -- CPU --")
    print(
        f"  Avg CPU      : {report.cpu_avg_pct:.1f}%  "
        f"(limit < {PASS_MAX_AVG_CPU_PCT:.0f}%)  [{_verdict(report.pass_cpu)}]"
    )
    print()
    print("  -- PnL --")
    pnl_str = f"{report.pnl:+.4f}" if report.pnl is not None else "N/A (not found in logs)"
    print(f"  PnL          : {pnl_str}")
    print()
    overall = _verdict(report.overall_pass)
    print(f"  OVERALL: {overall}")
    print(sep)


def build_markdown_section(report: StabilityReport) -> str:
    """Build a markdown section suitable for appending to shadow-stability-results.md."""
    ts = report.start_time.strftime("%Y-%m-%d %H:%M UTC")
    overall = "✅ PASS" if report.overall_pass else "❌ FAIL"
    crash_str = "✅ No crash" if report.pass_crash else "❌ CRASHED"
    exit_str = str(report.exit_code) if report.exit_code is not None else "timeout (OK)"
    mem_str = (
        f"✅ +{report.rss_growth_mb:.1f} MB"
        if report.pass_memory
        else f"❌ +{report.rss_growth_mb:.1f} MB (exceeded {PASS_MAX_RSS_GROWTH_MB:.0f} MB limit)"
    )
    cpu_str = (
        f"✅ {report.cpu_avg_pct:.1f}%"
        if report.pass_cpu
        else f"❌ {report.cpu_avg_pct:.1f}% (exceeded {PASS_MAX_AVG_CPU_PCT:.0f}% limit)"
    )
    pnl_str = f"{report.pnl:+.4f}" if report.pnl is not None else "N/A"

    lines = [
        f"## {report.duration_label.upper()} Shadow Stability — {ts}",
        "",
        f"**Result**: {overall}",
        "",
        "| Metric | Value | Status |",
        "|--------|-------|--------|",
        f"| Duration | {report.duration_label} ({report.elapsed_seconds:.0f}s) | — |",
        f"| Crash | exit={exit_str} | {crash_str} |",
        f"| Error lines | {report.error_count} | — |",
        f"| RSS initial | {report.rss_initial_mb:.1f} MB | — |",
        f"| RSS final | {report.rss_final_mb:.1f} MB | — |",
        f"| RSS growth | +{report.rss_growth_mb:.1f} MB | {mem_str} |",
        f"| RSS max | {report.rss_max_mb:.1f} MB | — |",
        f"| RSS avg | {report.rss_avg_mb:.1f} MB | — |",
        f"| CPU avg | {report.cpu_avg_pct:.1f}% | {cpu_str} |",
        f"| PnL | {pnl_str} | {'✅ > 0' if report.pass_pnl and report.pnl is not None else '—'} |",
        "",
        "---",
        "",
    ]
    return "\n".join(lines)


def write_results(report: StabilityReport, results_path: Path = _RESULTS_DOC) -> None:
    """Append a markdown section to the results document."""
    results_path.parent.mkdir(parents=True, exist_ok=True)

    if not results_path.exists():
        header = "# Shadow Stability Test Results\n\nAuto-generated by `scripts/shadow_stability_test.py`.\n\n---\n\n"
        results_path.write_text(header, encoding="utf-8")

    section = build_markdown_section(report)
    with results_path.open("a", encoding="utf-8") as fh:
        fh.write(section)

    print(f"\n[stability] Results appended to {results_path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    report = run_stability_test(
        duration_label=args.duration,
        engine_root=args.engine_root,
        env_override=args.env,
    )

    print_report(report)

    if not args.no_write:
        write_results(report)

    return 0 if report.overall_pass else 1


if __name__ == "__main__":
    sys.exit(main())
