"""Single-Write 동기화 CLI — 7개 파일 원자적 업데이트.

Usage::
    python -m src.workflow.cli sync --phase "TF-QF" --stage "C" \\
        --tests 5242 --prd-pass 313 --prd-total 315

Marker 기반: SSOT.md와 CLAUDE.md에 <!-- SYNC:KEY --> 마커 사이를 치환.
실패 시 백업에서 롤백.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class SyncParams:
    """동기화 파라미터."""
    phase: str
    stage: str = "pending"
    step: str = ""
    status: str = "in_progress"
    tests_passed: Optional[int] = None
    tests_failed: int = 0
    tests_skipped: int = 12
    prd_pass: Optional[int] = None
    prd_total: Optional[int] = None


class WorkflowSync:
    """7개 파일 원자적 동기화."""

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = root or Path(".")
        self._backups: list[tuple[Path, Path]] = []

    def sync(self, params: SyncParams) -> tuple[bool, str]:
        """7개 파일을 원자적으로 업데이트.

        Returns:
            (success, message)
        """
        files_to_update = self._collect_targets()

        # Step 1: 백업 생성
        self._create_backups(files_to_update)

        try:
            # Step 2: State JSON 파일 업데이트
            self._update_active_phase(params)
            self._update_current_stage(params)
            self._update_progress(params)

            # Step 3: Markdown 파일 마커 기반 치환
            self._update_ssot_markers(params)
            self._update_claude_md_markers(params)

            # Step 4: 검증
            ok, msg = self._verify()
            if not ok:
                self._rollback()
                return False, f"검증 실패 → 롤백 완료: {msg}"

            # Step 5: 백업 정리
            self._cleanup_backups()
            return True, f"7개 파일 동기화 완료: phase={params.phase}, stage={params.stage}"

        except Exception as exc:
            self._rollback()
            return False, f"동기화 실패 → 롤백 완료: {exc}"

    def _collect_targets(self) -> list[Path]:
        """동기화 대상 파일 목록."""
        return [
            self.root / ".omc" / "state" / "leviathan-active-phase.json",
            self.root / ".omc" / "state" / "leviathan-current-stage.json",
            self.root / ".omc" / "state" / "leviathan-progress.json",
            self.root / "SSOT.md",
            self.root / ".claude" / "CLAUDE.md",
        ]

    def _create_backups(self, files: list[Path]) -> None:
        """백업 생성."""
        self._backups = []
        for f in files:
            if f.exists():
                bak = f.with_suffix(f.suffix + ".sync-bak")
                shutil.copy2(f, bak)
                self._backups.append((f, bak))

    def _rollback(self) -> None:
        """백업에서 복원."""
        for orig, bak in self._backups:
            if bak.exists():
                shutil.copy2(bak, orig)
                bak.unlink()
        logger.warning("sync_rollback_completed", count=len(self._backups))

    def _cleanup_backups(self) -> None:
        """백업 파일 삭제."""
        for _, bak in self._backups:
            if bak.exists():
                bak.unlink()

    # --- State JSON 업데이트 ---

    def _update_active_phase(self, params: SyncParams) -> None:
        path = self.root / ".omc" / "state" / "leviathan-active-phase.json"
        data = {"current_phase": params.phase, "phase": params.phase, "status": params.status}
        if params.prd_pass is not None and params.prd_total is not None:
            data["prd"] = {"passed": params.prd_pass, "total_stories": params.prd_total}
        if params.tests_passed is not None:
            data["tests"] = {"passed": params.tests_passed, "failed": params.tests_failed, "skipped": params.tests_skipped}
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def _update_current_stage(self, params: SyncParams) -> None:
        path = self.root / ".omc" / "state" / "leviathan-current-stage.json"
        data = {"phase": params.phase, "stage": params.stage, "step": params.step or f"{params.stage}-Step1"}
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def _update_progress(self, params: SyncParams) -> None:
        path = self.root / ".omc" / "state" / "leviathan-progress.json"
        # Read existing to preserve fields like us_targets
        existing = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        existing.update({
            "current_phase": params.phase,
            "current_stage": params.stage,
            "current_step": params.step or f"{params.stage}-Step1",
            "status": params.status,
        })
        path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")

    # --- Markdown 마커 기반 치환 ---

    def _update_marker(self, file_path: Path, key: str, value: str) -> bool:
        """<!-- SYNC:KEY -->value<!-- /SYNC:KEY --> 패턴 치환.

        마커가 없으면 무시 (False 반환).
        """
        if not file_path.exists():
            return False
        text = file_path.read_text(encoding="utf-8")
        pattern = rf"(<!-- SYNC:{key} -->).*?(<!-- /SYNC:{key} -->)"
        replacement = rf"\g<1>{value}\g<2>"
        new_text, count = re.subn(pattern, replacement, text, flags=re.DOTALL)
        if count > 0:
            file_path.write_text(new_text, encoding="utf-8")
            return True
        return False

    def _update_ssot_markers(self, params: SyncParams) -> None:
        ssot = self.root / "SSOT.md"
        if not ssot.exists():
            return
        self._update_marker(ssot, "PHASE", f"{params.phase} ({params.status})")
        if params.tests_passed is not None:
            self._update_marker(ssot, "TESTS", f"{params.tests_passed:,} passed / {params.tests_failed} failed / {params.tests_skipped} skipped")
        if params.prd_pass is not None and params.prd_total is not None:
            self._update_marker(ssot, "PRD", f"{params.prd_pass}/{params.prd_total} passes:true")

    def _update_claude_md_markers(self, params: SyncParams) -> None:
        claude_md = self.root / ".claude" / "CLAUDE.md"
        if not claude_md.exists():
            return
        self._update_marker(claude_md, "PHASE", f"{params.phase} ({params.status})")
        if params.tests_passed is not None:
            self._update_marker(claude_md, "TESTS", f"{params.tests_passed:,} passed / {params.tests_failed} failed / {params.tests_skipped} skipped")
        if params.prd_pass is not None and params.prd_total is not None:
            self._update_marker(claude_md, "PRD", f"{params.prd_pass}/{params.prd_total} passes:true")

    # --- 검증 ---

    def _verify(self) -> tuple[bool, str]:
        """업데이트 후 3개 state 파일 Phase 일치 확인."""
        try:
            ap = json.loads((self.root / ".omc" / "state" / "leviathan-active-phase.json").read_text())
            cs = json.loads((self.root / ".omc" / "state" / "leviathan-current-stage.json").read_text())
            pg = json.loads((self.root / ".omc" / "state" / "leviathan-progress.json").read_text())
        except (json.JSONDecodeError, OSError) as exc:
            return False, f"State 파일 읽기 실패: {exc}"

        phases = {ap.get("current_phase"), cs.get("phase"), pg.get("current_phase")}
        if len(phases) != 1:
            return False, f"Phase 불일치: active={ap.get('current_phase')}, stage={cs.get('phase')}, progress={pg.get('current_phase')}"

        return True, "Phase 일치 확인"
