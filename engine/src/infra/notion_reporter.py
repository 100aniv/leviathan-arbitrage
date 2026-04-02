"""Notion Reporter — Phase K 테스트 결과 실시간 공유.

Notion MCP 또는 REST API fallback으로 Phase K 진행상황을 공유.
NOTION_TOKEN 환경변수 설정 시 REST API로 직접 페이지 생성/업데이트.
미설정 시 no-op (실패하지 않음).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_API_VERSION = "2022-06-28"


@dataclass
class TestCaseResult:
    case_id: str           # e.g. "K-B-01"
    strategy: str
    exchange: str
    period: str
    seed_usd: float
    sharpe: float
    mdd_pct: float
    win_rate: float
    pnl_usd: float
    status: str            # "PASS" | "FAIL" | "EXPECTED_FAIL" | "PENDING"


@dataclass
class LiveTradeRecord:
    exchange: str
    strategy: str
    fill_price: float
    mdd_pct: float
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class NotionReporter:
    """Notion Phase K 테스트 결과 공유 리포터.

    Notion MCP (claude.ai 통합) 또는 REST API fallback 사용.
    NOTION_TOKEN 미설정 시 no-op 모드로 동작 (예외 없음).
    """

    def __init__(
        self,
        parent_page_id: str = "cba952f3183c40168012e9f1afc8b6f6",
        notion_token: str | None = None,
    ) -> None:
        self._parent_page_id = parent_page_id
        self._token = notion_token or os.environ.get("NOTION_TOKEN", "")
        self._plan_page_id: str | None = None
        self._enabled = bool(self._token)

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def write_plan(self, phase: str, test_cases: list[dict[str, Any]]) -> str | None:
        """Phase K 플랜 페이지 생성 (23케이스 매트릭스 표 포함).

        Returns: created page_id or None on error/disabled.
        """
        if not self._enabled:
            logger.info("notion_reporter.write_plan: disabled (no NOTION_TOKEN)")
            return None

        title = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d')}] {phase} 플랜"
        content = self._build_plan_content(phase, test_cases)

        page_id = self._create_page(title, content)
        if page_id:
            self._plan_page_id = page_id
            logger.info("notion_reporter.write_plan: created page_id=%s", page_id)
        return page_id

    def update_test_progress(
        self, case_id: str, status: str, details: dict[str, Any] | None = None
    ) -> bool:
        """테스트 케이스 완료 시 Notion 체크리스트 실시간 업데이트.

        Args:
            case_id: e.g. "K-B-01"
            status: "PASS" | "FAIL" | "EXPECTED_FAIL"
            details: optional metrics dict

        Returns: True on success, False on error/disabled.
        """
        if not self._enabled or not self._plan_page_id:
            logger.info("notion_reporter.update_test_progress: disabled or no plan page")
            return False

        mark = "✅" if status == "PASS" else ("⚠️" if status == "EXPECTED_FAIL" else "❌")
        update_text = f"{mark} {case_id}: {status}"
        if details:
            metrics = ", ".join(f"{k}={v}" for k, v in details.items())
            update_text += f" ({metrics})"

        return self._append_to_page(self._plan_page_id, update_text)

    def create_final_report(
        self,
        phase: str,
        backtest_results: list[TestCaseResult],
        live_trades: list[LiveTradeRecord],
    ) -> str | None:
        """최종 리포트 페이지 생성 (백테스트 결과 표 + 라이브 체결 기록).

        Returns: created page_id or None on error/disabled.
        """
        if not self._enabled:
            logger.info("notion_reporter.create_final_report: disabled")
            return None

        title = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d')}] {phase} Final Report"
        content = self._build_final_report_content(backtest_results, live_trades)

        page_id = self._create_page(title, content)
        if page_id:
            logger.info("notion_reporter.create_final_report: created page_id=%s", page_id)
        return page_id

    # -----------------------------------------------------------------------
    # Content builders
    # -----------------------------------------------------------------------

    def _build_plan_content(self, phase: str, test_cases: list[dict]) -> str:
        """Build plan page content with test case matrix table."""
        lines = [
            f"# {phase} 플랜\n",
            "## 테스트 케이스 매트릭스\n",
            "| ID | 전략 | 거래소 | 기간 | 시드(USD) | 예상 결과 |",
            "|---|---|---|---|---|---|",
        ]
        for tc in test_cases:
            lines.append(
                f"| {tc.get('id', '')} | {tc.get('strategy', '')} | "
                f"{tc.get('exchange', '')} | {tc.get('period', '')} | "
                f"{tc.get('seed_usd', '')} | {tc.get('expected', 'PASS')} |"
            )
        lines.append("")
        return "\n".join(lines)

    def _build_final_report_content(
        self,
        backtest_results: list[TestCaseResult],
        live_trades: list[LiveTradeRecord],
    ) -> str:
        """Build final report with backtest table + live trades table."""
        lines = [
            "# Phase K Final Report\n",
            "## 백테스트 결과\n",
            "| ID | 전략 | 거래소 | 기간 | 시드 | Sharpe | MDD% | WR% | PnL($) | 결과 |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for r in backtest_results:
            lines.append(
                f"| {r.case_id} | {r.strategy} | {r.exchange} | {r.period} | "
                f"${r.seed_usd:.0f} | {r.sharpe:.2f} | {r.mdd_pct:.1f}% | "
                f"{r.win_rate*100:.1f}% | ${r.pnl_usd:.2f} | {r.status} |"
            )

        lines.extend([
            "",
            "## 라이브 체결 기록\n",
            "| 거래소 | 전략 | 체결가 | MDD% | 시각 |",
            "|---|---|---|---|---|",
        ])
        for t in live_trades:
            lines.append(
                f"| {t.exchange} | {t.strategy} | {t.fill_price:.4f} | "
                f"{t.mdd_pct:.2f}% | {t.timestamp} |"
            )

        return "\n".join(lines)

    # -----------------------------------------------------------------------
    # REST API helpers
    # -----------------------------------------------------------------------

    def _create_page(self, title: str, content: str) -> str | None:
        """Create a Notion page under parent_page_id via REST API."""
        try:
            import urllib.request
            payload = json.dumps({
                "parent": {"page_id": self._parent_page_id},
                "properties": {
                    "title": [{"type": "text", "text": {"content": title}}]
                },
                "children": [
                    {
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [{"type": "text", "text": {"content": content}}]
                        }
                    }
                ]
            }).encode()
            req = urllib.request.Request(
                f"{NOTION_API_BASE}/pages",
                data=payload,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Notion-Version": NOTION_API_VERSION,
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                return data.get("id")
        except Exception as exc:
            logger.warning("notion_reporter._create_page error: %s", exc)
            return None

    def _append_to_page(self, page_id: str, text: str) -> bool:
        """Append a paragraph block to an existing Notion page."""
        try:
            import urllib.request
            payload = json.dumps({
                "children": [
                    {
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [{"type": "text", "text": {"content": text}}]
                        }
                    }
                ]
            }).encode()
            req = urllib.request.Request(
                f"{NOTION_API_BASE}/blocks/{page_id}/children",
                data=payload,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Notion-Version": NOTION_API_VERSION,
                    "Content-Type": "application/json",
                },
                method="PATCH",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 200
        except Exception as exc:
            logger.warning("notion_reporter._append_to_page error: %s", exc)
            return False
