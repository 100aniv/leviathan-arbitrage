"""3-Way 일관성 검사기: SSOT.md <-> prd.json <-> State JSON.

Entry Gate 검증을 자동화. 결과는 Karina(architect 에이전트)에게 입력으로 전달됨.
이 도구는 보조 역할이며, Karina의 최종 판단을 대체하지 않음.

파일 형식 (2026-03-17 기준):
  SSOT.md §2 관심 라인:
    **Phase**: S13 (Shadow Loss Prevention)
    **Tests**: 4,695 passed / 0 failed / 12 skipped
    PRD: `.omc/prd.json` (216개 User Stories, 209 pass / 7 pending)

  .omc/prd.json:
    {"stories": [{"id": "US-001", ..., "passes": true|false}, ...], ...}

  .omc/state/leviathan-active-phase.json:
    {"current_phase": "S13", "prd": {"passed": 209, "total_stories": 216}, ...}

  .omc/state/leviathan-current-stage.json:
    {"phase": "S13", "stage": "pending", ...}
"""
import json
import re
import hashlib
import sqlite3
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CheckResult:
    """개별 검사 결과."""
    name: str
    status: str  # "OK", "DRIFT", "ERROR"
    message: str = ""
    severity: str = "INFO"  # "INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"
    auto_fixable: bool = False


@dataclass
class ConsistencyReport:
    """전체 일관성 검사 리포트."""
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def has_drift(self) -> bool:
        """드리프트(불일치)가 있는지 확인."""
        return any(c.status == "DRIFT" for c in self.checks)

    @property
    def has_error(self) -> bool:
        """에러가 있는지 확인."""
        return any(c.status == "ERROR" for c in self.checks)

    @property
    def summary(self) -> str:
        """검사 결과 요약 문자열."""
        ok = sum(1 for c in self.checks if c.status == "OK")
        drift = sum(1 for c in self.checks if c.status == "DRIFT")
        error = sum(1 for c in self.checks if c.status == "ERROR")
        return f"OK={ok}, DRIFT={drift}, ERROR={error}"

    def format_report(self) -> str:
        """사람이 읽을 수 있는 리포트 생성."""
        lines = [f"## 일관성 검사 리포트: {self.summary}\n"]
        for c in self.checks:
            icon = {"OK": "[OK]", "DRIFT": "[DRIFT]", "ERROR": "[ERROR]"}.get(c.status, "[?]")
            fixable = " (자동 수정 가능)" if c.auto_fixable else ""
            lines.append(f"{icon} **{c.name}** [{c.severity}]: {c.message}{fixable}")
        return "\n".join(lines)


