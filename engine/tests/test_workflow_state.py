"""LEVIATHAN 워크플로우 상태 스키마 테스트.

대상 모듈: engine/src/workflow/state_schema.py
검증 항목:
  - load_prd() / load_state() 헬퍼 함수
  - hash_file() 해시 일관성
  - PRDUserStory / LeviathanState TypedDict 필드
  - ConsistencyReport 데이터클래스 동작
  - JSON Schema 파일 존재 및 유효성
  - jsonschema 검증 (정상 케이스 + 위반 케이스)
"""
from __future__ import annotations

import json
import pathlib

import pytest

# ---------------------------------------------------------------------------
# 경로 상수
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]  # engine/ → 레포 루트
_OMC_DIR = _REPO_ROOT / ".omc"
_SCHEMA_DIR = (
    pathlib.Path(__file__).resolve().parents[1]  # tests/ → engine/
    / "src" / "workflow" / "schemas"
)

_REAL_PRD = _OMC_DIR / "prd.json"
_REAL_STATE_DIR = _OMC_DIR / "state"
_REAL_ACTIVE_PHASE = _REAL_STATE_DIR / "leviathan-active-phase.json"
_REAL_CURRENT_STAGE = _REAL_STATE_DIR / "leviathan-current-stage.json"

_PRD_SCHEMA = _SCHEMA_DIR / "prd_schema.json"
_STATE_SCHEMA = _SCHEMA_DIR / "state_schema.json"


# ---------------------------------------------------------------------------
# load_prd() 테스트
# ---------------------------------------------------------------------------


class TestLoadPrd:
    """load_prd() 헬퍼 함수 동작 검증."""

    @pytest.mark.skipif(
        not _REAL_PRD.exists(),
        reason=".omc/prd.json 파일이 존재하지 않아 스킵",
    )
    def test_실제_prd_json_로드_성공(self):
        """실제 .omc/prd.json 을 로드하면 stories 배열을 포함한 딕셔너리를 반환한다."""
        from src.workflow.state_schema import load_prd

        doc = load_prd()

        assert isinstance(doc, dict), "반환 타입은 dict이어야 한다"
        assert "stories" in doc, "prd.json에는 'stories' 키가 있어야 한다"
        assert isinstance(doc["stories"], list), "'stories' 값은 list이어야 한다"
        assert len(doc["stories"]) > 0, "stories 배열은 비어 있지 않아야 한다"

    @pytest.mark.skipif(
        not _REAL_PRD.exists(),
        reason=".omc/prd.json 파일이 존재하지 않아 스킵",
    )
    def test_실제_prd_json_각_스토리는_id와_passes_필드를_포함한다(self):
        """prd.json의 각 스토리 항목에는 'id'와 'passes' 필드가 있어야 한다."""
        from src.workflow.state_schema import load_prd

        doc = load_prd()

        for story in doc["stories"][:5]:  # 처음 5개만 검사
            assert "id" in story, f"스토리에 'id' 필드가 없음: {story}"
            assert "passes" in story, f"스토리 {story.get('id')}에 'passes' 필드가 없음"

    def test_존재하지_않는_경로는_FileNotFoundError를_발생시킨다(self, tmp_path):
        """존재하지 않는 경로로 load_prd() 호출 시 FileNotFoundError가 발생한다."""
        from src.workflow.state_schema import load_prd

        with pytest.raises(FileNotFoundError, match="prd.json을 찾을 수 없음"):
            load_prd(prd_path=tmp_path / "nonexistent_prd.json")

    def test_명시적_경로로_임시_prd_json_로드(self, tmp_path):
        """명시적 경로 오버라이드로 임시 prd.json을 로드한다."""
        from src.workflow.state_schema import load_prd

        fake_prd = {
            "project": "TEST",
            "stories": [
                {"id": "US-001", "title": "테스트 스토리", "phase": "A", "priority": "HIGH", "passes": True}
            ],
        }
        prd_file = tmp_path / "prd.json"
        prd_file.write_text(json.dumps(fake_prd), encoding="utf-8")

        doc = load_prd(prd_path=prd_file)

        assert doc["project"] == "TEST"
        assert len(doc["stories"]) == 1
        assert doc["stories"][0]["id"] == "US-001"


