"""LEVIATHAN 워크플로우 자동화 CLI.

사용법:
    python -m src.workflow.cli check_all          # 전체 일관성 검사 실행
    python -m src.workflow.cli checkpoint save     # 현재 상태 체크포인트 저장
    python -m src.workflow.cli checkpoint restore  # 마지막 체크포인트 복원
    python -m src.workflow.cli checkpoint history  # 체크포인트 이력 조회

종료 코드:
    0 - 모든 검사 OK (또는 체크포인트 작업 성공)
    1 - DRIFT 감지 (check_all)
    2 - ERROR 감지 (check_all 또는 체크포인트 작업 실패)
"""
import argparse
import json
import sys
from pathlib import Path


def _find_project_root() -> Path:
    """현재 디렉토리에서 위로 올라가며 프로젝트 루트 찾기 (SSOT.md가 있는 곳)."""
    candidate = Path.cwd()
    for _ in range(10):  # 최대 10레벨 상위까지
        if (candidate / "SSOT.md").exists():
            return candidate
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    # 찾지 못하면 현재 디렉토리 반환
    return Path.cwd()


# ---------------------------------------------------------------------------
# check_all 커맨드
# ---------------------------------------------------------------------------

def cmd_check_all(args: argparse.Namespace) -> int:
    """3-Way 일관성 검사 실행 및 결과 출력.

    종료 코드: 0=OK, 1=DRIFT, 2=ERROR.
    """
    from src.workflow.consistency import ConsistencyChecker

    root = Path(args.root) if args.root else _find_project_root()
    checker = ConsistencyChecker(root=root)
    report = checker.check_all()

    print(report.format_report())
    print()

    if report.has_error:
        print("[결과] ERROR — 하나 이상의 검사에서 에러 발생.")
        return 2
    if report.has_drift:
        print("[결과] DRIFT — 소스 간 불일치 감지. 진행 전 확인 필요.")
        return 1
    print("[결과] OK — 모든 소스 일관성 확인 완료.")
    return 0


# ---------------------------------------------------------------------------
# checkpoint 커맨드
# ---------------------------------------------------------------------------

def cmd_checkpoint_save(args: argparse.Namespace) -> int:
    """현재 워크플로우 상태를 체크포인트로 저장."""
    from src.workflow.checkpoint_engine import WorkflowCheckpointer

    root = Path(args.root) if args.root else _find_project_root()
    db_path = root / ".omc" / "state" / "checkpoints.db"

    # leviathan-active-phase.json에서 현재 상태 로드
    active_phase_file = root / ".omc" / "state" / "leviathan-active-phase.json"
    current_stage_file = root / ".omc" / "state" / "leviathan-current-stage.json"

    state: dict = {}
    if active_phase_file.exists():
        try:
            ap = json.loads(active_phase_file.read_text(encoding="utf-8"))
            state["phase"] = ap.get("current_phase", "unknown")
            state["prd_pass_count"] = ap.get("prd", {}).get("passed")
            state["prd_total_count"] = ap.get("prd", {}).get("total_stories")
            state["test_count"] = ap.get("tests", {}).get("passed")
            state["active_phase_raw"] = ap
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[에러] leviathan-active-phase.json 읽기 실패: {exc}", file=sys.stderr)
            return 2

    if current_stage_file.exists():
        try:
            cs = json.loads(current_stage_file.read_text(encoding="utf-8"))
            state["stage"] = cs.get("stage", "unknown")
            state["current_stage_raw"] = cs
        except (json.JSONDecodeError, OSError):
            state.setdefault("stage", "unknown")
    else:
        state.setdefault("stage", "unknown")

    trigger = args.trigger or "수동_CLI"

    with WorkflowCheckpointer(db_path=str(db_path)) as cp:
        cp_id = cp.save(state, trigger=trigger)

    print(f"[OK] 체크포인트 저장 완료: {cp_id}")
    return 0


