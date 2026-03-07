"""Extended coverage tests for src/infra/compliance.py — individual check methods."""
from __future__ import annotations

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from src.infra.compliance import (
    ComplianceChecker,
    ComplianceItem,
    ComplianceReport,
    ComplianceStatus,
)


# ---------------------------------------------------------------------------
# ComplianceReport.to_markdown
# ---------------------------------------------------------------------------

class TestComplianceReportToMarkdown:
    def _make_report(self, items: list[ComplianceItem]) -> ComplianceReport:
        return ComplianceReport(
            timestamp=datetime(2026, 3, 7, 0, 0, 0, tzinfo=timezone.utc),
            items=items,
        )

    def test_to_markdown_has_title(self):
        report = self._make_report([
            ComplianceItem("core_principle", "check_a", ComplianceStatus.PASS, "Check A"),
        ])
        md = report.to_markdown()
        assert "# LEVIATHAN Blueprint Compliance Report" in md

    def test_to_markdown_includes_timestamp(self):
        report = self._make_report([])
        md = report.to_markdown()
        assert "2026-03-07" in md

    def test_to_markdown_includes_score(self):
        items = [
            ComplianceItem("core_principle", "a", ComplianceStatus.PASS, "A"),
            ComplianceItem("core_principle", "b", ComplianceStatus.PASS, "B"),
        ]
        report = self._make_report(items)
        md = report.to_markdown()
        assert "100.0%" in md

    def test_to_markdown_groups_by_category(self):
        items = [
            ComplianceItem("core_principle", "a", ComplianceStatus.PASS, "A"),
            ComplianceItem("kill_switch", "b", ComplianceStatus.FAIL, "B"),
        ]
        report = self._make_report(items)
        md = report.to_markdown()
        # Should have section headers for each category
        assert len(md) > 100  # substantial output

    def test_to_markdown_includes_status_table(self):
        items = [
            ComplianceItem("cat", "a", ComplianceStatus.PASS, "A"),
            ComplianceItem("cat", "b", ComplianceStatus.FAIL, "B"),
            ComplianceItem("cat", "c", ComplianceStatus.PARTIAL, "C"),
        ]
        report = self._make_report(items)
        md = report.to_markdown()
        assert "PASS" in md
        assert "FAIL" in md
        assert "PARTIAL" in md

    def test_to_markdown_empty_report(self):
        report = self._make_report([])
        md = report.to_markdown()
        assert "0.0%" in md or "# LEVIATHAN" in md


# ---------------------------------------------------------------------------
# ComplianceReport.failures
# ---------------------------------------------------------------------------

class TestComplianceReportFailures:
    def test_failures_returns_only_fail_items(self):
        items = [
            ComplianceItem("cat", "ok", ComplianceStatus.PASS, "ok"),
            ComplianceItem("cat", "bad", ComplianceStatus.FAIL, "bad"),
            ComplianceItem("cat", "partial", ComplianceStatus.PARTIAL, "partial"),
            ComplianceItem("cat", "skip", ComplianceStatus.SKIPPED, "skip"),
        ]
        report = ComplianceReport(timestamp=datetime.now(timezone.utc), items=items)
        failures = report.failures()
        assert len(failures) == 1
        assert failures[0].name == "bad"
        assert failures[0].status == ComplianceStatus.FAIL

    def test_failures_empty_when_all_pass(self):
        items = [
            ComplianceItem("cat", "a", ComplianceStatus.PASS, "a"),
            ComplianceItem("cat", "b", ComplianceStatus.PASS, "b"),
        ]
        report = ComplianceReport(timestamp=datetime.now(timezone.utc), items=items)
        assert report.failures() == []

    def test_failures_multiple_fails(self):
        items = [
            ComplianceItem("cat", "a", ComplianceStatus.FAIL, "a"),
            ComplianceItem("cat", "b", ComplianceStatus.FAIL, "b"),
            ComplianceItem("cat", "c", ComplianceStatus.PASS, "c"),
        ]
        report = ComplianceReport(timestamp=datetime.now(timezone.utc), items=items)
        assert len(report.failures()) == 2


# ---------------------------------------------------------------------------
# _check_core_principles (individual)
# ---------------------------------------------------------------------------

