"""LEVIATHAN 워크플로우 체크포인트 엔진 테스트.

대상 모듈: engine/src/workflow/checkpoint_engine.py
검증 항목:
  - WorkflowCheckpointer `:memory:` 초기화
  - save() 체크포인트 저장 및 ID 반환
  - restore_latest() 최신 체크포인트 복원
  - restore_latest(phase=...) 필터 복원
  - get_checkpoint() 특정 ID 조회
  - get_checkpoint() 없는 ID → None 반환
  - list_history() 이력 조회 및 limit 제한
  - prune() 오래된 체크포인트 정리
  - context manager (__enter__ / __exit__) 사용
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# 공통 픽스처
# ---------------------------------------------------------------------------


@pytest.fixture
def 인메모리_체크포인터():
    """격리된 `:memory:` SQLite WorkflowCheckpointer 인스턴스를 반환한다."""
    from src.workflow.checkpoint_engine import WorkflowCheckpointer

    cp = WorkflowCheckpointer(db_path=":memory:")
    yield cp
    cp.close()


def _기본_상태(phase: str = "S13", stage: str = "A") -> dict:
    """테스트용 최소 워크플로우 상태 딕셔너리."""
    return {
        "phase": phase,
        "stage": stage,
        "prd_pass_count": 209,
        "prd_total_count": 216,
        "test_count": 4695,
    }


# ---------------------------------------------------------------------------
# 초기화 테스트
# ---------------------------------------------------------------------------


class TestWorkflowCheckpointerInit:
    """WorkflowCheckpointer 초기화 동작 검증."""

    def test_인메모리_데이터베이스로_초기화_성공(self):
        """:memory: 경로로 WorkflowCheckpointer를 생성하면 예외 없이 초기화된다."""
        from src.workflow.checkpoint_engine import WorkflowCheckpointer

        cp = WorkflowCheckpointer(db_path=":memory:")
        assert cp.conn is not None, "SQLite 연결이 존재해야 한다"
        cp.close()

    def test_초기화_후_checkpoints_테이블이_생성된다(self):
        """초기화 직후 checkpoints 테이블이 SQLite에 생성되어 있어야 한다."""
        from src.workflow.checkpoint_engine import WorkflowCheckpointer

        cp = WorkflowCheckpointer(db_path=":memory:")
        cursor = cp.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='checkpoints'"
        )
        row = cursor.fetchone()
        cp.close()

        assert row is not None, "checkpoints 테이블이 존재해야 한다"

    def test_파일_기반_데이터베이스로_초기화_성공(self, tmp_path):
        """tmp_path 경로의 파일 DB로 WorkflowCheckpointer를 생성할 수 있다."""
        from src.workflow.checkpoint_engine import WorkflowCheckpointer

        db_file = tmp_path / "test_checkpoints.db"
        cp = WorkflowCheckpointer(db_path=str(db_file))
        cp.close()

        assert db_file.exists(), "DB 파일이 생성되어야 한다"


# ---------------------------------------------------------------------------
# save() 테스트
# ---------------------------------------------------------------------------


class TestSave:
    """save() 메서드 동작 검증."""

    def test_save_호출_시_문자열_ID를_반환한다(self, 인메모리_체크포인터):
        """save()를 호출하면 비어 있지 않은 문자열 ID를 반환한다."""
        cp_id = 인메모리_체크포인터.save(_기본_상태(), trigger="테스트_저장")

        assert isinstance(cp_id, str), "반환값은 문자열이어야 한다"
        assert len(cp_id) > 0, "반환된 ID는 비어 있지 않아야 한다"

    def test_save_ID에_phase와_stage가_포함된다(self, 인메모리_체크포인터):
        """save()가 반환하는 ID에는 phase와 stage 값이 포함되어야 한다."""
        cp_id = 인메모리_체크포인터.save(
            _기본_상태(phase="S13", stage="B"),
            trigger="stage_B_start",
        )

        assert "S13" in cp_id, f"ID에 phase 'S13'이 포함되어야 한다: {cp_id}"
        assert "B" in cp_id, f"ID에 stage 'B'가 포함되어야 한다: {cp_id}"

    def test_여러_번_save_호출_시_각각_다른_ID가_생성된다(self, 인메모리_체크포인터):
        """save()를 여러 번 호출하면 각각 고유한 ID를 반환한다.

        save()의 ID는 초(秒) 단위 타임스탬프 기반이므로, 서로 다른 phase/stage
        조합을 사용하여 ID 고유성을 검증한다.
        """
        id1 = 인메모리_체크포인터.save(_기본_상태(phase="S12", stage="A"), trigger="첫_번째")
        id2 = 인메모리_체크포인터.save(_기본_상태(phase="S13", stage="B"), trigger="두_번째")

        assert id1 != id2, "phase/stage가 다르면 ID가 달라야 한다"

    def test_save_후_DB에_1건이_저장된다(self, 인메모리_체크포인터):
        """save() 후 checkpoints 테이블에 정확히 1건이 저장되어야 한다."""
        인메모리_체크포인터.save(_기본_상태(), trigger="단건_저장")

        count = 인메모리_체크포인터.conn.execute(
            "SELECT COUNT(*) FROM checkpoints"
        ).fetchone()[0]

        assert count == 1, f"저장 후 1건이어야 하지만 {count}건"


# ---------------------------------------------------------------------------
# restore_latest() 테스트
# ---------------------------------------------------------------------------


class TestRestoreLatest:
    """restore_latest() 메서드 동작 검증."""

    def test_저장_후_restore_latest가_상태_딕셔너리를_반환한다(self, 인메모리_체크포인터):
        """save() 후 restore_latest()를 호출하면 상태 딕셔너리를 반환한다."""
        원본 = _기본_상태(phase="S13", stage="A")
        인메모리_체크포인터.save(원본, trigger="복원_테스트")

        복원 = 인메모리_체크포인터.restore_latest()

        assert 복원 is not None, "저장 후 restore_latest()는 None을 반환하지 않아야 한다"
        assert 복원["phase"] == "S13"
        assert 복원["stage"] == "A"

    def test_복원된_상태에_restored_from_키가_추가된다(self, 인메모리_체크포인터):
        """restore_latest()가 반환하는 상태에는 '_restored_from' 키가 포함되어야 한다."""
        인메모리_체크포인터.save(_기본_상태(), trigger="복원_키_테스트")

        복원 = 인메모리_체크포인터.restore_latest()

        assert "_restored_from" in 복원, "복원된 상태에 '_restored_from' 키가 있어야 한다"

    def test_데이터_없을_때_restore_latest는_None을_반환한다(self, 인메모리_체크포인터):
        """체크포인트가 없는 상태에서 restore_latest()는 None을 반환해야 한다."""
        결과 = 인메모리_체크포인터.restore_latest()

        assert 결과 is None, "데이터 없을 때 None이어야 한다"

    def test_restore_latest_phase_필터로_해당_phase_최신_복원(self, 인메모리_체크포인터):
        """restore_latest(phase='S12')는 S12 phase의 최신 체크포인트만 복원한다."""
        import time

        인메모리_체크포인터.save(_기본_상태(phase="S12", stage="C"), trigger="s12_완료")
        time.sleep(0.01)
        인메모리_체크포인터.save(_기본_상태(phase="S13", stage="A"), trigger="s13_시작")

        복원_s12 = 인메모리_체크포인터.restore_latest(phase="S12")

        assert 복원_s12 is not None, "S12 체크포인트를 찾아야 한다"
        assert 복원_s12["phase"] == "S12", f"복원된 phase는 'S12'여야 한다: {복원_s12['phase']}"

    def test_restore_latest_존재하지_않는_phase_필터는_None_반환(self, 인메모리_체크포인터):
        """없는 phase로 restore_latest() 필터링 시 None을 반환한다."""
        인메모리_체크포인터.save(_기본_상태(phase="S13"), trigger="저장")

        결과 = 인메모리_체크포인터.restore_latest(phase="ZZ99")

        assert 결과 is None, "없는 phase에 대해 None이어야 한다"


# ---------------------------------------------------------------------------
# get_checkpoint() 테스트
# ---------------------------------------------------------------------------


class TestGetCheckpoint:
    """get_checkpoint() 메서드 동작 검증."""

    def test_저장된_ID로_특정_체크포인트를_조회한다(self, 인메모리_체크포인터):
        """save()가 반환한 ID로 get_checkpoint()를 호출하면 해당 상태를 반환한다."""
        원본 = _기본_상태(phase="S13", stage="B")
        cp_id = 인메모리_체크포인터.save(원본, trigger="특정_조회_테스트")

        조회 = 인메모리_체크포인터.get_checkpoint(cp_id)

        assert 조회 is not None, "저장된 ID로 조회하면 결과가 있어야 한다"
        assert 조회["phase"] == "S13"
        assert 조회["stage"] == "B"

    def test_존재하지_않는_ID로_조회_시_None을_반환한다(self, 인메모리_체크포인터):
        """존재하지 않는 ID로 get_checkpoint()를 호출하면 None을 반환해야 한다."""
        결과 = 인메모리_체크포인터.get_checkpoint("없는_체크포인트_ID_9999")

        assert 결과 is None, "없는 ID에 대해 None이어야 한다"

    def test_저장된_prd_카운트_값이_복원된다(self, 인메모리_체크포인터):
        """save()에 전달한 prd_pass_count 값이 get_checkpoint()로 복원된다."""
        원본 = _기본_상태()
        원본["prd_pass_count"] = 123
        cp_id = 인메모리_체크포인터.save(원본, trigger="prd_수_확인")

        복원 = 인메모리_체크포인터.get_checkpoint(cp_id)

        assert 복원["prd_pass_count"] == 123, "prd_pass_count가 복원되어야 한다"


# ---------------------------------------------------------------------------
# list_history() 테스트
# ---------------------------------------------------------------------------


class TestListHistory:
    """list_history() 메서드 동작 검증."""

    def test_빈_DB에서_list_history는_빈_리스트를_반환한다(self, 인메모리_체크포인터):
        """체크포인트가 없을 때 list_history()는 빈 리스트를 반환한다."""
        결과 = 인메모리_체크포인터.list_history()

        assert 결과 == [], "빈 DB에서 이력은 빈 리스트여야 한다"

    def test_저장한_수만큼_이력이_반환된다(self, 인메모리_체크포인터):
        """3건 저장 후 list_history()는 3건의 이력을 반환한다."""
        import time

        for i in range(3):
            인메모리_체크포인터.save(_기본_상태(stage=["A", "B", "C"][i]), trigger=f"저장_{i}")
            time.sleep(0.01)

        이력 = 인메모리_체크포인터.list_history()

        assert len(이력) == 3, f"3건 저장 후 이력은 3건이어야 한다: {len(이력)}"

    def test_list_history_limit_파라미터가_결과_수를_제한한다(self, 인메모리_체크포인터):
        """limit=2로 list_history()를 호출하면 최대 2건만 반환한다.

        INSERT OR REPLACE 충돌을 피하기 위해 각 저장마다 다른 phase를 사용한다.
        """
        단계 = ["A", "B", "C"]
        for i in range(5):
            # phase를 다르게 해서 ID 충돌 방지 (ID = phase_stage_타임스탬프)
            인메모리_체크포인터.save(
                _기본_상태(phase=f"T{i:02d}", stage="A"),
                trigger=f"이력_{i}",
            )

        이력 = 인메모리_체크포인터.list_history(limit=2)

        assert len(이력) == 2, f"limit=2이면 2건만 반환해야 한다: {len(이력)}"

    def test_list_history_반환_항목에_필수_키가_포함된다(self, 인메모리_체크포인터):
        """list_history() 각 항목에는 id, phase, stage, trigger, created_at이 있어야 한다."""
        인메모리_체크포인터.save(_기본_상태(), trigger="필드_확인")

        이력 = 인메모리_체크포인터.list_history()

        항목 = 이력[0]
        for key in ("id", "phase", "stage", "trigger", "created_at"):
            assert key in 항목, f"이력 항목에 '{key}' 키가 있어야 한다"

    def test_list_history_phase_필터로_해당_phase만_반환한다(self, 인메모리_체크포인터):
        """phase='S12' 필터로 list_history()를 호출하면 S12 체크포인트만 반환한다."""
        import time

        인메모리_체크포인터.save(_기본_상태(phase="S12"), trigger="s12")
        time.sleep(0.01)
        인메모리_체크포인터.save(_기본_상태(phase="S13"), trigger="s13")

        이력 = 인메모리_체크포인터.list_history(phase="S12")

        assert all(h["phase"] == "S12" for h in 이력), "S12 필터 결과에 다른 phase가 포함되면 안 된다"


# ---------------------------------------------------------------------------
# prune() 테스트
# ---------------------------------------------------------------------------


class TestPrune:
    """prune() 메서드 동작 검증."""

    def test_prune_keep_last_초과_항목이_삭제된다(self, 인메모리_체크포인터):
        """5건 저장 후 prune(keep_last=3) 호출 시 3건만 남아야 한다.

        INSERT OR REPLACE 충돌을 피하기 위해 phase를 다르게 지정한다.
        """
        for i in range(5):
            인메모리_체크포인터.save(
                _기본_상태(phase=f"P{i:02d}", stage="A"),
                trigger=f"정리_대상_{i}",
            )

        인메모리_체크포인터.prune(keep_last=3)

        남은_수 = 인메모리_체크포인터.conn.execute(
            "SELECT COUNT(*) FROM checkpoints"
        ).fetchone()[0]

        assert 남은_수 == 3, f"prune 후 3건만 남아야 한다: {남은_수}"

    def test_prune_keep_last가_전체_수보다_크면_삭제_없음(self, 인메모리_체크포인터):
        """3건 저장 후 prune(keep_last=10) 호출 시 3건이 그대로 유지된다.

        INSERT OR REPLACE 충돌을 피하기 위해 phase를 다르게 지정한다.
        """
        for i in range(3):
            인메모리_체크포인터.save(
                _기본_상태(phase=f"Q{i:02d}", stage="A"),
                trigger=f"유지_{i}",
            )

        인메모리_체크포인터.prune(keep_last=10)

        남은_수 = 인메모리_체크포인터.conn.execute(
            "SELECT COUNT(*) FROM checkpoints"
        ).fetchone()[0]

        assert 남은_수 == 3, f"keep_last가 전체보다 크면 아무것도 삭제되지 않아야 한다: {남은_수}"


# ---------------------------------------------------------------------------
# Context Manager 테스트
# ---------------------------------------------------------------------------


class TestContextManager:
    """WorkflowCheckpointer의 context manager 동작 검증."""

    def test_with_구문으로_사용_후_연결이_닫힌다(self):
        """with 구문 종료 후 SQLite 연결이 닫혀야 한다 (추가 호출 시 에러)."""
        import sqlite3
        from src.workflow.checkpoint_engine import WorkflowCheckpointer

        with WorkflowCheckpointer(db_path=":memory:") as cp:
            cp_id = cp.save(_기본_상태(), trigger="컨텍스트_매니저_테스트")
            assert isinstance(cp_id, str), "with 블록 내에서 save()가 동작해야 한다"

        # 연결 종료 후 사용 시 예외 발생 확인
        with pytest.raises(Exception):
            cp.conn.execute("SELECT 1")

    def test_with_구문_내_예외_발생_시에도_연결이_닫힌다(self):
        """with 블록 내 예외가 발생해도 __exit__이 호출되어 연결이 정리된다."""
        import sqlite3
        from src.workflow.checkpoint_engine import WorkflowCheckpointer

        cp_ref = None
        try:
            with WorkflowCheckpointer(db_path=":memory:") as cp:
                cp_ref = cp
                raise ValueError("의도적 예외")
        except ValueError:
            pass

        # 연결 종료 후 사용 시 예외 발생 확인
        with pytest.raises(Exception):
            cp_ref.conn.execute("SELECT 1")