# ---------------------------------------------------------------------------
# load_state() 테스트
# ---------------------------------------------------------------------------


class TestLoadState:
    """load_state() 헬퍼 함수 동작 검증."""

    @pytest.mark.skipif(
        not _REAL_ACTIVE_PHASE.exists(),
        reason="leviathan-active-phase.json 파일이 존재하지 않아 스킵",
    )
    def test_실제_active_phase_state_로드_성공(self):
        """실제 leviathan-active-phase.json을 로드하면 current_phase 키를 포함한다."""
        from src.workflow.state_schema import load_state

        state = load_state(
            state_file="leviathan-active-phase.json",
            state_path=_REAL_ACTIVE_PHASE,
        )

        assert isinstance(state, dict), "반환 타입은 dict이어야 한다"
        assert "current_phase" in state, "active-phase 상태에는 'current_phase' 키가 있어야 한다"

    @pytest.mark.skipif(
        not _REAL_CURRENT_STAGE.exists(),
        reason="leviathan-current-stage.json 파일이 존재하지 않아 스킵",
    )
    def test_실제_current_stage_state_로드_성공(self):
        """실제 leviathan-current-stage.json을 로드하면 phase와 stage 키를 포함한다."""
        from src.workflow.state_schema import load_state

        state = load_state(state_path=_REAL_CURRENT_STAGE)

        assert "phase" in state, "current-stage 상태에는 'phase' 키가 있어야 한다"
        assert "stage" in state, "current-stage 상태에는 'stage' 키가 있어야 한다"

    def test_존재하지_않는_state_파일은_FileNotFoundError를_발생시킨다(self, tmp_path):
        """존재하지 않는 경로로 load_state() 호출 시 FileNotFoundError가 발생한다."""
        from src.workflow.state_schema import load_state

        with pytest.raises(FileNotFoundError, match="상태 파일을 찾을 수 없음"):
            load_state(state_path=tmp_path / "nonexistent.json")

    def test_명시적_경로로_임시_state_json_로드(self, tmp_path):
        """명시적 경로 오버라이드로 임시 상태 JSON을 로드한다."""
        from src.workflow.state_schema import load_state

        fake_state = {"phase": "S13", "stage": "pending", "escalation_level": "L0"}
        state_file = tmp_path / "test-state.json"
        state_file.write_text(json.dumps(fake_state), encoding="utf-8")

        state = load_state(state_path=state_file)

        assert state["phase"] == "S13"
        assert state["stage"] == "pending"
        assert state["escalation_level"] == "L0"


# ---------------------------------------------------------------------------
# hash_file() 테스트
# ---------------------------------------------------------------------------


