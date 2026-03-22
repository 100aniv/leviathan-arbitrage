"""LEVIATHAN Workflow FSM — 결정론적 Stage 전환 상태 머신.

Usage::
    python -m src.workflow.cli transition shadow_pass
    python -m src.workflow.cli transition entry_gate_pass

잘못된 전환 시도를 차단하여 워크플로우 무결성 보장.
단순화 (v2): 19→15상태. A_quant, C_go, TF_PF, ESCALATE_L2 제거.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)


class InvalidTransition(Exception):
    """현재 상태에서 허용되지 않는 전환."""
    pass


# (current_state, event) -> next_state
TRANSITIONS: dict[tuple[str, str], str] = {
    # Stage A (기획) — architect + critic
    ("A", "entry_gate_pass"): "A_plan",
    ("A_plan", "plan_approved"): "B",
    ("A_plan", "plan_rejected"): "A",  # 재기획
    # Stage B (구현 + 검증)
    ("B", "build_complete"): "B_test",
    ("B_test", "pytest_pass"): "B_shadow",
    ("B_test", "pytest_fail"): "B",  # 수정 후 재빌드
    ("B_shadow", "shadow_pass"): "C",
    ("B_shadow", "shadow_fail_type_w"): "B",  # trades=0 → fix
    ("B_shadow", "shadow_fail_type_p"): "B_fix",
    ("B_shadow", "shadow_fail_type_b"): "B_fix",
    ("B_fix", "fix_complete"): "B_test",
    # Stage C (리뷰 + 릴리스)
    ("C", "assembly_pass"): "C_review",
    ("C", "assembly_fail"): "B",  # Stage B 복귀
    ("C_review", "review_pass"): "C_release",
    ("C_review", "review_fail"): "B",  # CRITICAL/HIGH → Stage B 복귀
    ("C_release", "pushed"): "NEXT_PHASE",
    # TF 관련 (3-Round: QF→SF→Final, PF 제거)
    ("TF_QF", "qf_pass"): "TF_SF",
    ("TF_QF", "qf_fail"): "REGRESSION",
    ("TF_SF", "sf_pass"): "TF_FINAL",
    ("TF_SF", "sf_fail"): "REGRESSION",
    ("TF_FINAL", "final_pass"): "LIVE",
    ("TF_FINAL", "final_fail"): "TF_SF",  # 코드 변경 시 SF부터
    ("REGRESSION", "regression_complete"): "TF_QF",
}

# 사람이 읽을 수 있는 상태 설명
STATE_LABELS: dict[str, str] = {
    "A": "Stage A: Entry Gate",
    "A_plan": "Stage A: Plan + Review",
    "B": "Stage B: Build",
    "B_test": "Stage B: pytest",
    "B_shadow": "Stage B: Shadow 10min",
    "B_fix": "Stage B: Fix Loop",
    "C": "Stage C: Assembly Gate",
    "C_review": "Stage C: Code Review",
    "C_release": "Stage C: SSOT + Git Push",
    "NEXT_PHASE": "다음 Phase 진입",
    "TF_QF": "TF Quarter-Final",
    "TF_SF": "TF Semi-Final",
    "TF_FINAL": "TF Final",
    "LIVE": "Live 운영",
    "REGRESSION": "회귀 Phase",
}


class WorkflowFSM:
    """결정론적 Stage 전환 상태 머신."""

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = root or Path(".")
        self._state_path = self.root / ".omc" / "state" / "leviathan-fsm-state.json"
        self._current = self._load_state()

    @property
    def current_state(self) -> str:
        return self._current

    def can_transition(self, event: str) -> bool:
        """현재 상태에서 이벤트로 전환 가능한지 확인."""
        return (self._current, event) in TRANSITIONS

    def transition(self, event: str) -> str:
        """상태 전환 실행. 불가능하면 InvalidTransition 발생."""
        key = (self._current, event)
        if key not in TRANSITIONS:
            allowed = [e for (s, e) in TRANSITIONS if s == self._current]
            raise InvalidTransition(
                f"'{self._current}' 상태에서 '{event}' 이벤트 불가. "
                f"허용된 이벤트: {allowed}"
            )
        old = self._current
        self._current = TRANSITIONS[key]
        self._save_state()
        logger.info(
            "fsm_transition",
            old_state=old,
            trigger=event,
            new_state=self._current,
            label=STATE_LABELS.get(self._current, self._current),
        )
        return self._current

    def set_state(self, state: str) -> None:
        """강제 상태 설정 (복구/초기화용)."""
        self._current = state
        self._save_state()
        logger.info("fsm_state_set", state=state)

    def get_allowed_events(self) -> list[str]:
        """현재 상태에서 허용된 이벤트 목록."""
        return [e for (s, e) in TRANSITIONS if s == self._current]

    def _load_state(self) -> str:
        """FSM 상태 파일에서 로드. 없으면 'A'."""
        if self._state_path.exists():
            try:
                data = json.loads(self._state_path.read_text(encoding="utf-8"))
                return data.get("state", "A")
            except (json.JSONDecodeError, OSError):
                pass
        return "A"

    def _save_state(self) -> None:
        """FSM 상태 파일에 저장."""
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"state": self._current, "label": STATE_LABELS.get(self._current, "")}
        self._state_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