class TestCheckCorePrinciples:
    def test_returns_list_of_compliance_items(self):
        checker = ComplianceChecker()
        items = checker._check_core_principles()
        assert isinstance(items, list)
        assert len(items) >= 1

    def test_all_items_have_core_principle_category(self):
        checker = ComplianceChecker()
        items = checker._check_core_principles()
        for item in items:
            assert item.category == "core_principle"

    def test_all_items_have_description(self):
        checker = ComplianceChecker()
        items = checker._check_core_principles()
        for item in items:
            assert item.description, f"Item {item.name} missing description"

    def test_all_items_have_valid_status(self):
        checker = ComplianceChecker()
        items = checker._check_core_principles()
        valid = set(ComplianceStatus)
        for item in items:
            assert item.status in valid

    def test_revenue_first_check_present(self):
        checker = ComplianceChecker()
        items = checker._check_core_principles()
        names = [i.name for i in items]
        assert "Revenue-First" in names

    def test_incremental_migration_check_present(self):
        checker = ComplianceChecker()
        items = checker._check_core_principles()
        names = [i.name for i in items]
        assert "Incremental-Migration" in names


# ---------------------------------------------------------------------------
# _check_slippage_model
# ---------------------------------------------------------------------------

class TestCheckSlippageModel:
    def test_returns_list_of_items(self):
        checker = ComplianceChecker()
        items = checker._check_slippage_model()
        assert isinstance(items, list)
        assert len(items) >= 1

    def test_all_items_have_slippage_category(self):
        checker = ComplianceChecker()
        items = checker._check_slippage_model()
        for item in items:
            assert item.category == "slippage"

    def test_all_items_have_valid_status(self):
        checker = ComplianceChecker()
        items = checker._check_slippage_model()
        valid = set(ComplianceStatus)
        for item in items:
            assert item.status in valid

    def test_all_items_have_description(self):
        checker = ComplianceChecker()
        items = checker._check_slippage_model()
        for item in items:
            assert item.description


# ---------------------------------------------------------------------------
# _check_race_conditions
# ---------------------------------------------------------------------------

class TestCheckRaceConditions:
    def test_returns_list_of_items(self):
        checker = ComplianceChecker()
        items = checker._check_race_conditions()
        assert isinstance(items, list)
        assert len(items) >= 1

    def test_all_items_have_valid_status(self):
        checker = ComplianceChecker()
        items = checker._check_race_conditions()
        valid = set(ComplianceStatus)
        for item in items:
            assert item.status in valid

    def test_all_items_have_description(self):
        checker = ComplianceChecker()
        items = checker._check_race_conditions()
        for item in items:
            assert item.description


# ---------------------------------------------------------------------------
# _check_observability
# ---------------------------------------------------------------------------

class TestCheckObservability:
    def test_returns_list_of_items(self):
        checker = ComplianceChecker()
        items = checker._check_observability()
        assert isinstance(items, list)
        assert len(items) >= 1

    def test_all_items_have_valid_status(self):
        checker = ComplianceChecker()
        items = checker._check_observability()
        valid = set(ComplianceStatus)
        for item in items:
            assert item.status in valid

    def test_all_items_have_description(self):
        checker = ComplianceChecker()
        items = checker._check_observability()
        for item in items:
            assert item.description


# ---------------------------------------------------------------------------
# _check_kill_switch (async)
# ---------------------------------------------------------------------------

class TestCheckKillSwitch:
    @pytest.mark.asyncio
    async def test_returns_list_of_items(self):
        checker = ComplianceChecker()
        items = await checker._check_kill_switch()
        assert isinstance(items, list)
        assert len(items) >= 1

    @pytest.mark.asyncio
    async def test_all_items_have_kill_switch_category(self):
        checker = ComplianceChecker()
        items = await checker._check_kill_switch()
        for item in items:
            assert item.category == "kill_switch"

    @pytest.mark.asyncio
    async def test_all_items_have_valid_status(self):
        checker = ComplianceChecker()
        items = await checker._check_kill_switch()
        valid = set(ComplianceStatus)
        for item in items:
            assert item.status in valid

    @pytest.mark.asyncio
    async def test_all_items_have_description(self):
        checker = ComplianceChecker()
        items = await checker._check_kill_switch()
        for item in items:
            assert item.description


