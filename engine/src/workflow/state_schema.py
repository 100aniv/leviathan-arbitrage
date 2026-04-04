"""LEVIATHAN 워크플로우 상태 스키마 및 헬퍼 함수.

실제 파일 형식에 맞춘 TypedDict 정의:
  - .omc/prd.json          (사용자 스토리)
  - .omc/state/leviathan-current-stage.json
  - .omc/state/leviathan-active-phase.json
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from dataclasses import dataclass, field
from typing import Any, Optional

from typing import TypedDict

# ---------------------------------------------------------------------------
# 레포 루트 경로 해석
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]  # engine/src/workflow/ → 레포 루트
_OMC_DIR = _REPO_ROOT / ".omc"


# ---------------------------------------------------------------------------
# PRD TypedDict 정의
# ---------------------------------------------------------------------------


class PRDUserStory(TypedDict, total=False):
    """prd.json의 'stories' 배열 항목 하나에 대응."""

    id: str                        # "US-001"
    title: str
    phase: str                     # "A", "B-1", "S13" 등
    priority: int | str            # 레거시 정수 순위 또는 "CRITICAL"/"HIGH"/"MEDIUM"/"LOW"
    passes: bool
    acceptanceCriteria: list[str]


class PRDDocument(TypedDict, total=False):
    """prd.json 최상위 구조."""

    project: str
    version: str
    created: str
    updated: str
    plan_ref: str
    total_stories: int
    phases: dict[str, str]
    gap_dependency_order: str
    stories: list[PRDUserStory]


# ---------------------------------------------------------------------------
# Stage 상세 TypedDict 정의
# ---------------------------------------------------------------------------


class StageADetail(TypedDict, total=False):
    """Stage A (기획) 상세 블록."""

    status: Optional[str]          # pending | in_progress | completed | failed
    started_at: Optional[str]
    completed_at: Optional[str]
    entry_gate: Optional[str | bool]
    plan_file: Optional[str]
    quant_gate: Optional[str | bool]


class StageBDetail(TypedDict, total=False):
    """Stage B (구현 + 검증) 상세 블록."""

    status: Optional[str]
    started_at: Optional[str]
    completed_at: Optional[str]
    phase1_impl: Optional[str | bool]
    pytest_pass: Optional[bool]
    phase2_shadow: Optional[str | bool]
    shadow_duration_min: Optional[float]
    shadow_pnl: Optional[float]
    shadow_wr: Optional[float]
    shadow_crash: Optional[int]


class StageCDetail(TypedDict, total=False):
    """Stage C (리뷰 + 릴리스) 상세 블록."""

    status: Optional[str]
    started_at: Optional[str]
    completed_at: Optional[str]
    code_review: Optional[str | bool]
    security_review: Optional[str | bool]
    phase_review: Optional[str | bool]
    go_no_go: Optional[str | bool]
    ssot_updated: Optional[bool]
    git_pushed: Optional[bool]
    telegram_notified: Optional[bool]
    boss_approved: Optional[bool]


class StageDetail(TypedDict, total=False):
    """A/B/C 스테이지 상세 컨테이너."""

    A: StageADetail
    B: StageBDetail
    C: StageCDetail


# ---------------------------------------------------------------------------
# LEVIATHAN 상태 TypedDict 정의
# ---------------------------------------------------------------------------


class LeviathanState(TypedDict, total=False):
    """leviathan-current-stage.json에 대응."""

    schema: str                    # '$schema' 키에 매핑
    phase: str                     # "S13"
    stage: str                     # "A" | "B" | "C" | "pending"
    stage_detail: StageDetail
    escalation_level: str          # "L0" … "L5"
    updated_at: str                # ISO 8601
    updated_by: str
    # US 단위 사이클 필드 (Phase K 재설계 v5 — 2026-04-04)
    current_us: Optional[str]      # 현재 실행 중인 US ID (예: "US-387")
    us_queue: Optional[list[str]]  # 남은 US 목록 (순서 보장)


class TestMetrics(TypedDict, total=False):
    passed: int
    failed: int
    skipped: int
    coverage_pct: int


class ComplianceMetrics(TypedDict, total=False):
    total: int
    passed: int
    pct: int


class ModeInfo(TypedDict, total=False):
    data_mode: str
    execution_mode: str


class PRDSummary(TypedDict, total=False):
    total_stories: int
    passed: int
    pending: int
    pending_detail: str


class CollectorInfo(TypedDict, total=False):
    active: int
    total: int
    list: list[str]


class InfraInfo(TypedDict, total=False):
    docker_services: int
    healthy: str
    components: list[str]


class LeviathanActivePhase(TypedDict, total=False):
    """leviathan-active-phase.json에 대응."""

    schema: str
    current_phase: str
    current_phase_title: str
    current_phase_us_range: str
    current_phase_status: str
    current_phase_us_total: int
    current_phase_us_done: int
    tests: TestMetrics
    compliance: ComplianceMetrics
    mode: ModeInfo
    prd: PRDSummary
    latest_commit: str
    pipeline: list[str]
    completed_phases: list[str]
    collectors: CollectorInfo
    infra: InfraInfo
    updated_at: str
    updated_by: str


# ---------------------------------------------------------------------------
# 일관성 리포트 데이터클래스
# ---------------------------------------------------------------------------


@dataclass
class ConsistencyReport:
    """일관성 검사 실행 결과."""

    passed: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checked_files: list[str] = field(default_factory=list)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.passed = False

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def summary(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return (
            f"ConsistencyReport [{status}] "
            f"errors={len(self.errors)} warnings={len(self.warnings)} "
            f"files_checked={len(self.checked_files)}"
        )


# ---------------------------------------------------------------------------
# 헬퍼 함수
# ---------------------------------------------------------------------------


def hash_file(path: pathlib.Path) -> str:
    """파일 내용의 SHA-256 해시를 반환."""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def load_prd(prd_path: pathlib.Path | None = None) -> PRDDocument:
    """prd.json 문서를 로드하여 반환.

    Args:
        prd_path: 명시적 경로 오버라이드. 기본값은 레포 루트 기준 .omc/prd.json.

    Returns:
        파싱된 PRDDocument 딕셔너리.

    Raises:
        FileNotFoundError: prd.json 파일이 존재하지 않을 때.
        json.JSONDecodeError: 파일이 유효한 JSON이 아닐 때.
    """
    path = prd_path or (_OMC_DIR / "prd.json")
    if not path.exists():
        raise FileNotFoundError(f"prd.json을 찾을 수 없음: {path}")
    data: Any = json.loads(path.read_text(encoding="utf-8"))
    return data  # type: ignore[return-value]


def load_state(
    state_file: str = "leviathan-current-stage.json",
    state_path: pathlib.Path | None = None,
) -> dict[str, Any]:
    """LEVIATHAN 상태 JSON 파일을 로드.

    Args:
        state_file: .omc/state/ 내 파일명 (기본: leviathan-current-stage.json).
        state_path: 명시적 경로 오버라이드.

    Returns:
        파싱된 상태 딕셔너리.

    Raises:
        FileNotFoundError: 상태 파일이 존재하지 않을 때.
        json.JSONDecodeError: 파일이 유효한 JSON이 아닐 때.
    """
    path = state_path or (_OMC_DIR / "state" / state_file)
    if not path.exists():
        raise FileNotFoundError(f"상태 파일을 찾을 수 없음: {path}")
    data: Any = json.loads(path.read_text(encoding="utf-8"))
    return data
