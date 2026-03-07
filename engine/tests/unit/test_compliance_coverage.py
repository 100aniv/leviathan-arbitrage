"""Coverage tests for src/infra/compliance.py.

Covers:
  - ComplianceReport.summary(), failures(), to_markdown()
  - ComplianceChecker._check_core_principles()
  - ComplianceChecker._check_kill_switch()
  - ComplianceChecker._check_wal()
  - ComplianceChecker._check_slippage_model()
  - ComplianceChecker._check_race_conditions()
  - ComplianceChecker.run_audit() (smoke test)
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.infra.compliance import (
    ComplianceChecker,
    ComplianceItem,
    ComplianceReport,
    ComplianceStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _item(status: ComplianceStatus, category: str = "test", name: str = "c") -> ComplianceItem:
    return ComplianceItem(category=category, name=name, status=status, description="")


def _report(*statuses: ComplianceStatus) -> ComplianceReport:
    return ComplianceReport(
        timestamp=datetime.now(timezone.utc),
        items=[_item(s) for s in statuses],
    )


# ---------------------------------------------------------------------------
# ComplianceReport.summary()
# ---------------------------------------------------------------------------


class TestComplianceReportSummary:
    def test_summary_contains_score_label(self):
        report = _report(ComplianceStatus.PASS, ComplianceStatus.FAIL)
        assert "Compliance Score:" in report.summary()

    def test_summary_shows_correct_pass_fraction(self):
        report = _report(ComplianceStatus.PASS, ComplianceStatus.PASS, ComplianceStatus.FAIL)
        assert "2/" in report.summary()

    def test_summary_100_percent_when_all_pass(self):
        report = _report(ComplianceStatus.PASS, ComplianceStatus.PASS)
        assert report.score_pct == 100.0
        assert "100.0%" in report.summary()

    def test_summary_excludes_skipped_from_denominator(self):
        # 1 PASS + 1 SKIPPED → score = 100% (skipped excluded)
        report = _report(ComplianceStatus.PASS, ComplianceStatus.SKIPPED)
        assert report.score_pct == 100.0

    def test_summary_zero_percent_when_all_skipped(self):
        report = _report(ComplianceStatus.SKIPPED, ComplianceStatus.SKIPPED)
        assert report.score_pct == 0.0
        assert isinstance(report.summary(), str)


# ---------------------------------------------------------------------------
# ComplianceReport.failures()
# ---------------------------------------------------------------------------


class TestComplianceReportFailures:
    def test_returns_only_fail_items(self):
        report = _report(
            ComplianceStatus.PASS,
            ComplianceStatus.FAIL,
            ComplianceStatus.PARTIAL,
            ComplianceStatus.FAIL,
        )
        failures = report.failures()
        assert len(failures) == 2
        assert all(f.status == ComplianceStatus.FAIL for f in failures)

    def test_returns_empty_when_all_pass(self):
        report = _report(ComplianceStatus.PASS, ComplianceStatus.PASS)
        assert report.failures() == []

    def test_does_not_include_partial_items(self):
        report = _report(ComplianceStatus.PARTIAL)
        assert report.failures() == []

    def test_does_not_include_skipped_items(self):
        report = _report(ComplianceStatus.SKIPPED)
        assert report.failures() == []


# ---------------------------------------------------------------------------
# ComplianceReport.to_markdown()
# ---------------------------------------------------------------------------


class TestComplianceReportToMarkdown:
    def test_produces_non_empty_string(self):
        report = _report(ComplianceStatus.PASS)
        md = report.to_markdown()
        assert isinstance(md, str) and len(md) > 0

    def test_includes_score_percentage(self):
        report = _report(ComplianceStatus.PASS, ComplianceStatus.FAIL)
        md = report.to_markdown()
        assert "%" in md

    def test_includes_failures_section_when_there_are_fails(self):
        report = _report(ComplianceStatus.FAIL)
        md = report.to_markdown()
        assert "Failures" in md

    def test_no_failures_section_when_all_pass(self):
        report = _report(ComplianceStatus.PASS, ComplianceStatus.PASS)
        assert "Failures" not in report.to_markdown()

    def test_includes_failures_section_for_partial_items(self):
        report = _report(ComplianceStatus.PARTIAL)
        assert "Failures" in report.to_markdown()

    def test_groups_items_by_category(self):
        items = [
            ComplianceItem(
                category="core_principle", name="c1",
                status=ComplianceStatus.PASS, description=""
            ),
            ComplianceItem(
                category="kill_switch", name="c2",
                status=ComplianceStatus.PASS, description=""
            ),
        ]
        report = ComplianceReport(timestamp=datetime.now(timezone.utc), items=items)
        md = report.to_markdown()
        assert "Core Principles" in md
        assert "Kill Switch" in md

    def test_includes_timestamp(self):
        report = _report(ComplianceStatus.PASS)
        md = report.to_markdown()
        # Timestamp appears in the header and footer
        assert "2026" in md or "Date" in md


# ---------------------------------------------------------------------------
# ComplianceChecker._check_core_principles()
# ---------------------------------------------------------------------------


class TestCheckCorePrinciples:
    def test_returns_exactly_five_items(self):
        checker = ComplianceChecker()
        with patch("src.infra.compliance._try_import", return_value=(False, None)):
            items = checker._check_core_principles()
        assert len(items) == 5

    def test_revenue_first_passes_when_two_collectors_importable(self):
        checker = ComplianceChecker()

        call_count = 0

        def mock_import(mod_path: str):
            nonlocal call_count
            call_count += 1
            if "collector" in mod_path:
                return True, MagicMock()
            return False, None

        with patch("src.infra.compliance._try_import", side_effect=mock_import):
            items = checker._check_core_principles()

        revenue = next(i for i in items if i.name == "Revenue-First")
        assert revenue.status == ComplianceStatus.PASS

    def test_revenue_first_fails_when_no_collectors_importable(self):
        checker = ComplianceChecker()
        with patch("src.infra.compliance._try_import", return_value=(False, None)):
            items = checker._check_core_principles()

        revenue = next(i for i in items if i.name == "Revenue-First")
        assert revenue.status == ComplianceStatus.FAIL

    def test_revenue_first_partial_when_one_collector_importable(self):
        checker = ComplianceChecker()
        collector_calls = 0

        def mock_import(mod_path: str):
            nonlocal collector_calls
            if "collector" in mod_path:
                collector_calls += 1
                # Only the first collector succeeds
                return (collector_calls == 1), MagicMock() if collector_calls == 1 else None
            return False, None

        with patch("src.infra.compliance._try_import", side_effect=mock_import):
            items = checker._check_core_principles()

        revenue = next(i for i in items if i.name == "Revenue-First")
        assert revenue.status in (ComplianceStatus.PARTIAL, ComplianceStatus.FAIL)

    def test_items_have_required_fields(self):
        checker = ComplianceChecker()
        with patch("src.infra.compliance._try_import", return_value=(False, None)):
            items = checker._check_core_principles()

        for item in items:
            assert item.category == "core_principle"
            assert item.name
            assert item.description
            assert item.status in list(ComplianceStatus)


# ---------------------------------------------------------------------------
# ComplianceChecker._check_kill_switch()
# ---------------------------------------------------------------------------


class TestCheckKillSwitch:
    @pytest.mark.asyncio
    async def test_returns_items_when_module_not_importable(self):
        checker = ComplianceChecker()
        with patch("src.infra.compliance._try_import", return_value=(False, None)):
            items = await checker._check_kill_switch()

        assert len(items) > 0
        for item in items:
            assert item.status in (ComplianceStatus.FAIL, ComplianceStatus.SKIPPED)

    @pytest.mark.asyncio
    async def test_tier1_latency_passes_with_fast_halt_local(self):
        checker = ComplianceChecker()

        mock_ks_mod = MagicMock()
        mock_ks_mod.halt_local = MagicMock()   # sync, no delay
        mock_ks_mod.clear_halt = MagicMock()
        mock_ks_mod.KillSwitchTarget = MagicMock()
        mock_ks_mod.is_halted = MagicMock(return_value=False)

        def mock_import(mod_path: str):
            if "kill_switch" in mod_path:
                return True, mock_ks_mod
            return False, None

        with patch("src.infra.compliance._try_import", side_effect=mock_import):
            items = await checker._check_kill_switch()

        tier1 = next(i for i in items if i.name == "Tier1-Latency")
        assert tier1.status == ComplianceStatus.PASS

    @pytest.mark.asyncio
    async def test_tier1_latency_skipped_when_halt_local_missing(self):
        checker = ComplianceChecker()

        mock_ks_mod = MagicMock(spec=[])  # no attributes

        def mock_import(mod_path: str):
            if "kill_switch" in mod_path:
                return True, mock_ks_mod
            return False, None

        with patch("src.infra.compliance._try_import", side_effect=mock_import):
            items = await checker._check_kill_switch()

        tier1 = next(i for i in items if i.name == "Tier1-Latency")
        assert tier1.status in (ComplianceStatus.SKIPPED, ComplianceStatus.FAIL)

    @pytest.mark.asyncio
    async def test_returns_multiple_checks(self):
        checker = ComplianceChecker()
        with patch("src.infra.compliance._try_import", return_value=(False, None)):
            items = await checker._check_kill_switch()

        # Should have at least Tier1, Tier2, Tier3 checks
        assert len(items) >= 3


# ---------------------------------------------------------------------------
# ComplianceChecker._check_wal()
# ---------------------------------------------------------------------------


class TestCheckWal:
    @pytest.mark.asyncio
    async def test_returns_two_items(self):
        checker = ComplianceChecker()
        with patch("src.infra.compliance._try_import", return_value=(False, None)):
            items = await checker._check_wal()
        assert len(items) == 2

    @pytest.mark.asyncio
    async def test_wal_module_passes_when_dual_write_importable_with_error(self):
        checker = ComplianceChecker()
        mock_mod = MagicMock()
        mock_mod.TradeRejectedError = ValueError

        with patch("src.infra.compliance._try_import", return_value=(True, mock_mod)):
            items = await checker._check_wal()

        wal_module = next(i for i in items if i.name == "WAL-Module")
        assert wal_module.status == ComplianceStatus.PASS

    @pytest.mark.asyncio
    async def test_wal_module_partial_when_no_trade_rejected_error(self):
        checker = ComplianceChecker()
        mock_mod = MagicMock(spec=[])  # no TradeRejectedError attribute

        with patch("src.infra.compliance._try_import", return_value=(True, mock_mod)):
            items = await checker._check_wal()

        wal_module = next(i for i in items if i.name == "WAL-Module")
        assert wal_module.status == ComplianceStatus.PARTIAL

    @pytest.mark.asyncio
    async def test_wal_module_fails_when_not_importable(self):
        checker = ComplianceChecker()
        with patch("src.infra.compliance._try_import", return_value=(False, None)):
            items = await checker._check_wal()

        wal_module = next(i for i in items if i.name == "WAL-Module")
        assert wal_module.status == ComplianceStatus.FAIL

    @pytest.mark.asyncio
    async def test_wal_health_skipped_when_no_db_pool(self):
        checker = ComplianceChecker(db_pool=None)
        mock_mod = MagicMock()
        mock_mod.TradeRejectedError = ValueError

        with patch("src.infra.compliance._try_import", return_value=(True, mock_mod)):
            items = await checker._check_wal()

        wal_health = next(i for i in items if i.name == "WAL-Health")
        assert wal_health.status == ComplianceStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_wal_health_passes_with_accessible_db(self):
        mock_pool = AsyncMock()
        mock_conn = AsyncMock()
        mock_pool.acquire = AsyncMock(return_value=mock_conn)
        mock_conn.fetchval = AsyncMock(return_value=42)
        mock_pool.release = AsyncMock()

        checker = ComplianceChecker(db_pool=mock_pool)
        mock_mod = MagicMock()
        mock_mod.TradeRejectedError = ValueError

        with patch("src.infra.compliance._try_import", return_value=(True, mock_mod)):
            items = await checker._check_wal()

        wal_health = next(i for i in items if i.name == "WAL-Health")
        assert wal_health.status == ComplianceStatus.PASS

    @pytest.mark.asyncio
    async def test_wal_health_partial_when_db_raises(self):
        mock_pool = AsyncMock()
        mock_pool.acquire = AsyncMock(side_effect=Exception("db connection failed"))

        checker = ComplianceChecker(db_pool=mock_pool)
        mock_mod = MagicMock()
        mock_mod.TradeRejectedError = ValueError

        with patch("src.infra.compliance._try_import", return_value=(True, mock_mod)):
            items = await checker._check_wal()

        wal_health = next(i for i in items if i.name == "WAL-Health")
        assert wal_health.status == ComplianceStatus.PARTIAL


# ---------------------------------------------------------------------------
# ComplianceChecker._check_slippage_model()
# ---------------------------------------------------------------------------


class TestCheckSlippageModel:
    def test_returns_two_items(self):
        checker = ComplianceChecker()
        with patch("src.infra.compliance._try_import_attr", return_value=(False, None)):
            items = checker._check_slippage_model()
        assert len(items) == 2

    def test_cex_slippage_passes_when_importable(self):
        checker = ComplianceChecker()
        mock_cls = MagicMock()
        mock_cls.GAMMA = 0.5
        mock_cls.GAMMA_CALIBRATED = True

        with patch("src.infra.compliance._try_import_attr", return_value=(True, mock_cls)):
            items = checker._check_slippage_model()

        cex_item = next(i for i in items if i.name == "CEX-Orderbook-Slippage")
        assert cex_item.status == ComplianceStatus.PASS

    def test_gamma_passes_when_calibrated(self):
        checker = ComplianceChecker()
        mock_cls = MagicMock()
        mock_cls.GAMMA = 0.5
        mock_cls.GAMMA_CALIBRATED = True

        with patch("src.infra.compliance._try_import_attr", return_value=(True, mock_cls)):
            items = checker._check_slippage_model()

        gamma_item = next(i for i in items if i.name == "Power-Law-Gamma")
        assert gamma_item.status == ComplianceStatus.PASS

    def test_gamma_partial_when_not_calibrated_and_no_env(self):
        checker = ComplianceChecker()
        mock_cls = MagicMock()
        mock_cls.GAMMA = 0.5
        mock_cls.GAMMA_CALIBRATED = False

        with patch("src.infra.compliance._try_import_attr", return_value=(True, mock_cls)):
            with patch("os.getenv", return_value=None):
                items = checker._check_slippage_model()

        gamma_item = next(i for i in items if i.name == "Power-Law-Gamma")
        assert gamma_item.status == ComplianceStatus.PARTIAL

    def test_both_fail_and_skipped_when_cls_not_importable(self):
        checker = ComplianceChecker()
        with patch("src.infra.compliance._try_import_attr", return_value=(False, None)):
            items = checker._check_slippage_model()

        cex_item = next(i for i in items if i.name == "CEX-Orderbook-Slippage")
        gamma_item = next(i for i in items if i.name == "Power-Law-Gamma")
        assert cex_item.status == ComplianceStatus.FAIL
        assert gamma_item.status == ComplianceStatus.SKIPPED

    def test_gamma_fails_when_class_has_no_gamma_attribute(self):
        checker = ComplianceChecker()
        mock_cls = MagicMock(spec=[])  # no GAMMA attribute

        with patch("src.infra.compliance._try_import_attr", return_value=(True, mock_cls)):
            items = checker._check_slippage_model()

        gamma_item = next(i for i in items if i.name == "Power-Law-Gamma")
        assert gamma_item.status == ComplianceStatus.FAIL


# ---------------------------------------------------------------------------
# ComplianceChecker._check_race_conditions()
# ---------------------------------------------------------------------------


class TestCheckRaceConditions:
    def test_returns_non_empty_list(self):
        checker = ComplianceChecker()
        with patch("src.infra.compliance._try_import", return_value=(False, None)):
            items = checker._check_race_conditions()
        assert isinstance(items, list)
        assert len(items) > 0

    def test_all_items_have_race_condition_category_or_valid_category(self):
        checker = ComplianceChecker()
        with patch("src.infra.compliance._try_import", return_value=(False, None)):
            items = checker._check_race_conditions()
        for item in items:
            assert item.category  # non-empty category

    def test_kill_switch_lock_skipped_or_fail_when_module_not_importable(self):
        checker = ComplianceChecker()
        with patch("src.infra.compliance._try_import", return_value=(False, None)):
            items = checker._check_race_conditions()

        lock_items = [i for i in items if i.name == "KillSwitch-Lock"]
        assert len(lock_items) >= 1
        assert lock_items[0].status in (ComplianceStatus.SKIPPED, ComplianceStatus.FAIL)

    def test_kill_switch_lock_passes_with_runtime_lock(self):
        checker = ComplianceChecker()

        mock_ks_cls = MagicMock()
        mock_ks_mod = MagicMock()
        mock_ks_mod.KillSwitch = mock_ks_cls

        import inspect
        # Provide source that includes asyncio.Lock() and _lock
        fake_src = "self._lock = asyncio.Lock()"

        def mock_import(mod_path: str):
            if "kill_switch" in mod_path:
                return True, mock_ks_mod
            return False, None

        with patch("src.infra.compliance._try_import", side_effect=mock_import):
            with patch("inspect.getsource", return_value=fake_src):
                items = checker._check_race_conditions()

        lock_items = [i for i in items if i.name == "KillSwitch-Lock"]
        assert lock_items[0].status == ComplianceStatus.PASS


# ---------------------------------------------------------------------------
# ComplianceChecker.run_audit() — smoke test
# ---------------------------------------------------------------------------


class TestRunAudit:
    @pytest.mark.asyncio
    async def test_returns_compliance_report(self):
        checker = ComplianceChecker()

        with patch.object(checker, "_check_core_principles", return_value=[]):
            with patch.object(checker, "_check_kill_switch", new=AsyncMock(return_value=[])):
                with patch.object(checker, "_check_wal", new=AsyncMock(return_value=[])):
                    with patch.object(checker, "_check_slippage_model", return_value=[]):
                        with patch.object(checker, "_check_race_conditions", return_value=[]):
                            with patch.object(checker, "_check_observability", return_value=[]):
                                with patch.object(
                                    checker, "_check_data_integrity",
                                    new=AsyncMock(return_value=[]),
                                ):
                                    report = await checker.run_audit()

        assert isinstance(report, ComplianceReport)

    @pytest.mark.asyncio
    async def test_aggregates_items_from_all_checks(self):
        checker = ComplianceChecker()
        item_a = _item(ComplianceStatus.PASS, name="a")
        item_b = _item(ComplianceStatus.FAIL, name="b")

        with patch.object(checker, "_check_core_principles", return_value=[item_a]):
            with patch.object(checker, "_check_kill_switch", new=AsyncMock(return_value=[item_b])):
                with patch.object(checker, "_check_wal", new=AsyncMock(return_value=[])):
                    with patch.object(checker, "_check_slippage_model", return_value=[]):
                        with patch.object(checker, "_check_race_conditions", return_value=[]):
                            with patch.object(checker, "_check_observability", return_value=[]):
                                with patch.object(
                                    checker, "_check_data_integrity",
                                    new=AsyncMock(return_value=[]),
                                ):
                                    report = await checker.run_audit()

        assert report.pass_count == 1
        assert report.fail_count == 1
        assert report.total_count == 2

    @pytest.mark.asyncio
    async def test_report_has_timestamp(self):
        checker = ComplianceChecker()

        with patch.object(checker, "_check_core_principles", return_value=[]):
            with patch.object(checker, "_check_kill_switch", new=AsyncMock(return_value=[])):
                with patch.object(checker, "_check_wal", new=AsyncMock(return_value=[])):
                    with patch.object(checker, "_check_slippage_model", return_value=[]):
                        with patch.object(checker, "_check_race_conditions", return_value=[]):
                            with patch.object(checker, "_check_observability", return_value=[]):
                                with patch.object(
                                    checker, "_check_data_integrity",
                                    new=AsyncMock(return_value=[]),
                                ):
                                    report = await checker.run_audit()

        assert report.timestamp is not None
        assert report.timestamp.tzinfo is not None  # timezone-aware
