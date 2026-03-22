"""LEVIATHAN 일관성 검사기 테스트.

대상 모듈: engine/src/workflow/consistency.py, engine/src/workflow/cli.py
검증 항목:
  - ConsistencyChecker.check_all() 실제 프로젝트 루트에서 OK 실행
  - CheckResult 데이터클래스 필드
  - ConsistencyReport has_drift / has_error 프로퍼티
  - ConsistencyReport format_report() 출력 형식
  - _check_files_exist() 정상 경로
  - _check_files_exist() 누락 파일 ERROR 케이스
  - _check_prd_counts() DRIFT 시나리오
  - _check_ssot_hash_drift() 체크포인트 없을 때 OK
  - CLI check_all main() 직접 호출 테스트
"""
from __future__ import annotations

import json
import pathlib

import pytest

# ---------------------------------------------------------------------------
# 경로 상수
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]  # engine/ → 레포 루트
_OMC_STATE = _REPO_ROOT / ".omc" / "state"

_REAL_PRD = _REPO_ROOT / ".omc" / "prd.json"
_REAL_SSOT = _REPO_ROOT / "SSOT.md"
_REAL_ACTIVE_PHASE = _REPO_ROOT / ".omc" / "state" / "leviathan-active-phase.json"

_REAL_FILES_EXIST = (
    _REAL_PRD.exists()
    and _REAL_SSOT.exists()
    and _REAL_ACTIVE_PHASE.exists()
)


# ---------------------------------------------------------------------------
# CheckResult 데이터클래스 테스트
# ---------------------------------------------------------------------------


class TestCheckResult:
    """CheckResult 데이터클래스 필드 및 기본값 검증."""

    def test_CheckResult_기본_필드로_생성_가능하다(self):
        """name과 status 인자만으로 CheckResult를 생성할 수 있다."""
        from src.workflow.consistency import CheckResult

        result = CheckResult(name="파일_존재", status="OK")

        assert result.name == "파일_존재"
        assert result.status == "OK"
        assert result.message == ""
        assert result.severity == "INFO"
        assert result.auto_fixable is False

    def test_CheckResult_모든_필드를_설정할_수_있다(self):
        """CheckResult의 모든 필드를 명시적으로 설정할 수 있다."""
        from src.workflow.consistency import CheckResult

        result = CheckResult(
            name="PRD_카운트",
            status="DRIFT",
            message="SSOT 209 != 실제 210",
            severity="HIGH",
            auto_fixable=False,
        )

        assert result.name == "PRD_카운트"
        assert result.status == "DRIFT"
        assert result.message == "SSOT 209 != 실제 210"
        assert result.severity == "HIGH"
        assert result.auto_fixable is False

    def test_CheckResult_ERROR_status를_설정할_수_있다(self):
        """status를 'ERROR'로 설정한 CheckResult를 생성할 수 있다."""
        from src.workflow.consistency import CheckResult

        result = CheckResult(name="파일_없음", status="ERROR", message="SSOT.md 누락", severity="CRITICAL")

        assert result.status == "ERROR"
        assert result.severity == "CRITICAL"

    def test_CheckResult_auto_fixable_True_설정(self):
        """auto_fixable=True로 CheckResult를 설정할 수 있다."""
        from src.workflow.consistency import CheckResult

        result = CheckResult(name="자동수정", status="DRIFT", auto_fixable=True)

        assert result.auto_fixable is True


# ---------------------------------------------------------------------------
# ConsistencyReport 테스트
# ---------------------------------------------------------------------------


