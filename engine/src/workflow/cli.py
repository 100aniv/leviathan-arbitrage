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

    return parser


def main(argv: list[str] | None = None) -> int:
    """진입점. 종료 코드 반환."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