class TestHashFile:
    """hash_file() 헬퍼 함수의 해시 일관성 검증."""

    def test_동일_파일을_두_번_해시하면_같은_값을_반환한다(self, tmp_path):
        """동일한 파일에 hash_file()을 두 번 호출하면 동일한 해시를 반환한다."""
        from src.workflow.state_schema import hash_file

        f = tmp_path / "sample.txt"
        f.write_text("레비아탄 엔진 해시 테스트", encoding="utf-8")

        hash1 = hash_file(f)
        hash2 = hash_file(f)

        assert hash1 == hash2, "동일 파일의 해시는 항상 같아야 한다"

    def test_내용이_다른_두_파일은_서로_다른_해시를_반환한다(self, tmp_path):
        """내용이 다른 두 파일을 해시하면 서로 다른 값이 나와야 한다."""
        from src.workflow.state_schema import hash_file

        f1 = tmp_path / "file_a.txt"
        f2 = tmp_path / "file_b.txt"
        f1.write_text("파일 A 내용", encoding="utf-8")
        f2.write_text("파일 B 내용 (다름)", encoding="utf-8")

        assert hash_file(f1) != hash_file(f2), "내용이 다른 파일의 해시는 달라야 한다"

    def test_해시_결과는_64자_16진수_문자열이다(self, tmp_path):
        """SHA-256 해시 결과는 64자 16진수 문자열이어야 한다."""
        from src.workflow.state_schema import hash_file

        f = tmp_path / "hash_check.txt"
        f.write_bytes(b"\x00\x01\x02\x03")

        result = hash_file(f)

        assert isinstance(result, str), "해시 결과는 문자열이어야 한다"
        assert len(result) == 64, f"SHA-256 해시는 64자여야 한다. 실제: {len(result)}"
        assert all(c in "0123456789abcdef" for c in result), "해시는 16진수 문자만 포함해야 한다"

    def test_파일_내용이_바뀌면_해시도_바뀐다(self, tmp_path):
        """파일 내용을 수정하면 hash_file() 결과도 달라진다."""
        from src.workflow.state_schema import hash_file

        f = tmp_path / "mutable.txt"
        f.write_text("원본 내용", encoding="utf-8")
        hash_before = hash_file(f)

        f.write_text("수정된 내용", encoding="utf-8")
        hash_after = hash_file(f)

        assert hash_before != hash_after, "파일 내용 변경 후 해시가 달라야 한다"


# ---------------------------------------------------------------------------
# TypedDict 필드 테스트
# ---------------------------------------------------------------------------


class TestPRDUserStoryTypedDict:
    """PRDUserStory TypedDict 구조 확인."""

    def test_PRDUserStory_TypedDict_임포트_성공(self):
        """PRDUserStory TypedDict을 임포트할 수 있어야 한다."""
        from src.workflow.state_schema import PRDUserStory  # noqa: F401

    def test_PRDUserStory_딕셔너리로_인스턴스화_가능하다(self):
        """PRDUserStory 구조에 맞는 딕셔너리를 생성할 수 있다."""
        from src.workflow.state_schema import PRDUserStory

        story: PRDUserStory = {
            "id": "US-001",
            "title": "첫 번째 스토리",
            "phase": "S13",
            "priority": "HIGH",
            "passes": True,
            "acceptanceCriteria": ["조건 1", "조건 2"],
        }

        assert story["id"] == "US-001"
        assert story["passes"] is True
        assert isinstance(story["acceptanceCriteria"], list)

    def test_PRDDocument_TypedDict_stories_필드를_포함한다(self):
        """PRDDocument TypedDict에 stories 필드가 있어야 한다."""
        from src.workflow.state_schema import PRDDocument

        doc: PRDDocument = {
            "project": "LEVIATHAN",
            "version": "1.0",
            "stories": [],
        }

        assert doc["project"] == "LEVIATHAN"
        assert isinstance(doc["stories"], list)


class TestLeviathanStateTypedDict:
    """LeviathanState TypedDict 구조 확인."""

    def test_LeviathanState_TypedDict_임포트_성공(self):
        """LeviathanState TypedDict을 임포트할 수 있어야 한다."""
        from src.workflow.state_schema import LeviathanState  # noqa: F401

    def test_LeviathanState_필수_필드로_딕셔너리_생성_가능하다(self):
        """LeviathanState 구조에 맞는 딕셔너리를 생성할 수 있다."""
        from src.workflow.state_schema import LeviathanState, StageDetail

        detail: StageDetail = {
            "A": {"status": "completed"},
            "B": {"status": "in_progress"},
            "C": {"status": "pending"},
        }
        state: LeviathanState = {
            "phase": "S13",
            "stage": "B",
            "stage_detail": detail,
            "escalation_level": "L0",
            "updated_at": "2026-03-17T00:00:00",
            "updated_by": "test-engineer",
        }

        assert state["phase"] == "S13"
        assert state["stage"] == "B"
        assert state["escalation_level"] == "L0"

    def test_StageBDetail_shadow_필드들을_포함할_수_있다(self):
        """StageBDetail TypedDict에 shadow 관련 필드를 설정할 수 있다."""
        from src.workflow.state_schema import StageBDetail

        b: StageBDetail = {
            "status": "completed",
            "pytest_pass": True,
            "shadow_duration_min": 10.5,
            "shadow_pnl": 21.10,
            "shadow_wr": 1.0,
            "shadow_crash": 0,
        }

        assert b["shadow_pnl"] == pytest.approx(21.10)
        assert b["shadow_crash"] == 0