class ConsistencyChecker:
    """SSOT.md, prd.json, State JSON 간 3-Way 일관성 검사."""

    def __init__(self, root: Optional[Path] = None):
        self.root = root or Path(".")  # 프로젝트 루트
        self.ssot_path = self.root / "SSOT.md"
        self.prd_path = self.root / ".omc" / "prd.json"
        self.state_dir = self.root / ".omc" / "state"
        self.active_phase_path = self.state_dir / "leviathan-active-phase.json"
        self.current_stage_path = self.state_dir / "leviathan-current-stage.json"
        self.checkpoint_db = self.state_dir / "checkpoints.db"

    def check_all(self) -> ConsistencyReport:
        """모든 일관성 검사 실행. 리포트 반환."""
        report = ConsistencyReport()
        # 게이트: 파일 존재 여부 먼저 확인
        files_result = self._check_files_exist()
        report.checks.append(files_result)
        if files_result.status == "ERROR":
            # 소스 파일 누락 시 나머지 검사 스킵
            return report
        report.checks.append(self._check_prd_counts())
        report.checks.append(self._check_active_phase())
        report.checks.append(self._check_test_count())
        report.checks.append(self._check_ssot_hash_drift())
        return report

    # ------------------------------------------------------------------
    # 개별 검사 구현
    # ------------------------------------------------------------------

    def _check_files_exist(self) -> CheckResult:
        """3개 소스 파일 존재 여부 확인."""
        missing = []
        if not self.ssot_path.exists():
            missing.append("SSOT.md")
        if not self.prd_path.exists():
            missing.append(".omc/prd.json")
        if not self.state_dir.exists():
            missing.append(".omc/state/")
        elif not self.active_phase_path.exists():
            missing.append(".omc/state/leviathan-active-phase.json")
        if missing:
            return CheckResult(
                "파일_존재",
                "ERROR",
                f"누락: {', '.join(missing)}",
                "CRITICAL",
            )
        return CheckResult("파일_존재", "OK", "3개 소스 모두 존재")

    def _check_prd_counts(self) -> CheckResult:
        """PRD 통과 수: SSOT §2 vs prd.json 실제 수."""
        # --- prd.json에서 실제 수 읽기 ---
        try:
            prd_data = json.loads(self.prd_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            return CheckResult("PRD_카운트", "ERROR", f"prd.json 읽기 실패: {exc}", "CRITICAL")

        stories = prd_data.get("stories", [])
        if not stories:
            return CheckResult("PRD_카운트", "ERROR", "prd.json에 stories 배열 없음", "HIGH")

        actual_total = len(stories)
        actual_passed = sum(1 for s in stories if s.get("passes") is True)

        # --- active-phase state에서 기록된 수 읽기 ---
        try:
            state = json.loads(self.active_phase_path.read_text(encoding="utf-8"))
            prd_state = state.get("prd", {})
            state_total = prd_state.get("total_stories")
            state_passed = prd_state.get("passed")
        except (json.JSONDecodeError, OSError) as exc:
            return CheckResult("PRD_카운트", "ERROR", f"active-phase 상태 읽기 실패: {exc}", "HIGH")

        # --- SSOT.md §2에서 기록된 수 읽기 ---
        ssot_total, ssot_passed = self._parse_ssot_prd_counts()

        drifts = []
        if state_total is not None and state_total != actual_total:
            drifts.append(f"state 전체 {state_total} != 실제 {actual_total}")
        if state_passed is not None and state_passed != actual_passed:
            drifts.append(f"state 통과 {state_passed} != 실제 {actual_passed}")
        if ssot_total is not None and ssot_total != actual_total:
            drifts.append(f"SSOT 전체 {ssot_total} != 실제 {actual_total}")
        if ssot_passed is not None and ssot_passed != actual_passed:
            drifts.append(f"SSOT 통과 {ssot_passed} != 실제 {actual_passed}")

        if drifts:
            return CheckResult(
                "PRD_카운트",
                "DRIFT",
                f"PRD 수 불일치: {'; '.join(drifts)}",
                "HIGH",
                auto_fixable=False,
            )
        return CheckResult(
            "PRD_카운트",
            "OK",
            f"PRD 수 일치: {actual_passed}/{actual_total} 통과",
        )

    def _check_active_phase(self) -> CheckResult:
        """활성 Phase: State JSON vs SSOT §2."""
        # --- state에서 읽기 ---
        try:
            state = json.loads(self.active_phase_path.read_text(encoding="utf-8"))
            state_phase = state.get("current_phase")
        except (json.JSONDecodeError, OSError) as exc:
            return CheckResult("활성_Phase", "ERROR", f"active-phase 상태 읽기 실패: {exc}", "HIGH")

        # --- current-stage에서도 확인 ---
        stage_phase: Optional[str] = None
        if self.current_stage_path.exists():
            try:
                cs = json.loads(self.current_stage_path.read_text(encoding="utf-8"))
                stage_phase = cs.get("phase")
            except (json.JSONDecodeError, OSError):
                pass  # 비치명적; current-stage는 보조

        # --- SSOT.md §2에서 읽기 ---
        ssot_phase = self._parse_ssot_current_phase()

        drifts = []
        if ssot_phase and ssot_phase != state_phase:
            drifts.append(f"SSOT phase '{ssot_phase}' != state '{state_phase}'")
        if stage_phase and stage_phase != state_phase:
            drifts.append(f"current-stage phase '{stage_phase}' != active-phase '{state_phase}'")

        if drifts:
            return CheckResult(
                "활성_Phase",
                "DRIFT",
                f"Phase 불일치: {'; '.join(drifts)}",
                "HIGH",
            )
        return CheckResult(
            "활성_Phase",
            "OK",
            f"활성 Phase 일치: {state_phase}",
        )

    def _check_test_count(self) -> CheckResult:
        """테스트 수: SSOT §2 vs active-phase state (마지막 기록 값)."""
        # pytest를 여기서 실행하지 않음 (너무 느림).
        # SSOT 기록 수와 state 기록 수를 비교.
        ssot_tests = self._parse_ssot_test_count()

        try:
            state = json.loads(self.active_phase_path.read_text(encoding="utf-8"))
            tests_state = state.get("tests", {})
            state_passed = tests_state.get("passed")
        except (json.JSONDecodeError, OSError) as exc:
            return CheckResult("테스트_수", "ERROR", f"active-phase 상태 읽기 실패: {exc}", "MEDIUM")

        if ssot_tests is None and state_passed is None:
            return CheckResult("테스트_수", "ERROR", "SSOT와 state 모두에서 테스트 수를 찾을 수 없음", "MEDIUM")

        if ssot_tests is not None and state_passed is not None and ssot_tests != state_passed:
            return CheckResult(
                "테스트_수",
                "DRIFT",
                f"테스트 수: SSOT 기록 {ssot_tests} vs state 기록 {state_passed}",
                "MEDIUM",
            )

        recorded = ssot_tests or state_passed
        return CheckResult(
            "테스트_수",
            "OK",
            f"테스트 수 일치: {recorded} passed (마지막 기록)",
        )

    def _check_ssot_hash_drift(self) -> CheckResult:
        """마지막 체크포인트 이후 SSOT.md 변경 여부 확인."""
        if not self.checkpoint_db.exists():
            return CheckResult(
                "SSOT_해시_드리프트",
                "OK",
                "체크포인트 DB 없음 — 해시 드리프트 검사 스킵",
                "INFO",
            )

        try:
            import sqlite3
            conn = sqlite3.connect(str(self.checkpoint_db))
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT ssot_hash, id, created_at FROM checkpoints ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            conn.close()
        except (sqlite3.Error, OSError) as exc:
            return CheckResult("SSOT_해시_드리프트", "ERROR", f"체크포인트 DB 읽기 실패: {exc}", "LOW")

        if row is None:
            return CheckResult(
                "SSOT_해시_드리프트",
                "OK",
                "저장된 체크포인트 없음 — 해시 드리프트 검사 스킵",
                "INFO",
            )

        last_hash: str = row["ssot_hash"] or ""
        last_checkpoint_id: str = row["id"]
        last_created_at: str = row["created_at"]

        current_hash = self._hash_file(self.ssot_path)

        if last_hash and current_hash and last_hash != current_hash:
            return CheckResult(
                "SSOT_해시_드리프트",
                "DRIFT",
                (
                    f"체크포인트 '{last_checkpoint_id}' ({last_created_at}) 이후 "
                    f"SSOT.md가 워크플로우 외부에서 변경됨. "
                    f"저장 해시: {last_hash}, 현재: {current_hash}"
                ),
                "MEDIUM",
            )

        return CheckResult(
            "SSOT_해시_드리프트",
            "OK",
            f"SSOT.md 해시가 체크포인트 '{last_checkpoint_id}'와 일치",
        )

    # ------------------------------------------------------------------
    # SSOT.md 파싱 헬퍼
    # ------------------------------------------------------------------

    def _read_ssot_section2(self) -> str:
        """SSOT.md §2 텍스트 반환 (## 2. 부터 다음 ## 까지)."""
        try:
            text = self.ssot_path.read_text(encoding="utf-8")
        except OSError:
            return ""
        # "## 2." 부터 다음 "## " 앞까지 추출
        match = re.search(r"(## 2\..*?)(?=\n## |\Z)", text, re.DOTALL)
        return match.group(1) if match else ""

    def _parse_ssot_prd_counts(self) -> tuple[Optional[int], Optional[int]]:
        """SSOT §2에서 PRD 전체/통과 수 파싱.

        패턴 예시:
          216개 User Stories, 209 pass / 7 pending
        반환: (전체, 통과) 또는 (None, None)
        """
        section = self._read_ssot_section2()
        if not section:
            return None, None

        # 패턴: NNN개 User Stories, NNN pass
        m = re.search(r"(\d+)개\s+User\s+Stories,\s+(\d+)\s+pass", section)
        if m:
            return int(m.group(1)), int(m.group(2))

        # 폴백: PRD: NNN pass / NNN pending 스타일
        m = re.search(r"PRD.*?(\d+)\s+pass\s*/\s*(\d+)\s+pending", section)
        if m:
            passed = int(m.group(1))
            pending = int(m.group(2))
            return passed + pending, passed

        return None, None

    def _parse_ssot_current_phase(self) -> Optional[str]:
        """SSOT §2에서 현재 Phase 파싱.

        패턴 예시: **Phase**: S13 (Shadow Loss Prevention)
        반환: "S13" 같은 Phase 문자열 또는 None
        """
        section = self._read_ssot_section2()
        if not section:
            return None
        m = re.search(r"\*\*Phase\*\*:\s*([A-Z0-9\-]+)", section)
        return m.group(1).strip() if m else None

    def _parse_ssot_test_count(self) -> Optional[int]:
        """SSOT §2에서 통과 테스트 수 파싱.

        패턴 예시: **Tests**: 4,695 passed / 0 failed / 12 skipped
        반환: 정수 또는 None
        """
        section = self._read_ssot_section2()
        if not section:
            return None
        # "4,695 passed" 또는 "4695 passed" 매칭
        m = re.search(r"\*\*Tests\*\*:\s*([\d,]+)\s+passed", section)
        if m:
            return int(m.group(1).replace(",", ""))
        return None

    # ------------------------------------------------------------------
    # 유틸리티
    # ------------------------------------------------------------------

    def _hash_file(self, path: Path) -> str:
        """드리프트 감지용 파일 SHA256 해시 (앞 16자)."""
        if not path.exists():
            return ""
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
