"""Unit tests for NotionReporter (US-374).

Tests cover:
- Class / method existence
- no-op behaviour when NOTION_TOKEN is absent
- Content builder output
- Dataclass field contracts
- API helpers are NOT called when disabled
"""
from __future__ import annotations

import os
from dataclasses import fields
from unittest.mock import MagicMock, patch

import pytest

from src.infra.notion_reporter import (
    LiveTradeRecord,
    NotionReporter,
    TestCaseResult,
)


# ---------------------------------------------------------------------------
# 1. Class existence
# ---------------------------------------------------------------------------

def test_notion_reporter_class_exists():
    reporter = NotionReporter()
    assert isinstance(reporter, NotionReporter)


# ---------------------------------------------------------------------------
# 2. Method existence and callability
# ---------------------------------------------------------------------------

def test_write_plan_method_exists():
    reporter = NotionReporter()
    assert callable(getattr(reporter, "write_plan", None))


def test_update_test_progress_method_exists():
    reporter = NotionReporter()
    assert callable(getattr(reporter, "update_test_progress", None))


def test_create_final_report_method_exists():
    reporter = NotionReporter()
    assert callable(getattr(reporter, "create_final_report", None))


# ---------------------------------------------------------------------------
# 5-7. no-op behaviour — NOTION_TOKEN absent
# ---------------------------------------------------------------------------

def test_write_plan_returns_none_without_token(monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    reporter = NotionReporter(notion_token=None)
    result = reporter.write_plan("Phase K", [])
    assert result is None


def test_update_test_progress_returns_false_without_token(monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    reporter = NotionReporter(notion_token=None)
    result = reporter.update_test_progress("K-B-01", "PASS")
    assert result is False


def test_create_final_report_returns_none_without_token(monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    reporter = NotionReporter(notion_token=None)
    result = reporter.create_final_report("Phase K", [], [])
    assert result is None


# ---------------------------------------------------------------------------
# 8. _build_plan_content — table header included
# ---------------------------------------------------------------------------

def test_build_plan_content_has_table_header():
    reporter = NotionReporter()
    test_cases = [
        {"id": "K-B-01", "strategy": "cross_exchange", "exchange": "binance",
         "period": "7d", "seed_usd": 10000, "expected": "PASS"},
    ]
    content = reporter._build_plan_content("Phase K", test_cases)
    assert "| ID | 전략 | 거래소 | 기간 | 시드(USD) | 예상 결과 |" in content
    assert "K-B-01" in content
    assert "cross_exchange" in content


# ---------------------------------------------------------------------------
# 9. _build_final_report_content — two sections present
# ---------------------------------------------------------------------------

def test_build_final_report_content_has_two_sections():
    reporter = NotionReporter()
    bt = [
        TestCaseResult(
            case_id="K-B-01",
            strategy="cross_exchange",
            exchange="binance",
            period="7d",
            seed_usd=10000.0,
            sharpe=1.5,
            mdd_pct=3.2,
            win_rate=0.6,
            pnl_usd=250.0,
            status="PASS",
        )
    ]
    lt = [
        LiveTradeRecord(
            exchange="binance",
            strategy="cross_exchange",
            fill_price=42000.1234,
            mdd_pct=1.5,
        )
    ]
    content = reporter._build_final_report_content(bt, lt)
    assert "## 백테스트 결과" in content
    assert "## 라이브 체결 기록" in content
    assert "K-B-01" in content
    assert "42000.1234" in content


# ---------------------------------------------------------------------------
# 10. TestCaseResult dataclass fields
# ---------------------------------------------------------------------------

def test_test_case_result_fields():
    expected = {
        "case_id", "strategy", "exchange", "period",
        "seed_usd", "sharpe", "mdd_pct", "win_rate", "pnl_usd", "status",
    }
    actual = {f.name for f in fields(TestCaseResult)}
    assert expected == actual


# ---------------------------------------------------------------------------
# 11. LiveTradeRecord dataclass fields
# ---------------------------------------------------------------------------

def test_live_trade_record_fields():
    expected = {"exchange", "strategy", "fill_price", "mdd_pct", "timestamp"}
    actual = {f.name for f in fields(LiveTradeRecord)}
    assert expected == actual


def test_live_trade_record_timestamp_default():
    record = LiveTradeRecord(
        exchange="upbit", strategy="triangular", fill_price=100.0, mdd_pct=0.5
    )
    # timestamp should be auto-populated as an ISO string
    assert record.timestamp
    assert "T" in record.timestamp  # ISO 8601 format


# ---------------------------------------------------------------------------
# 12. _create_page not called when disabled
# ---------------------------------------------------------------------------

def test_create_page_not_called_when_disabled(monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    reporter = NotionReporter(notion_token=None)
    assert not reporter._enabled

    mock_create = MagicMock(return_value=None)
    reporter._create_page = mock_create

    reporter.write_plan("Phase K", [])
    reporter.create_final_report("Phase K", [], [])

    mock_create.assert_not_called()
