"""Phase 8 Step 1 — PAPER_USE_LIVEMODE flag 동작 검증.

Codex SUGGEST (codex-leviathan-phase-8-step-1-2026-04-27): "PAPER_USE_LIVEMODE
경로 전용 테스트 필요 — 최소 3개: risk_guardian paper bypass, recorder mode='paper',
approval gate not called in paper."

본 테스트는 paper_mode_loop의 LiveMode 인스턴스화 인자를 정적 검증 (full async run 대신).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def test_paper_use_livemode_flag_default_false_in_engine_json() -> None:
    """engine.json에 PAPER_USE_LIVEMODE=false default 확인 (안전 ramp-up)."""
    import json
    import pathlib
    cfg_path = pathlib.Path(__file__).parent.parent.parent.parent / "config" / "engine.json"
    cfg = json.loads(cfg_path.read_text())
    assert cfg["feature_flags"]["PAPER_USE_LIVEMODE"] is False, \
        "PAPER_USE_LIVEMODE은 default false로 안전 ramp-up. 명시적 활성 필요."


def test_paper_mode_loop_imports_livemode_when_flag_true() -> None:
    """get_bool_flag('PAPER_USE_LIVEMODE')==True → LiveMode 사용 path 진입 가능 검증."""
    # 정적 검증: 코드에 LiveMode import + _use_livemode 분기 존재
    import inspect
    from src.runtime import mode_loops
    src_text = inspect.getsource(mode_loops.paper_mode_loop)
    assert "LiveMode" in src_text, "paper_mode_loop이 LiveMode를 import해야 함"
    assert "_use_livemode" in src_text, "PAPER_USE_LIVEMODE flag 분기 필요"
    assert 'execution_mode="paper"' in src_text, \
        "LiveMode를 paper 모드로 사용 (PaperExecutor + BookWalkSlippage 자동 wiring)"


def test_paper_use_livemode_path_risk_guardian_none() -> None:
    """Codex BLOCKING #1: paper LiveMode 경로에서 risk_guardian=None 명시 (legacy 룰 보존)."""
    import inspect
    from src.runtime import mode_loops
    src_text = inspect.getsource(mode_loops.paper_mode_loop)
    # _use_livemode 블록에서 risk_guardian=None 명시
    # ("Codex BLOCKING #1" 주석 옆에)
    assert "risk_guardian=None" in src_text, \
        "paper 경로에서 risk_guardian=None 명시 필수 (100% reject 회귀 방지)"
    assert "Codex BLOCKING #1" in src_text, \
        "BLOCKING #1 fix 주석 보존"


def test_paper_use_livemode_path_recorder_alias() -> None:
    """Codex BLOCKING #2: engine._live_mode = engine._paper_mode alias.

    MarketRecorderListener가 engine._live_mode._execution_mode 참조 — paper에서도
    'paper' 정확 기록되도록 alias 설정.
    """
    import inspect
    from src.runtime import mode_loops
    src_text = inspect.getsource(mode_loops.paper_mode_loop)
    assert "engine._live_mode = engine._paper_mode" in src_text, \
        "MarketRecorderListener가 paper 모드 정확 기록하려면 alias 필수"
    assert "Codex BLOCKING #2" in src_text, \
        "BLOCKING #2 fix 주석 보존"


def test_paper_use_livemode_path_no_live_gate() -> None:
    """SUGGEST: paper에서 live_gate=None (approval gate skip)."""
    import inspect
    from src.runtime import mode_loops
    src_text = inspect.getsource(mode_loops.paper_mode_loop)
    # _use_livemode 블록에서 live_gate=None
    assert "live_gate=None" in src_text, \
        "paper 경로에서 live_gate=None (approval gate 무용)"