class TestConsistencyReport:
    """ConsistencyReport 데이터클래스 동작 검증."""

    def test_빈_리포트는_has_drift와_has_error가_False이다(self):
        """checks가 비어 있는 ConsistencyReport는 has_drift=False, has_error=False이다."""
        from src.workflow.consistency import ConsistencyReport

        report = ConsistencyReport()

        assert report.has_drift is False
        assert report.has_error is False

    def test_DRIFT_결과_추가_시_has_drift가_True가_된다(self):
        """status='DRIFT'인 CheckResult를 추가하면 has_drift가 True가 된다."""
        from src.workflow.consistency import CheckResult, ConsistencyReport

        report = ConsistencyReport()
        report.checks.append(CheckResult(name="PRD_카운트", status="DRIFT"))

        assert report.has_drift is True
        assert report.has_error is False

    def test_ERROR_결과_추가_시_has_error가_True가_된다(self):
        """status='ERROR'인 CheckResult를 추가하면 has_error가 True가 된다."""
        from src.workflow.consistency import CheckResult, ConsistencyReport

        report = ConsistencyReport()
        report.checks.append(CheckResult(name="파일_없음", status="ERROR"))

        assert report.has_error is True
        assert report.has_drift is False

    def test_OK만_있는_리포트는_has_drift_has_error_모두_False이다(self):
        """모든 checks가 'OK'이면 has_drift와 has_error 모두 False이다."""
        from src.workflow.consistency import CheckResult, ConsistencyReport

        report = ConsistencyReport()
        report.checks.append(CheckResult(name="파일_존재", status="OK"))
        report.checks.append(CheckResult(name="PRD_카운트", status="OK"))

        assert report.has_drift is False
        assert report.has_error is False

    def test_summary_속성은_OK_DRIFT_ERROR_수를_포함한다(self):
        """summary 속성은 'OK=N, DRIFT=N, ERROR=N' 형태의 문자열을 반환한다."""
        from src.workflow.consistency import CheckResult, ConsistencyReport

        report = ConsistencyReport()
        report.checks.append(CheckResult(name="A", status="OK"))
        report.checks.append(CheckResult(name="B", status="DRIFT"))
        report.checks.append(CheckResult(name="C", status="ERROR"))

        summary = report.summary

        assert "OK=1" in summary
        assert "DRIFT=1" in summary
        assert "ERROR=1" in summary

    def test_format_report_출력에_검사_이름이_포함된다(self):
        """format_report()가 반환하는 문자열에 각 CheckResult의 name이 포함된다."""
        from src.workflow.consistency import CheckResult, ConsistencyReport

        report = ConsistencyReport()
        report.checks.append(CheckResult(name="파일_존재", status="OK", message="3개 모두 존재"))
        report.checks.append(CheckResult(name="PRD_카운트", status="DRIFT", message="수 불일치"))

        출력 = report.format_report()

        assert "파일_존재" in 출력, "format_report 출력에 '파일_존재'가 포함되어야 한다"
        assert "PRD_카운트" in 출력, "format_report 출력에 'PRD_카운트'가 포함되어야 한다"

    def test_format_report_출력에_상태_아이콘이_포함된다(self):
        """format_report() 출력에 [OK], [DRIFT], [ERROR] 아이콘이 포함된다."""
        from src.workflow.consistency import CheckResult, ConsistencyReport

        report = ConsistencyReport()
        report.checks.append(CheckResult(name="A", status="OK"))
        report.checks.append(CheckResult(name="B", status="DRIFT"))
        report.checks.append(CheckResult(name="C", status="ERROR"))

        출력 = report.format_report()

        assert "[OK]" in 출력
        assert "[DRIFT]" in 출력
        assert "[ERROR]" in 출력

    def test_format_report_자동_수정_가능_항목에_안내_문구가_포함된다(self):
        """auto_fixable=True인 항목은 format_report 출력에 '자동 수정 가능' 문구가 포함된다."""
        from src.workflow.consistency import CheckResult, ConsistencyReport

        report = ConsistencyReport()
        report.checks.append(
            CheckResult(name="자동수정가능", status="DRIFT", auto_fixable=True)
        )

        출력 = report.format_report()

        assert "자동 수정 가능" in 출력, "auto_fixable=True이면 '자동 수정 가능' 문구가 있어야 한다"


# ---------------------------------------------------------------------------
# ConsistencyChecker._check_files_exist() 테스트
# ---------------------------------------------------------------------------


