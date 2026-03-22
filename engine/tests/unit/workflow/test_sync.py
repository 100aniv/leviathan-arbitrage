"""Tests for engine/src/workflow/sync.py — Single-Write 동기화 CLI."""
import json
import pytest
from pathlib import Path
from unittest.mock import patch

from src.workflow.sync import WorkflowSync, SyncParams


@pytest.fixture
def sync_env(tmp_path):
    """동기화 테스트 환경 구성."""
    # State 디렉토리
    state_dir = tmp_path / ".omc" / "state"
    state_dir.mkdir(parents=True)

    # 초기 state 파일
    (state_dir / "leviathan-active-phase.json").write_text(
        '{"current_phase": "S21", "phase": "S21", "status": "in_progress"}'
    )
    (state_dir / "leviathan-current-stage.json").write_text(
        '{"phase": "S21", "stage": "A", "step": "A-Step1"}'
    )
    (state_dir / "leviathan-progress.json").write_text(json.dumps({
        "current_phase": "S21", "current_stage": "A",
        "current_step": "entry", "status": "in_progress",
        "us_targets": [], "us_count": 0,
    }))

    # SSOT.md with markers
    ssot = tmp_path / "SSOT.md"
    ssot.write_text(
        "# LEVIATHAN SSOT\n\n"
        "**Phase**: <!-- SYNC:PHASE -->S21 (in_progress)<!-- /SYNC:PHASE -->\n"
        "**Tests**: <!-- SYNC:TESTS -->5,000 passed / 0 failed / 12 skipped<!-- /SYNC:TESTS -->\n"
        "**PRD**: <!-- SYNC:PRD -->300/315 passes:true<!-- /SYNC:PRD -->\n"
    )

    # CLAUDE.md with markers
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    claude_md = claude_dir / "CLAUDE.md"
    claude_md.write_text(
        "# CLAUDE.md\n\n"
        "**Phase**: <!-- SYNC:PHASE -->S21 (in_progress)<!-- /SYNC:PHASE -->\n"
        "**Tests**: <!-- SYNC:TESTS -->5,000 passed / 0 failed / 12 skipped<!-- /SYNC:TESTS -->\n"
        "**PRD**: <!-- SYNC:PRD -->300/315 passes:true<!-- /SYNC:PRD -->\n"
    )

    return tmp_path


