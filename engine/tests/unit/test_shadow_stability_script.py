"""Unit tests for engine/scripts/shadow_stability_test.py."""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

import pytest

# Make the scripts directory importable
_SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from shadow_stability_test import (
    MetricSample,
    StabilityReport,
    build_markdown_section,
    parse_args,
)


# ---------------------------------------------------------------------------
# test_script_exists
# ---------------------------------------------------------------------------

def test_script_exists():
    """The stability test script must exist at the expected path."""
    script = Path(__file__).parent.parent.parent / "scripts" / "shadow_stability_test.py"
    assert script.exists(), f"Script not found: {script}"
    assert script.stat().st_size > 0, "Script file is empty"


# ---------------------------------------------------------------------------
# test_parse_args
# ---------------------------------------------------------------------------

class TestParseArgs:
    def test_default_duration(self):
        args = parse_args([])
        assert args.duration == "1h"

    def test_duration_1h(self):
        args = parse_args(["--duration", "1h"])
        assert args.duration == "1h"

    def test_duration_6h(self):
        args = parse_args(["--duration", "6h"])
        assert args.duration == "6h"

    def test_duration_12h(self):
        args = parse_args(["--duration", "12h"])
        assert args.duration == "12h"

    def test_invalid_duration_raises(self):
        with pytest.raises(SystemExit):
            parse_args(["--duration", "2h"])

    def test_no_write_flag(self):
        args = parse_args(["--no-write"])
        assert args.no_write is True

    def test_no_write_default_false(self):
        args = parse_args([])
        assert args.no_write is False

    def test_env_override(self):
        args = parse_args(["--env", "staging"])
        assert args.env == "staging"

    def test_env_default_none(self):
        args = parse_args([])
        assert args.env is None


# ---------------------------------------------------------------------------
# test_report_generation
# ---------------------------------------------------------------------------

def _make_report(
    *,
    duration_label: str = "1h",
    exit_code: int | None = None,
    rss_values: list[float] | None = None,
    cpu_values: list[float] | None = None,
    error_count: int = 0,
    pnl: float | None = 1.23,
) -> StabilityReport:
    """Helper to build a StabilityReport with synthetic samples."""
    now = datetime.datetime(2026, 3, 11, 12, 0, 0, tzinfo=datetime.timezone.utc)
    rss = rss_values or [100.0, 110.0, 115.0]
    cpu = cpu_values or [20.0, 25.0, 22.0]
    import time as _time
    base_ts = _time.time()
    samples = [
        MetricSample(timestamp=base_ts + i * 30, rss_mb=r, cpu_pct=c)
        for i, (r, c) in enumerate(zip(rss, cpu))
    ]
    return StabilityReport(
        duration_label=duration_label,
        start_time=now,
        end_time=now + datetime.timedelta(hours=1),
        exit_code=exit_code,
        samples=samples,
        error_count=error_count,
        pnl=pnl,
    )