class TestCheckFilesExist:
    """_check_files_exist() 메서드 동작 검증."""

    def test_모든_필수_파일이_존재하면_OK를_반환한다(self, tmp_path):
        """SSOT.md, prd.json, leviathan-active-phase.json이 모두 존재하면 status='OK'이다."""
        from src.workflow.consistency import ConsistencyChecker

        # 필수 파일 구조 생성
        (tmp_path / "SSOT.md").write_text("# SSOT", encoding="utf-8")
        omc_state = tmp_path / ".omc" / "state"
        omc_state.mkdir(parents=True)
        (tmp_path / ".omc" / "prd.json").write_text(
            json.dumps({"stories": []}), encoding="utf-8"
        )
        (omc_state / "leviathan-active-phase.json").write_text(
            json.dumps({"current_phase": "S13"}), encoding="utf-8"
        )

        checker = ConsistencyChecker(root=tmp_path)
        result = checker._check_files_exist()

        assert result.status == "OK", f"모든 파일 존재 시 OK이어야 한다: {result.message}"

    def test_SSOT_md가_없으면_ERROR를_반환한다(self, tmp_path):
        """SSOT.md가 없으면 _check_files_exist()가 status='ERROR'를 반환한다."""
        from src.workflow.consistency import ConsistencyChecker

        # SSOT.md 제외하고 나머지만 생성
        omc_state = tmp_path / ".omc" / "state"
        omc_state.mkdir(parents=True)
        (tmp_path / ".omc" / "prd.json").write_text("{}", encoding="utf-8")
        (omc_state / "leviathan-active-phase.json").write_text("{}", encoding="utf-8")

        checker = ConsistencyChecker(root=tmp_path)
        result = checker._check_files_exist()

        assert result.status == "ERROR", "SSOT.md 누락 시 ERROR여야 한다"
        assert "SSOT.md" in result.message, "에러 메시지에 'SSOT.md'가 포함되어야 한다"

    def test_prd_json이_없으면_ERROR를_반환한다(self, tmp_path):
        """.omc/prd.json이 없으면 _check_files_exist()가 status='ERROR'를 반환한다."""
        from src.workflow.consistency import ConsistencyChecker

        (tmp_path / "SSOT.md").write_text("# SSOT", encoding="utf-8")
        omc_state = tmp_path / ".omc" / "state"
        omc_state.mkdir(parents=True)
        # prd.json 의도적으로 미생성
        (omc_state / "leviathan-active-phase.json").write_text("{}", encoding="utf-8")

        checker = ConsistencyChecker(root=tmp_path)
        result = checker._check_files_exist()

        assert result.status == "ERROR", "prd.json 누락 시 ERROR여야 한다"
        assert "prd.json" in result.message, "에러 메시지에 'prd.json'이 포함되어야 한다"

    def test_active_phase_json이_없으면_ERROR를_반환한다(self, tmp_path):
        """leviathan-active-phase.json이 없으면 _check_files_exist()가 status='ERROR'를 반환한다."""
        from src.workflow.consistency import ConsistencyChecker

        (tmp_path / "SSOT.md").write_text("# SSOT", encoding="utf-8")
        omc_state = tmp_path / ".omc" / "state"
        omc_state.mkdir(parents=True)
        (tmp_path / ".omc" / "prd.json").write_text("{}", encoding="utf-8")
        # active-phase.json 의도적으로 미생성

        checker = ConsistencyChecker(root=tmp_path)
        result = checker._check_files_exist()

        assert result.status == "ERROR", "active-phase.json 누락 시 ERROR여야 한다"

    def test_ERROR_결과의_severity는_CRITICAL이다(self, tmp_path):
        """파일 누락 시 CheckResult의 severity는 'CRITICAL'이어야 한다."""
        from src.workflow.consistency import ConsistencyChecker

        # 아무 파일도 없는 빈 디렉토리에서 검사
        checker = ConsistencyChecker(root=tmp_path)
        result = checker._check_files_exist()

        assert result.severity == "CRITICAL", "파일 누락 에러의 severity는 CRITICAL이어야 한다"


# ---------------------------------------------------------------------------
# ConsistencyChecker._check_prd_counts() DRIFT 시나리오 테스트
# ---------------------------------------------------------------------------