class TestWorkflowSync:
    """동기화 테스트."""

    def test_sync_updates_all_state_files(self, sync_env):
        """sync가 3개 state JSON 파일을 모두 업데이트하는지."""
        ws = WorkflowSync(root=sync_env)
        params = SyncParams(phase="TF-QF", stage="C", step="review", status="tf-entry")
        ok, msg = ws.sync(params)

        assert ok, msg

        ap = json.loads((sync_env / ".omc/state/leviathan-active-phase.json").read_text())
        cs = json.loads((sync_env / ".omc/state/leviathan-current-stage.json").read_text())
        pg = json.loads((sync_env / ".omc/state/leviathan-progress.json").read_text())

        assert ap["current_phase"] == "TF-QF"
        assert cs["phase"] == "TF-QF"
        assert pg["current_phase"] == "TF-QF"
        assert cs["stage"] == "C"

    def test_sync_updates_ssot_markers(self, sync_env):
        """sync가 SSOT.md 마커를 치환하는지."""
        ws = WorkflowSync(root=sync_env)
        params = SyncParams(
            phase="TF-QF", tests_passed=5242, tests_failed=0,
            prd_pass=313, prd_total=315,
        )
        ok, msg = ws.sync(params)
        assert ok, msg

        ssot = (sync_env / "SSOT.md").read_text()
        assert "TF-QF" in ssot
        assert "5,242 passed" in ssot
        assert "313/315" in ssot

    def test_sync_updates_claude_md_markers(self, sync_env):
        """sync가 CLAUDE.md 마커를 치환하는지."""
        ws = WorkflowSync(root=sync_env)
        params = SyncParams(
            phase="TF-SF", tests_passed=5300, prd_pass=314, prd_total=315,
        )
        ok, msg = ws.sync(params)
        assert ok, msg

        claude = (sync_env / ".claude/CLAUDE.md").read_text()
        assert "TF-SF" in claude
        assert "5,300 passed" in claude
        assert "314/315" in claude

    def test_sync_rollback_on_phase_mismatch(self, sync_env):
        """검증 실패 시 롤백하는지."""
        ws = WorkflowSync(root=sync_env)

        # 의도적으로 active-phase만 직접 수정하여 불일치 유발
        params = SyncParams(phase="TF-QF")
        # Monkey-patch: update_progress에서 다른 phase 기록
        original = ws._update_progress

        def broken_progress(p):
            original(p)
            # 의도적 불일치
            path = sync_env / ".omc/state/leviathan-progress.json"
            data = json.loads(path.read_text())
            data["current_phase"] = "BROKEN"
            path.write_text(json.dumps(data))

        ws._update_progress = broken_progress
        ok, msg = ws.sync(params)

        assert not ok
        assert "롤백" in msg

        # 원본 복원 확인
        ap = json.loads((sync_env / ".omc/state/leviathan-active-phase.json").read_text())
        assert ap["current_phase"] == "S21"  # 원래 값 복원

    def test_sync_preserves_existing_progress_fields(self, sync_env):
        """sync가 progress.json의 기존 필드(us_targets 등)를 보존하는지."""
        ws = WorkflowSync(root=sync_env)
        params = SyncParams(phase="TF-QF")
        ok, msg = ws.sync(params)
        assert ok, msg

        pg = json.loads((sync_env / ".omc/state/leviathan-progress.json").read_text())
        assert pg["us_targets"] == []  # 기존 필드 보존
        assert pg["us_count"] == 0

    def test_sync_without_optional_params(self, sync_env):
        """tests/prd 없이 phase만으로 동기화."""
        ws = WorkflowSync(root=sync_env)
        params = SyncParams(phase="TF-PF")
        ok, msg = ws.sync(params)
        assert ok, msg

        ap = json.loads((sync_env / ".omc/state/leviathan-active-phase.json").read_text())
        assert ap["current_phase"] == "TF-PF"
        assert "prd" not in ap  # prd 파라미터 없으면 prd 키 미포함

    def test_sync_no_marker_graceful(self, sync_env):
        """마커 없는 파일에서 에러 없이 무시하는지."""
        # 마커 제거
        ssot = sync_env / "SSOT.md"
        ssot.write_text("# No markers here\nJust text.")

        ws = WorkflowSync(root=sync_env)
        params = SyncParams(phase="TF-QF", tests_passed=5242)
        ok, msg = ws.sync(params)
        assert ok, msg  # 마커 없어도 state 파일은 업데이트됨


class TestFSM:
    """FSM 테스트 (import 확인용)."""

    def test_fsm_import(self):
        from src.workflow.fsm import WorkflowFSM, TRANSITIONS, InvalidTransition
        assert len(TRANSITIONS) > 10

    def test_fsm_basic_transition(self, tmp_path):
        from src.workflow.fsm import WorkflowFSM
        fsm = WorkflowFSM(root=tmp_path)
        assert fsm.current_state == "A"
        new = fsm.transition("entry_gate_pass")
        assert new == "A_plan"

    def test_fsm_invalid_transition(self, tmp_path):
        from src.workflow.fsm import WorkflowFSM, InvalidTransition
        fsm = WorkflowFSM(root=tmp_path)
        with pytest.raises(InvalidTransition):
            fsm.transition("shadow_pass")  # A 상태에서 shadow_pass 불가

    def test_fsm_full_cycle(self, tmp_path):
        """A → B → C → NEXT_PHASE 전체 사이클 (v2 단순화)."""
        from src.workflow.fsm import WorkflowFSM
        fsm = WorkflowFSM(root=tmp_path)
        fsm.transition("entry_gate_pass")  # A -> A_plan
        fsm.transition("plan_approved")    # A_plan -> B
        fsm.transition("build_complete")   # B -> B_test
        fsm.transition("pytest_pass")      # B_test -> B_shadow
        fsm.transition("shadow_pass")      # B_shadow -> C
        fsm.transition("assembly_pass")    # C -> C_review
        fsm.transition("review_pass")      # C_review -> C_release
        result = fsm.transition("pushed")  # C_release -> NEXT_PHASE
        assert result == "NEXT_PHASE"
