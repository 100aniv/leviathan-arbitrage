"""Tests for compliance audit — Blueprint compliance verification."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.infra.compliance import (
    ComplianceChecker,
    ComplianceItem,
    ComplianceReport,
    ComplianceStatus,
)


# ---------------------------------------------------------------------------
# ComplianceStatus enum
# ---------------------------------------------------------------------------

class TestComplianceStatus:
    def test_values(self):
        assert ComplianceStatus.PASS == "PASS"
        assert ComplianceStatus.FAIL == "FAIL"
        assert ComplianceStatus.PARTIAL == "PARTIAL"
        assert ComplianceStatus.SKIPPED == "SKIPPED"

    def test_is_str(self):
        assert isinstance(ComplianceStatus.PASS, str)


# ---------------------------------------------------------------------------
# ComplianceItem dataclass
# ---------------------------------------------------------------------------

class TestComplianceItem:
    def test_basic_creation(self):
        item = ComplianceItem(
            category="core_principle",
            name="revenue_first",
            status=ComplianceStatus.PASS,
            description="Revenue > $1/day",
        )
        assert item.category == "core_principle"
        assert item.status == ComplianceStatus.PASS
        assert item.detail == ""
        assert item.recommendation == ""

    def test_with_detail_and_recommendation(self):
        item = ComplianceItem(
            category="kill_switch",
            name="tier1_latency",
            status=ComplianceStatus.FAIL,
            description="Tier 1 < 1ms",
            detail="Measured: 5ms",
            recommendation="Optimize halt flag path",
        )
        assert item.detail == "Measured: 5ms"
        assert item.recommendation != ""


# ---------------------------------------------------------------------------
# ComplianceReport dataclass
# ---------------------------------------------------------------------------

class TestComplianceReport:
    def _make_report(self, statuses: list[ComplianceStatus]) -> ComplianceReport:
        items = [
            ComplianceItem(
                category="test",
                name=f"check_{i}",
                status=s,
                description=f"Check {i}",
            )
            for i, s in enumerate(statuses)
        ]
        return ComplianceReport(
            timestamp=datetime.now(timezone.utc),
            items=items,
        )

    def test_empty_report(self):
        report = ComplianceReport(timestamp=datetime.now(timezone.utc))
        assert report.total_count == 0
        assert report.pass_count == 0
        assert report.score_pct == 0.0

    def test_all_pass(self):
        report = self._make_report([ComplianceStatus.PASS] * 5)
        assert report.pass_count == 5
        assert report.fail_count == 0
        assert report.score_pct == 100.0

    def test_mixed_statuses(self):
        report = self._make_report([
            ComplianceStatus.PASS,
            ComplianceStatus.PASS,
            ComplianceStatus.FAIL,
            ComplianceStatus.PARTIAL,
        ])
        assert report.pass_count == 2
        assert report.fail_count == 1
        assert report.partial_count == 1
        assert report.total_count == 4
        assert report.score_pct == 50.0  # 2/4

    def test_skipped_excluded_from_score(self):
        report = self._make_report([
            ComplianceStatus.PASS,
            ComplianceStatus.PASS,
            ComplianceStatus.SKIPPED,
        ])
        # 2 pass out of 2 non-skipped = 100%
        assert report.score_pct == 100.0
        assert report.skipped_count == 1

    def test_summary_string(self):
        report = self._make_report([ComplianceStatus.PASS, ComplianceStatus.FAIL])
        summary = report.summary()
        assert "50.0%" in summary
        assert "1/2 PASS" in summary
        assert "1 FAIL" in summary


# ---------------------------------------------------------------------------
# ComplianceChecker
# ---------------------------------------------------------------------------

class TestComplianceChecker:
    def test_init(self):
        auditor = ComplianceChecker()
        assert isinstance(auditor, ComplianceChecker)

    @pytest.mark.asyncio
    async def test_audit_returns_report(self):
        auditor = ComplianceChecker()
        report = await auditor.run_audit()
        assert isinstance(report, ComplianceReport)
        assert report.total_count > 0

    @pytest.mark.asyncio
    async def test_audit_covers_core_categories(self):
        auditor = ComplianceChecker()
        report = await auditor.run_audit()
        categories = {item.category for item in report.items}
        # Should cover multiple compliance categories
        assert len(categories) >= 2

    @pytest.mark.asyncio
    async def test_audit_all_items_have_descriptions(self):
        auditor = ComplianceChecker()
        report = await auditor.run_audit()
        for item in report.items:
            assert item.description, f"Item {item.name} has no description"
            assert item.category, f"Item {item.name} has no category"

    @pytest.mark.asyncio
    async def test_audit_score_in_range(self):
        auditor = ComplianceChecker()
        report = await auditor.run_audit()
        assert 0.0 <= report.score_pct <= 100.0