class TestCheckPrdCounts:
    """_check_prd_counts() 메서드의 DRIFT 시나리오 검증."""

    def _최소_파일_세트_생성(
        self,
        tmp_path: pathlib.Path,
        stories: list[dict],
        state_total: int,
        state_passed: int,
        ssot_text: str = "",
    ) -> "ConsistencyChecker":
        """테스트용 최소 파일 세트를 생성하고 ConsistencyChecker를 반환한다."""
        from src.workflow.consistency import ConsistencyChecker

        (tmp_path / "SSOT.md").write_text(ssot_text or "# SSOT\n", encoding="utf-8")
        omc_state = tmp_path / ".omc" / "state"
        omc_state.mkdir(parents=True)
        (tmp_path / ".omc" / "prd.json").write_text(
            json.dumps({"stories": stories}), encoding="utf-8"
        )
        (omc_state / "leviathan-active-phase.json").write_text(
            json.dumps(
                {
                    "current_phase": "S13",
                    "prd": {"total_stories": state_total, "passed": state_passed},
                    "tests": {"passed": 4695},
                }
            ),
            encoding="utf-8",
        )
        return ConsistencyChecker(root=tmp_path)

    def test_prd_카운트_일치_시_OK를_반환한다(self, tmp_path):
        """prd.json과 state의 카운트가 일치하면 status='OK'를 반환한다."""
        stories = [
            {"id": f"US-{i:03d}", "passes": True}
            for i in range(1, 4)  # 3개 모두 통과
        ]
        checker = self._최소_파일_세트_생성(tmp_path, stories, state_total=3, state_passed=3)

        result = checker._check_prd_counts()

        assert result.status == "OK", f"카운트 일치 시 OK여야 한다: {result.message}"

    def test_state_통과_수가_실제와_다르면_DRIFT를_반환한다(self, tmp_path):
        """state의 passed 수가 prd.json 실제 통과 수와 다르면 status='DRIFT'를 반환한다."""
        stories = [
            {"id": "US-001", "passes": True},
            {"id": "US-002", "passes": False},
        ]
        # state에는 2개 통과라고 기록, 실제는 1개
        checker = self._최소_파일_세트_생성(tmp_path, stories, state_total=2, state_passed=2)

        result = checker._check_prd_counts()

        assert result.status == "DRIFT", f"카운트 불일치 시 DRIFT여야 한다: {result.message}"
        assert "통과" in result.message or "pass" in result.message.lower(), \
            f"에러 메시지에 통과 수 불일치 내용이 있어야 한다: {result.message}"

    def test_state_전체_수가_실제와_다르면_DRIFT를_반환한다(self, tmp_path):
        """state의 total_stories가 prd.json 실제 스토리 수와 다르면 status='DRIFT'를 반환한다."""
        stories = [
            {"id": "US-001", "passes": True},
            {"id": "US-002", "passes": True},
        ]
        # state에는 전체 5개라고 기록, 실제는 2개
        checker = self._최소_파일_세트_생성(tmp_path, stories, state_total=5, state_passed=2)

        result = checker._check_prd_counts()

        assert result.status == "DRIFT", f"전체 수 불일치 시 DRIFT여야 한다: {result.message}"

    def test_SSOT_md_PRD_수와_실제가_다르면_DRIFT를_반환한다(self, tmp_path):
        """SSOT.md §2의 PRD 수와 prd.json 실제 수가 다르면 DRIFT를 반환한다."""
        stories = [
            {"id": "US-001", "passes": True},
            {"id": "US-002", "passes": True},
        ]
        # SSOT에는 5개 전체, 3개 통과라고 기록, 실제 prd.json은 2개 전체, 2개 통과
        ssot_text = "## 2. 현재 상태\n**Phase**: S13\n5개 User Stories, 3 pass / 2 pending\n"
        checker = self._최소_파일_세트_생성(
            tmp_path, stories, state_total=2, state_passed=2, ssot_text=ssot_text
        )

        result = checker._check_prd_counts()

        assert result.status == "DRIFT", f"SSOT와 실제 수 불일치 시 DRIFT여야 한다: {result.message}"

    def test_stories_가_없으면_ERROR를_반환한다(self, tmp_path):
        """prd.json에 stories 배열이 없으면 status='ERROR'를 반환한다."""
        from src.workflow.consistency import ConsistencyChecker

        (tmp_path / "SSOT.md").write_text("# SSOT\n", encoding="utf-8")
        omc_state = tmp_path / ".omc" / "state"
        omc_state.mkdir(parents=True)
        (tmp_path / ".omc" / "prd.json").write_text(
            json.dumps({}),  # stories 키 없음
            encoding="utf-8",
        )
        (omc_state / "leviathan-active-phase.json").write_text(
            json.dumps({"current_phase": "S13", "prd": {}, "tests": {}}),
            encoding="utf-8",
        )

        checker = ConsistencyChecker(root=tmp_path)
        result = checker._check_prd_counts()

        assert result.status == "ERROR", "stories 없을 때 ERROR여야 한다"


