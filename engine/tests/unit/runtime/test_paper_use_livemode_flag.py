"""Phase 8 Step 3 — paper_mode_loop 단일 배관 검증 (ShadowMode 폐기 후).

Phase 8 history:
- Step 1 (a4eb86b): PAPER_USE_LIVEMODE flag 도입 (ramp-up)
- Step 2 (34f734a): flag=true 활성 + paper smoke 검증
- Step 3 (this commit): ShadowMode 폐기 + PAPER_USE_LIVEMODE flag 제거 (항상 LiveMode)

Codex BLOCKING fix는 Step 1 commit 55f3629에서 보존:
- risk_guardian=None
- live_gate=None
- engine._live_mode = engine._paper_mode alias
"""
from __future__ import annotations


def test_paper_mode_loop_always_uses_livemode() -> None:
    """Phase 8 Step 4 후: paper_mode_loop이 _build_livemode_runner helper 호출.

    LiveMode 인스턴스화는 helper로 추출됨 (사장님 메모리 정합 — 단일 빌더).
    """
    import inspect
    from src.runtime import mode_loops
    src_text = inspect.getsource(mode_loops.paper_mode_loop)
    # helper 호출 검증 (Phase 8 Step 4)
    assert "_build_livemode_runner(" in src_text, \
        "paper_mode_loop은 _build_livemode_runner helper 호출 (단일 빌더)"
    assert 'execution_mode="paper"' in src_text, \
        "LiveMode를 paper 모드로 사용 (PaperExecutor + BookWalkSlippage 자동 wiring)"
    # helper 자체가 LiveMode를 import + 인스턴스화 (단일 source)
    helper_src = inspect.getsource(mode_loops._build_livemode_runner)
    assert "from src.modes.live import LiveMode" in helper_src, \
        "helper에서 LiveMode import"
    assert "return LiveMode(" in helper_src, \
        "helper에서 LiveMode 인스턴스 생성"


def test_paper_mode_loop_no_shadowmode_branch() -> None:
    """Phase 8 Step 3: ShadowMode 인스턴스 생성 분기 폐기 검증.

    docstring/주석에는 "ShadowMode 폐기" 같은 history 언급 가능 — function call만 체크.
    """
    import inspect
    from src.runtime import mode_loops
    src_text = inspect.getsource(mode_loops.paper_mode_loop)
    # 핵심: ShadowMode( 함수 호출 패턴 없어야 함 (인스턴스 생성 차단)
    assert "ShadowMode(" not in src_text, \
        "ShadowMode 인스턴스 생성 분기 제거됨 (Phase 8 Step 3)"
    # 핵심: get_bool_flag("PAPER_USE_LIVEMODE") 호출 없어야 함 (flag 자체 사용 안 함)
    assert 'get_bool_flag("PAPER_USE_LIVEMODE")' not in src_text, \
        "PAPER_USE_LIVEMODE flag 호출 제거됨 (LiveMode 항상 사용)"


def test_paper_use_livemode_flag_removed_from_engine_json() -> None:
    """Phase 8 Step 3: engine.json에서 PAPER_USE_LIVEMODE flag 제거 (LiveMode 항상 활성)."""
    import json
    import pathlib
    cfg_path = pathlib.Path(__file__).parent.parent.parent.parent / "config" / "engine.json"
    cfg = json.loads(cfg_path.read_text())
    assert "PAPER_USE_LIVEMODE" not in cfg["feature_flags"], \
        "PAPER_USE_LIVEMODE flag는 Phase 8 Step 3에서 제거됨 (LiveMode 항상 사용)"


def test_paper_use_livemode_path_risk_guardian_none() -> None:
    """Codex BLOCKING #1 fix 보존: helper에서 paper일 때 risk_guardian=None.

    Phase 8 Step 4 후: 분기 로직은 _build_livemode_runner helper로 이동.
    """
    import inspect
    from src.runtime import mode_loops
    helper_src = inspect.getsource(mode_loops._build_livemode_runner)
    assert "risk_guardian=None if is_paper else engine._risk_guardian" in helper_src, \
        "helper에서 paper일 때 risk_guardian=None (100% reject 회귀 방지)"


def test_paper_use_livemode_path_recorder_alias() -> None:
    """Codex BLOCKING #2 fix 보존: engine._live_mode = engine._paper_mode alias."""
    import inspect
    from src.runtime import mode_loops
    src_text = inspect.getsource(mode_loops.paper_mode_loop)
    assert "engine._live_mode = engine._paper_mode" in src_text, \
        "MarketRecorderListener가 paper 모드 정확 기록하려면 alias 필수"


def test_paper_use_livemode_path_no_live_gate() -> None:
    """Codex SUGGEST 보존: helper에서 paper일 때 live_gate=None (approval gate skip)."""
    import inspect
    from src.runtime import mode_loops
    helper_src = inspect.getsource(mode_loops._build_livemode_runner)
    assert "live_gate=None if is_paper" in helper_src, \
        "helper에서 paper일 때 live_gate=None (approval gate 무용)"