class TestReportGeneration:
    # --- computed properties ---

    def test_rss_initial(self):
        r = _make_report(rss_values=[100.0, 120.0, 140.0])
        assert r.rss_initial_mb == pytest.approx(100.0)

    def test_rss_final(self):
        r = _make_report(rss_values=[100.0, 120.0, 140.0])
        assert r.rss_final_mb == pytest.approx(140.0)

    def test_rss_growth(self):
        r = _make_report(rss_values=[100.0, 120.0, 140.0])
        assert r.rss_growth_mb == pytest.approx(40.0)

    def test_rss_max(self):
        r = _make_report(rss_values=[100.0, 150.0, 140.0])
        assert r.rss_max_mb == pytest.approx(150.0)

    def test_rss_min(self):
        r = _make_report(rss_values=[100.0, 150.0, 90.0])
        assert r.rss_min_mb == pytest.approx(90.0)

    def test_rss_avg(self):
        r = _make_report(rss_values=[100.0, 200.0, 300.0])
        assert r.rss_avg_mb == pytest.approx(200.0)

    def test_cpu_avg(self):
        r = _make_report(cpu_values=[10.0, 20.0, 30.0])
        assert r.cpu_avg_pct == pytest.approx(20.0)

    # --- pass/fail criteria ---

    def test_pass_when_timeout(self):
        r = _make_report(exit_code=None)  # timeout = expected
        assert r.pass_crash is True
        assert r.crashed is False

    def test_fail_when_nonzero_exit(self):
        r = _make_report(exit_code=1)
        assert r.crashed is True
        assert r.pass_crash is False

    def test_pass_memory_below_limit(self):
        r = _make_report(rss_values=[100.0, 110.0, 120.0])  # growth = 20 MB < 50
        assert r.pass_memory is True

    def test_fail_memory_above_limit(self):
        r = _make_report(rss_values=[100.0, 130.0, 160.0])  # growth = 60 MB > 50
        assert r.pass_memory is False

    def test_pass_cpu_below_limit(self):
        r = _make_report(cpu_values=[50.0, 60.0, 55.0])
        assert r.pass_cpu is True

    def test_fail_cpu_above_limit(self):
        r = _make_report(cpu_values=[85.0, 90.0, 88.0])
        assert r.pass_cpu is False

    def test_overall_pass_all_good(self):
        r = _make_report(
            exit_code=None,
            rss_values=[100.0, 110.0, 115.0],
            cpu_values=[30.0, 35.0, 32.0],
            pnl=1.5,
        )
        assert r.overall_pass is True

    def test_overall_fail_crash(self):
        r = _make_report(exit_code=2)
        assert r.overall_pass is False

    def test_overall_fail_memory(self):
        r = _make_report(rss_values=[100.0, 200.0])  # growth = 100 MB
        assert r.overall_pass is False

    def test_pnl_positive_pass(self):
        r = _make_report(pnl=0.5)
        assert r.pass_pnl is True

    def test_pnl_negative_fail(self):
        r = _make_report(pnl=-1.0)
        assert r.pass_pnl is False

    def test_pnl_none_inconclusive(self):
        r = _make_report(pnl=None)
        assert r.pass_pnl is True  # cannot verify = not a failure

    # --- markdown output ---

    def test_markdown_contains_duration_label(self):
        r = _make_report(duration_label="6h")
        md = build_markdown_section(r)
        assert "6H" in md or "6h" in md

    def test_markdown_contains_pass(self):
        r = _make_report(exit_code=None, rss_values=[100.0, 110.0], cpu_values=[30.0, 35.0])
        md = build_markdown_section(r)
        assert "PASS" in md

    def test_markdown_contains_fail(self):
        r = _make_report(exit_code=1)
        md = build_markdown_section(r)
        assert "FAIL" in md

    def test_markdown_contains_rss_metrics(self):
        r = _make_report(rss_values=[100.0, 120.0, 140.0])
        md = build_markdown_section(r)
        assert "RSS" in md
        assert "100" in md

    def test_markdown_contains_cpu_metric(self):
        r = _make_report(cpu_values=[40.0, 50.0])
        md = build_markdown_section(r)
        assert "CPU" in md

    def test_markdown_contains_pnl(self):
        r = _make_report(pnl=2.50)
        md = build_markdown_section(r)
        assert "PnL" in md or "pnl" in md.lower()

    def test_markdown_ends_with_separator(self):
        r = _make_report()
        md = build_markdown_section(r)
        assert md.strip().endswith("---")

    def test_empty_samples_no_crash(self):
        """Report with no samples should not raise."""
        now = datetime.datetime(2026, 3, 11, tzinfo=datetime.timezone.utc)
        r = StabilityReport(
            duration_label="1h",
            start_time=now,
            end_time=now + datetime.timedelta(hours=1),
            exit_code=None,
            samples=[],
            error_count=0,
            pnl=None,
        )
        assert r.rss_initial_mb == 0.0
        assert r.rss_final_mb == 0.0
        assert r.cpu_avg_pct == 0.0
        md = build_markdown_section(r)
        assert isinstance(md, str)