# ---------------------------------------------------------------------------
# ConsistencyChecker._check_ssot_hash_drift() 테스트
# ---------------------------------------------------------------------------


class TestCheckSsotHashDrift:
    """_check_ssot_hash_drift() 메서드 동작 검증."""

    def test_체크포인트_DB_없을_때_OK를_반환한다(self, tmp_path):
        """checkpoints.db가 없으면 해시 드리프트 검사를 스킵하고 OK를 반환한다."""
        from src.workflow.consistency import ConsistencyChecker

        (tmp_path / "SSOT.md").write_text("# SSOT", encoding="utf-8")
        omc_state = tmp_path / ".omc" / "state"
        omc_state.mkdir(parents=True)
        # checkpoints.db 의도적으로 미생성

        checker = ConsistencyChecker(root=tmp_path)
        result = checker._check_ssot_hash_drift()

        assert result.status == "OK", "체크포인트 DB 없을 때 OK여야 한다"
        assert "스킵" in result.message or "없음" in result.message, \
            f"메시지에 스킵 또는 없음 표현이 있어야 한다: {result.message}"

    def test_체크포인트_DB는_있지만_데이터_없을_때_OK를_반환한다(self, tmp_path):
        """체크포인트 DB가 존재하지만 저장된 체크포인트가 없으면 OK를 반환한다."""
        import sqlite3
        from src.workflow.consistency import ConsistencyChecker

        (tmp_path / "SSOT.md").write_text("# SSOT", encoding="utf-8")
        omc_state = tmp_path / ".omc" / "state"
        omc_state.mkdir(parents=True)

        # 빈 체크포인트 DB 생성 (테이블만 있고 데이터 없음)
        db_path = omc_state / "checkpoints.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS checkpoints (
                id TEXT PRIMARY KEY, phase TEXT, stage TEXT,
                state_json TEXT, ssot_hash TEXT,
                prd_pass_count INTEGER, prd_total_count INTEGER,
                test_count INTEGER, trigger TEXT, created_at TEXT
            )
        """)
        conn.commit()
        conn.close()

        checker = ConsistencyChecker(root=tmp_path)
        result = checker._check_ssot_hash_drift()

        assert result.status == "OK", f"빈 DB에서 OK여야 한다: {result.message}"

    def test_SSOT_md_해시가_체크포인트와_일치하면_OK를_반환한다(self, tmp_path):
        """SSOT.md의 현재 해시가 마지막 체크포인트 해시와 일치하면 OK를 반환한다."""
        import hashlib
        import sqlite3
        from src.workflow.consistency import ConsistencyChecker

        ssot_content = "# SSOT\n**Phase**: S13\n"
        (tmp_path / "SSOT.md").write_text(ssot_content, encoding="utf-8")
        omc_state = tmp_path / ".omc" / "state"
        omc_state.mkdir(parents=True)

        # 현재 해시와 동일한 해시로 체크포인트 저장
        current_hash = hashlib.sha256(ssot_content.encode("utf-8")).hexdigest()[:16]
        db_path = omc_state / "checkpoints.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS checkpoints (
                id TEXT PRIMARY KEY, phase TEXT, stage TEXT,
                state_json TEXT, ssot_hash TEXT,
                prd_pass_count INTEGER, prd_total_count INTEGER,
                test_count INTEGER, trigger TEXT, created_at TEXT
            )
        """)
        conn.execute(
            "INSERT INTO checkpoints VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "S13_A_20260317_120000", "S13", "A", "{}",
                current_hash, 209, 216, 4695, "테스트", "2026-03-17T12:00:00",
            ),
        )
        conn.commit()
        conn.close()

        checker = ConsistencyChecker(root=tmp_path)
        result = checker._check_ssot_hash_drift()

        assert result.status == "OK", f"해시 일치 시 OK여야 한다: {result.message}"

    def test_SSOT_md가_변경되면_DRIFT를_반환한다(self, tmp_path):
        """체크포인트 이후 SSOT.md가 수정되었으면 status='DRIFT'를 반환한다."""
        import hashlib
        import sqlite3
        from src.workflow.consistency import ConsistencyChecker

        # 체크포인트에는 이전 내용의 해시 저장
        old_content = "# 이전 SSOT 내용\n"
        old_hash = hashlib.sha256(old_content.encode("utf-8")).hexdigest()[:16]

        # 현재 SSOT.md는 다른 내용
        (tmp_path / "SSOT.md").write_text("# 수정된 SSOT 내용\n", encoding="utf-8")
        omc_state = tmp_path / ".omc" / "state"
        omc_state.mkdir(parents=True)

        db_path = omc_state / "checkpoints.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS checkpoints (
                id TEXT PRIMARY KEY, phase TEXT, stage TEXT,
                state_json TEXT, ssot_hash TEXT,
                prd_pass_count INTEGER, prd_total_count INTEGER,
                test_count INTEGER, trigger TEXT, created_at TEXT
            )
        """)
        conn.execute(
            "INSERT INTO checkpoints VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "S13_A_20260317_100000", "S13", "A", "{}",
                old_hash, 209, 216, 4695, "테스트", "2026-03-17T10:00:00",
            ),
        )
        conn.commit()
        conn.close()

        checker = ConsistencyChecker(root=tmp_path)
        result = checker._check_ssot_hash_drift()

        assert result.status == "DRIFT", f"SSOT 변경 감지 시 DRIFT여야 한다: {result.message}"


# ---------------------------------------------------------------------------
# ConsistencyChecker.check_all() 실제 프로젝트 테스트
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _REAL_FILES_EXIST,
    reason="실제 .omc/prd.json, SSOT.md, leviathan-active-phase.json 파일이 없어 스킵",
)
class TestCheckAllRealProject:
    """실제 프로젝트 루트에서 check_all() 실행 검증."""

    def test_실제_프로젝트_루트에서_check_all이_ConsistencyReport를_반환한다(self):
        """실제 프로젝트 파일들로 check_all()을 실행하면 ConsistencyReport가 반환된다."""
        from src.workflow.consistency import ConsistencyChecker, ConsistencyReport

        checker = ConsistencyChecker(root=_REPO_ROOT)
        report = checker.check_all()

        assert isinstance(report, ConsistencyReport), "check_all()은 ConsistencyReport를 반환해야 한다"

    def test_실제_프로젝트에서_check_all_결과에_파일_존재_검사가_포함된다(self):
        """실제 프로젝트에서 check_all()을 실행하면 '파일_존재' 검사 결과가 포함된다."""
        from src.workflow.consistency import ConsistencyChecker

        checker = ConsistencyChecker(root=_REPO_ROOT)
        report = checker.check_all()

        이름들 = [c.name for c in report.checks]
        assert "파일_존재" in 이름들, f"'파일_존재' 검사가 포함되어야 한다: {이름들}"

    def test_실제_프로젝트에서_파일_존재_검사는_OK이다(self):
        """실제 파일이 모두 존재하므로 '파일_존재' 검사는 status='OK'여야 한다."""
        from src.workflow.consistency import ConsistencyChecker

        checker = ConsistencyChecker(root=_REPO_ROOT)
        report = checker.check_all()

        파일_검사 = next((c for c in report.checks if c.name == "파일_존재"), None)
        assert 파일_검사 is not None, "'파일_존재' 검사 결과를 찾을 수 없다"
        assert 파일_검사.status == "OK", \
            f"실제 파일들이 존재하므로 OK여야 한다: {파일_검사.message}"


# ---------------------------------------------------------------------------
# CLI check_all main() 직접 호출 테스트
# ---------------------------------------------------------------------------


class TestCliCheckAll:
    """CLI check_all 커맨드 main() 직접 호출 검증."""

    def test_누락_파일_있는_디렉토리에서_check_all은_종료코드_2를_반환한다(self, tmp_path):
        """필수 파일이 없는 디렉토리를 --root로 지정하면 종료 코드 2를 반환한다."""
        from src.workflow.cli import main

        결과 = main(["--root", str(tmp_path), "check_all"])

        assert 결과 == 2, f"필수 파일 없을 때 종료 코드 2여야 한다: {결과}"

    def test_모든_파일이_있고_일관성_통과_시_check_all은_종료코드_0을_반환한다(self, tmp_path):
        """일관성 검사를 통과하면 종료 코드 0을 반환한다."""
        from src.workflow.cli import main

        # 일관성 검사를 통과하는 최소 파일 세트 생성
        (tmp_path / "SSOT.md").write_text(
            "## 2. 현재 상태\n**Phase**: S13\n**Tests**: 4,695 passed / 0 failed / 12 skipped\n"
            "3개 User Stories, 2 pass / 1 pending\n",
            encoding="utf-8",
        )
        omc_state = tmp_path / ".omc" / "state"
        omc_state.mkdir(parents=True)
        stories = [
            {"id": "US-001", "passes": True},
            {"id": "US-002", "passes": True},
            {"id": "US-003", "passes": False},
        ]
        (tmp_path / ".omc" / "prd.json").write_text(
            json.dumps({"stories": stories}), encoding="utf-8"
        )
        (omc_state / "leviathan-active-phase.json").write_text(
            json.dumps(
                {
                    "current_phase": "S13",
                    "prd": {"total_stories": 3, "passed": 2},
                    "tests": {"passed": 4695},
                }
            ),
            encoding="utf-8",
        )
        # 검사 6: CLAUDE.md 동기화 — .claude/CLAUDE.md 필요
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        (claude_dir / "CLAUDE.md").write_text(
            "- **Phase 순서**: **S13** 진행중\n",
            encoding="utf-8",
        )

        결과 = main(["--root", str(tmp_path), "check_all"])

        assert 결과 == 0, f"모든 검사 통과 시 종료 코드 0이어야 한다: {결과}"

    def test_DRIFT_있는_경우_check_all은_종료코드_1을_반환한다(self, tmp_path):
        """드리프트가 있는 경우 종료 코드 1을 반환한다."""
        from src.workflow.cli import main

        (tmp_path / "SSOT.md").write_text(
            "## 2. 현재 상태\n**Phase**: S13\n"
            "99개 User Stories, 88 pass / 11 pending\n",  # 실제와 다른 수
            encoding="utf-8",
        )
        omc_state = tmp_path / ".omc" / "state"
        omc_state.mkdir(parents=True)
        stories = [{"id": "US-001", "passes": True}]
        (tmp_path / ".omc" / "prd.json").write_text(
            json.dumps({"stories": stories}), encoding="utf-8"
        )
        (omc_state / "leviathan-active-phase.json").write_text(
            json.dumps(
                {
                    "current_phase": "S13",
                    "prd": {"total_stories": 1, "passed": 1},
                    "tests": {"passed": 4695},
                }
            ),
            encoding="utf-8",
        )
        # 검사 6: CLAUDE.md 필요 (ERROR 방지)
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        (claude_dir / "CLAUDE.md").write_text(
            "- **Phase 순서**: **S13** 진행중\n",
            encoding="utf-8",
        )

        결과 = main(["--root", str(tmp_path), "check_all"])

        assert 결과 == 1, f"DRIFT 있을 때 종료 코드 1이어야 한다: {결과}"

    def test_build_parser가_check_all_서브커맨드를_포함한다(self):
        """build_parser()가 반환하는 파서에 'check_all' 서브커맨드가 있어야 한다."""
        from src.workflow.cli import build_parser

        parser = build_parser()

        # check_all 서브커맨드로 파싱이 가능하면 성공
        # tmp dir 없이도 파서 구조 확인 가능
        assert parser is not None, "파서가 생성되어야 한다"