# ---------------------------------------------------------------------------
# ConsistencyReport (state_schema.py 버전) 테스트
# ---------------------------------------------------------------------------


class TestConsistencyReportStateSchema:
    """state_schema.py의 ConsistencyReport 데이터클래스 동작 검증."""

    def test_기본_생성_시_passed가_True이다(self):
        """ConsistencyReport를 인자 없이 생성하면 passed=True이다."""
        from src.workflow.state_schema import ConsistencyReport

        report = ConsistencyReport()

        assert report.passed is True
        assert report.errors == []
        assert report.warnings == []
        assert report.checked_files == []

    def test_add_error_호출_시_passed가_False로_바뀐다(self):
        """add_error()를 호출하면 passed가 False로 변경되고 errors에 추가된다."""
        from src.workflow.state_schema import ConsistencyReport

        report = ConsistencyReport()
        report.add_error("테스트 에러 메시지")

        assert report.passed is False
        assert len(report.errors) == 1
        assert "테스트 에러 메시지" in report.errors[0]

    def test_add_warning_은_passed를_변경하지_않는다(self):
        """add_warning()을 호출해도 passed는 True를 유지한다."""
        from src.workflow.state_schema import ConsistencyReport

        report = ConsistencyReport()
        report.add_warning("경고 메시지")

        assert report.passed is True
        assert len(report.warnings) == 1

    def test_여러_에러_추가_시_모두_errors_리스트에_저장된다(self):
        """add_error()를 여러 번 호출하면 모두 errors 리스트에 누적된다."""
        from src.workflow.state_schema import ConsistencyReport

        report = ConsistencyReport()
        report.add_error("에러 1")
        report.add_error("에러 2")
        report.add_error("에러 3")

        assert len(report.errors) == 3
        assert report.passed is False

    def test_summary_문자열에_PASS_또는_FAIL이_포함된다(self):
        """에러 없으면 summary에 'PASS', 에러 있으면 'FAIL'이 포함된다."""
        from src.workflow.state_schema import ConsistencyReport

        ok_report = ConsistencyReport()
        fail_report = ConsistencyReport()
        fail_report.add_error("검사 실패")

        assert "PASS" in ok_report.summary()
        assert "FAIL" in fail_report.summary()


# ---------------------------------------------------------------------------
# JSON Schema 파일 존재 및 유효성 테스트
# ---------------------------------------------------------------------------