def cmd_checkpoint_restore(args: argparse.Namespace) -> int:
    """가장 최근 체크포인트 복원 (또는 특정 ID 지정)."""
    from src.workflow.checkpoint_engine import WorkflowCheckpointer

    root = Path(args.root) if args.root else _find_project_root()
    db_path = root / ".omc" / "state" / "checkpoints.db"

    if not db_path.exists():
        print("[에러] 체크포인트 DB가 없습니다. 먼저 'checkpoint save'를 실행하세요.", file=sys.stderr)
        return 2

    with WorkflowCheckpointer(db_path=str(db_path)) as cp:
        if args.id:
            state = cp.get_checkpoint(args.id)
            if state is None:
                print(f"[에러] 체크포인트 '{args.id}'를 찾을 수 없습니다.", file=sys.stderr)
                return 2
        else:
            phase_filter = args.phase or None
            state = cp.restore_latest(phase=phase_filter)
            if state is None:
                label = f"phase={phase_filter}" if phase_filter else "전체"
                print(f"[에러] {label}에 해당하는 체크포인트가 없습니다.", file=sys.stderr)
                return 2

    restored_from = state.get("_restored_from", "unknown")
    print(f"[OK] 체크포인트 복원 완료: {restored_from}")
    print(json.dumps(state, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_checkpoint_history(args: argparse.Namespace) -> int:
    """최근 체크포인트 이력 조회."""
    from src.workflow.checkpoint_engine import WorkflowCheckpointer

    root = Path(args.root) if args.root else _find_project_root()
    db_path = root / ".omc" / "state" / "checkpoints.db"

    if not db_path.exists():
        print("체크포인트 DB가 없습니다. 이력이 없습니다.")
        return 0

    with WorkflowCheckpointer(db_path=str(db_path)) as cp:
        history = cp.list_history(phase=args.phase or None, limit=args.limit)

    if not history:
        label = f"phase={args.phase}" if args.phase else "전체"
        print(f"{label}에 해당하는 체크포인트가 없습니다.")
        return 0

    # 테이블 출력
    header = f"{'ID':<35} {'PHASE':<8} {'STAGE':<12} {'PRD통과':<10} {'테스트':<8} {'트리거':<25} 생성일시"
    print(header)
    print("-" * len(header))
    for row in history:
        prd_pass = str(row.get("prd_pass_count") or "-")
        tests = str(row.get("test_count") or "-")
        print(
            f"{row['id']:<35} {row['phase']:<8} {row['stage']:<12} "
            f"{prd_pass:<10} {tests:<8} {row['trigger']:<25} {row['created_at']}"
        )
    print(f"\n총 {len(history)}개 체크포인트.")
    return 0


# ---------------------------------------------------------------------------
# checkpoint apply 커맨드
# ---------------------------------------------------------------------------

def cmd_checkpoint_apply(args: argparse.Namespace) -> int:
    """체크포인트를 실제 state 파일에 적용 (restore + 덮어쓰기)."""
    from src.workflow.checkpoint_engine import WorkflowCheckpointer

    root = Path(args.root) if args.root else _find_project_root()
    db_path = root / ".omc" / "state" / "checkpoints.db"

    if not db_path.exists():
        print("[에러] 체크포인트 DB가 없습니다.", file=sys.stderr)
        return 2

    with WorkflowCheckpointer(db_path=str(db_path)) as cp:
        state = cp.restore_latest(phase=args.phase or None)
        if state is None:
            print("[에러] 복원할 체크포인트가 없습니다.", file=sys.stderr)
            return 2

    # 실제 state 파일에 덮어쓰기
    state_dir = root / ".omc" / "state"
    phase = state.get("phase", "")
    stage = state.get("stage", "")

    active = {"current_phase": phase, "phase": phase, "status": "in_progress"}
    prd_pass = state.get("prd_pass_count")
    prd_total = state.get("prd_total_count")
    if prd_pass is not None and prd_total is not None:
        active["prd"] = {"passed": prd_pass, "total_stories": prd_total}
    test_count = state.get("test_count")
    if test_count is not None:
        active["tests"] = {"passed": test_count, "failed": 0, "skipped": 12}

    (state_dir / "leviathan-active-phase.json").write_text(
        json.dumps(active, ensure_ascii=False), encoding="utf-8"
    )
    (state_dir / "leviathan-current-stage.json").write_text(
        json.dumps({"phase": phase, "stage": stage, "step": f"{stage}-Step1"}, ensure_ascii=False),
        encoding="utf-8",
    )

    # progress.json 업데이트 (기존 필드 보존)
    progress_path = state_dir / "leviathan-progress.json"
    existing = {}
    if progress_path.exists():
        try:
            existing = json.loads(progress_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    existing.update({"current_phase": phase, "current_stage": stage, "status": "in_progress"})
    progress_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")

    cp_id = state.get("_restored_from", "unknown")
    print(f"[OK] 체크포인트 '{cp_id}' → state 파일 3개 적용 완료 (phase={phase}, stage={stage})")
    return 0


# ---------------------------------------------------------------------------
# sync 커맨드
# ---------------------------------------------------------------------------

def cmd_sync(args: argparse.Namespace) -> int:
    """7개 파일 원자적 동기화."""
    from src.workflow.sync import WorkflowSync, SyncParams

    root = Path(args.root) if args.root else _find_project_root()
    params = SyncParams(
        phase=args.phase,
        stage=args.stage or "pending",
        step=args.step or "",
        status=args.status or "in_progress",
        tests_passed=args.tests,
        prd_pass=args.prd_pass,
        prd_total=args.prd_total,
    )

    ws = WorkflowSync(root=root)
    ok, msg = ws.sync(params)
    print(f"{'[OK]' if ok else '[FAIL]'} {msg}")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# transition 커맨드
# ---------------------------------------------------------------------------

def cmd_transition(args: argparse.Namespace) -> int:
    """FSM 상태 전환."""
    from src.workflow.fsm import WorkflowFSM, InvalidTransition

    root = Path(args.root) if args.root else _find_project_root()
    fsm = WorkflowFSM(root=root)

    if args.event == "status":
        from src.workflow.fsm import STATE_LABELS
        label = STATE_LABELS.get(fsm.current_state, "")
        allowed = fsm.get_allowed_events()
        print(f"현재 상태: {fsm.current_state} ({label})")
        print(f"허용 이벤트: {', '.join(allowed) if allowed else '없음 (종료 상태)'}")
        return 0

    if args.event == "set":
        if not args.state:
            print("[에러] --state 인자가 필요합니다.", file=sys.stderr)
            return 2
        fsm.set_state(args.state)
        print(f"[OK] 상태 강제 설정: {args.state}")
        return 0

    try:
        new_state = fsm.transition(args.event)
        from src.workflow.fsm import STATE_LABELS
        label = STATE_LABELS.get(new_state, "")
        print(f"[OK] 전환 완료: {new_state} ({label})")
        return 0
    except InvalidTransition as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# notion_report 커맨드
# ---------------------------------------------------------------------------

def cmd_notion_report(args: argparse.Namespace) -> int:
    """NotionReporter를 통해 Phase K 테스트 결과 페이지 생성."""
    from src.infra.notion_reporter import NotionReporter

    root = Path(args.root) if args.root else _find_project_root()
    phase = args.phase or "K"

    # backtest_batches.json에서 케이스 로드
    batches_file = root / "engine" / "config" / "backtest_batches.json"
    if not batches_file.exists():
        # engine/ 디렉토리 내에서 실행 시
        batches_file = root / "config" / "backtest_batches.json"

    test_cases: list[dict] = []
    if batches_file.exists():
        try:
            data = json.loads(batches_file.read_text(encoding="utf-8"))
            # 구조: {"batch1_binance": [...], "batch2_krw": [...], ...}
            for batch_cases in data.values():
                if isinstance(batch_cases, list):
                    test_cases.extend(batch_cases)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[에러] backtest_batches.json 읽기 실패: {exc}", file=sys.stderr)
            return 2
    else:
        print(f"[경고] backtest_batches.json 없음: {batches_file}", file=sys.stderr)

    reporter = NotionReporter()
    page_id = reporter.write_plan(phase=f"Phase {phase}", test_cases=test_cases)
    print(f"notion_report: page_id={page_id}")
    return 0


# ---------------------------------------------------------------------------
# 인자 파서
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """CLI 인자 파서 구성."""
    parser = argparse.ArgumentParser(
        prog="python -m src.workflow.cli",
        description="LEVIATHAN 워크플로우 자동화 CLI",
    )
    parser.add_argument(
        "--root",
        metavar="경로",
        default=None,
        help="프로젝트 루트 디렉토리 (기본: SSOT.md 위치 자동 탐지)",
    )

    sub = parser.add_subparsers(dest="command", metavar="커맨드")
    sub.required = True

    # check_all
    p_check = sub.add_parser("check_all", help="3-Way 일관성 검사 실행")
    p_check.set_defaults(func=cmd_check_all)

    # checkpoint
    p_cp = sub.add_parser("checkpoint", help="워크플로우 체크포인트 관리")
    cp_sub = p_cp.add_subparsers(dest="cp_command", metavar="액션")
    cp_sub.required = True

    # checkpoint save
    p_save = cp_sub.add_parser("save", help="현재 상태를 체크포인트로 저장")
    p_save.add_argument(
        "--trigger",
        metavar="라벨",
        default=None,
        help="체크포인트 저장 사유 (기본: 수동_CLI)",
    )
    p_save.set_defaults(func=cmd_checkpoint_save)

    # checkpoint restore
    p_restore = cp_sub.add_parser("restore", help="가장 최근 체크포인트 복원")
    p_restore.add_argument(
        "--id",
        metavar="체크포인트_ID",
        default=None,
        help="복원할 특정 체크포인트 ID (기본: 가장 최근)",
    )
    p_restore.add_argument(
        "--phase",
        metavar="PHASE",
        default=None,
        help="Phase로 필터링 (예: S13)",
    )
    p_restore.set_defaults(func=cmd_checkpoint_restore)

    # checkpoint history
    p_hist = cp_sub.add_parser("history", help="체크포인트 이력 조회")
    p_hist.add_argument(
        "--phase",
        metavar="PHASE",
        default=None,
        help="Phase로 필터링 (예: S13)",
    )
    p_hist.add_argument(
        "--limit",
        metavar="N",
        type=int,
        default=20,
        help="최대 표시 수 (기본: 20)",
    )
    p_hist.set_defaults(func=cmd_checkpoint_history)

    # checkpoint apply
    p_apply = cp_sub.add_parser("apply", help="체크포인트를 state 파일에 실제 적용")
    p_apply.add_argument("--phase", metavar="PHASE", default=None, help="Phase로 필터링")
    p_apply.set_defaults(func=cmd_checkpoint_apply)

    # sync
    p_sync = sub.add_parser("sync", help="7개 파일 원자적 동기화")
    p_sync.add_argument("--phase", required=True, help="Phase (예: TF-QF)")
    p_sync.add_argument("--stage", default="pending", help="Stage (기본: pending)")
    p_sync.add_argument("--step", default="", help="Step")
    p_sync.add_argument("--status", default="in_progress", help="상태 (기본: in_progress)")
    p_sync.add_argument("--tests", type=int, default=None, help="테스트 통과 수")
    p_sync.add_argument("--prd-pass", type=int, default=None, help="PRD 통과 수")
    p_sync.add_argument("--prd-total", type=int, default=None, help="PRD 전체 수")
    p_sync.set_defaults(func=cmd_sync)

    # transition
    p_trans = sub.add_parser("transition", help="FSM 상태 전환")
    p_trans.add_argument("event", help="이벤트 (예: shadow_pass, entry_gate_pass) 또는 status/set")
    p_trans.add_argument("--state", default=None, help="set 시 강제 설정할 상태")
    p_trans.set_defaults(func=cmd_transition)

    # notion_report
    p_notion = sub.add_parser("notion_report", help="Notion에 Phase K 테스트 결과 페이지 생성")
    p_notion.add_argument("--phase", default="K", help="Phase 레이블 (기본: K)")
    p_notion.set_defaults(func=cmd_notion_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    """진입점. 종료 코드 반환."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