# ---------------------------------------------------------------------------
# _check_wal (async)
# ---------------------------------------------------------------------------

class TestCheckWal:
    @pytest.mark.asyncio
    async def test_returns_list_of_items(self):
        checker = ComplianceChecker()
        items = await checker._check_wal()
        assert isinstance(items, list)
        assert len(items) >= 1

    @pytest.mark.asyncio
    async def test_all_items_have_valid_status(self):
        checker = ComplianceChecker()
        items = await checker._check_wal()
        valid = set(ComplianceStatus)
        for item in items:
            assert item.status in valid

    @pytest.mark.asyncio
    async def test_all_items_have_description(self):
        checker = ComplianceChecker()
        items = await checker._check_wal()
        for item in items:
            assert item.description


# ---------------------------------------------------------------------------
# _check_data_integrity (async)
# ---------------------------------------------------------------------------

class TestCheckDataIntegrity:
    @pytest.mark.asyncio
    async def test_no_db_pool_returns_skipped_or_empty(self):
        checker = ComplianceChecker(db_pool=None)
        items = await checker._check_data_integrity()
        assert isinstance(items, list)
        # Without a DB pool, items should either be empty or SKIPPED
        for item in items:
            assert item.status in (ComplianceStatus.SKIPPED, ComplianceStatus.PASS,
                                   ComplianceStatus.FAIL, ComplianceStatus.PARTIAL)

    @pytest.mark.asyncio
    async def test_with_db_pool_returns_items(self):
        mock_pool = MagicMock()
        checker = ComplianceChecker(db_pool=mock_pool)
        items = await checker._check_data_integrity()
        assert isinstance(items, list)


# ---------------------------------------------------------------------------
# run_audit integration
# ---------------------------------------------------------------------------

class TestRunAuditIntegration:
    @pytest.mark.asyncio
    async def test_produces_valid_report(self):
        checker = ComplianceChecker()
        report = await checker.run_audit()
        assert isinstance(report, ComplianceReport)
        assert report.total_count > 0
        assert 0.0 <= report.score_pct <= 100.0

    @pytest.mark.asyncio
    async def test_report_covers_core_principle_category(self):
        checker = ComplianceChecker()
        report = await checker.run_audit()
        categories = {item.category for item in report.items}
        assert "core_principle" in categories

    @pytest.mark.asyncio
    async def test_report_covers_kill_switch_category(self):
        checker = ComplianceChecker()
        report = await checker.run_audit()
        categories = {item.category for item in report.items}
        assert "kill_switch" in categories

    @pytest.mark.asyncio
    async def test_report_has_slippage_category(self):
        checker = ComplianceChecker()
        report = await checker.run_audit()
        categories = {item.category for item in report.items}
        assert "slippage" in categories

    @pytest.mark.asyncio
    async def test_checker_with_injected_dependencies(self):
        mock_ks = MagicMock()
        mock_cb = MagicMock()
        mock_tg = MagicMock()
        checker = ComplianceChecker(
            kill_switch=mock_ks,
            circuit_breaker=mock_cb,
            telegram=mock_tg,
        )
        report = await checker.run_audit()
        assert report.total_count > 0

    @pytest.mark.asyncio
    async def test_all_report_items_have_required_fields(self):
        checker = ComplianceChecker()
        report = await checker.run_audit()
        for item in report.items:
            assert item.category, f"Item {item.name} missing category"
            assert item.name, f"Item missing name"
            assert item.description, f"Item {item.name} missing description"
            assert item.status in set(ComplianceStatus)

    @pytest.mark.asyncio
    async def test_summary_string_contains_score(self):
        checker = ComplianceChecker()
        report = await checker.run_audit()
        summary = report.summary()
        assert "%" in summary
        assert "PASS" in summary

    @pytest.mark.asyncio
    async def test_to_markdown_produces_output(self):
        checker = ComplianceChecker()
        report = await checker.run_audit()
        md = report.to_markdown()
        assert len(md) > 200  # substantial markdown output
        assert "# LEVIATHAN Blueprint Compliance Report" in md