class TestJsonSchemaFiles:
    """schemas/ 디렉토리의 JSON Schema 파일 존재 및 유효성 검증."""

    @pytest.mark.skipif(
        not _PRD_SCHEMA.exists(),
        reason="prd_schema.json 파일이 존재하지 않아 스킵",
    )
    def test_prd_schema_json_파일이_존재하고_유효한_JSON이다(self):
        """prd_schema.json이 존재하고 유효한 JSON으로 파싱된다."""
        content = _PRD_SCHEMA.read_text(encoding="utf-8")
        schema = json.loads(content)

        assert isinstance(schema, dict), "스키마는 dict이어야 한다"
        assert "$schema" in schema or "type" in schema, "JSON Schema 구조여야 한다"

    @pytest.mark.skipif(
        not _STATE_SCHEMA.exists(),
        reason="state_schema.json 파일이 존재하지 않아 스킵",
    )
    def test_state_schema_json_파일이_존재하고_유효한_JSON이다(self):
        """state_schema.json이 존재하고 유효한 JSON으로 파싱된다."""
        content = _STATE_SCHEMA.read_text(encoding="utf-8")
        schema = json.loads(content)

        assert isinstance(schema, dict), "스키마는 dict이어야 한다"
        assert "properties" in schema, "state_schema에는 'properties' 키가 있어야 한다"

    @pytest.mark.skipif(
        not _PRD_SCHEMA.exists(),
        reason="prd_schema.json 파일이 존재하지 않아 스킵",
    )
    def test_prd_schema_json_필수_필드가_정의되어_있다(self):
        """prd_schema.json에 id, title, phase, priority, passes 필수 필드가 정의되어 있다."""
        schema = json.loads(_PRD_SCHEMA.read_text(encoding="utf-8"))

        required = schema.get("required", [])
        assert "id" in required, "prd_schema에 'id'가 required이어야 한다"
        assert "passes" in required, "prd_schema에 'passes'가 required이어야 한다"

    @pytest.mark.skipif(
        not _STATE_SCHEMA.exists(),
        reason="state_schema.json 파일이 존재하지 않아 스킵",
    )
    def test_state_schema_json_escalation_level_패턴이_정의되어_있다(self):
        """state_schema.json의 escalation_level 필드에 L0~L5 패턴이 정의되어 있다."""
        schema = json.loads(_STATE_SCHEMA.read_text(encoding="utf-8"))

        escalation = schema["properties"]["escalation_level"]
        assert "pattern" in escalation, "escalation_level에는 정규식 패턴이 있어야 한다"
        assert "L" in escalation["pattern"], "패턴에 'L'이 포함되어야 한다"


# ---------------------------------------------------------------------------
# jsonschema 검증 테스트
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _PRD_SCHEMA.exists(),
    reason="prd_schema.json 파일이 존재하지 않아 스킵",
)
class TestJsonSchemaValidation:
    """jsonschema 라이브러리를 사용한 스키마 검증 테스트."""

    @pytest.fixture(autouse=True)
    def _jsonschema_필요(self):
        """jsonschema 패키지가 없으면 테스트를 스킵한다."""
        pytest.importorskip("jsonschema")

    def test_유효한_user_story는_prd_schema_검증을_통과한다(self):
        """필수 필드를 모두 포함한 UserStory는 prd_schema.json 검증을 통과한다."""
        import jsonschema

        schema = json.loads(_PRD_SCHEMA.read_text(encoding="utf-8"))
        valid_story = {
            "id": "US-001",
            "title": "거래소 연결",
            "phase": "S13",
            "priority": "HIGH",
            "passes": True,
        }

        # 예외가 발생하지 않으면 통과
        jsonschema.validate(valid_story, schema)

    def test_id_필드가_누락된_user_story는_검증에_실패한다(self):
        """'id' 필드가 없는 UserStory는 jsonschema 검증에서 ValidationError가 발생한다."""
        import jsonschema

        schema = json.loads(_PRD_SCHEMA.read_text(encoding="utf-8"))
        invalid_story = {
            # id 누락
            "title": "id 없는 스토리",
            "phase": "S13",
            "priority": "HIGH",
            "passes": False,
        }

        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(invalid_story, schema)

    def test_passes_필드가_문자열인_경우_검증에_실패한다(self):
        """'passes' 필드에 불리언 대신 문자열을 넣으면 ValidationError가 발생한다."""
        import jsonschema

        schema = json.loads(_PRD_SCHEMA.read_text(encoding="utf-8"))
        invalid_story = {
            "id": "US-002",
            "title": "타입 오류 스토리",
            "phase": "A",
            "priority": "LOW",
            "passes": "true",  # 문자열 — 잘못된 타입
        }

        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(invalid_story, schema)

    def test_id_패턴이_잘못된_user_story는_검증에_실패한다(self):
        """'id'가 'US-NNN' 패턴을 따르지 않으면 ValidationError가 발생한다."""
        import jsonschema

        schema = json.loads(_PRD_SCHEMA.read_text(encoding="utf-8"))
        invalid_story = {
            "id": "WRONG-001",  # 패턴 위반
            "title": "패턴 위반 스토리",
            "phase": "A",
            "priority": 1,
            "passes": False,
        }

        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(invalid_story, schema)
