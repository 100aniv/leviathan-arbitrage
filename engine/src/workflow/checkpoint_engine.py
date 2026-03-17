"""LEVIATHAN 워크플로우 스테이지 자동 체크포인팅.

.omc/state/checkpoints.db (로컬 SQLite)에 상태 스냅샷을 저장.
이것은 워크플로우 개발 상태이며, 거래 데이터가 아님.
  - TimescaleDB (Docker) = 거래 데이터 → engine/src/
  - SQLite (로컬) = 워크플로우 체크포인트 → engine/src/workflow/ 전용
"""
import sqlite3
import json
import hashlib
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

CHECKPOINT_DIR = Path(".omc/state")
CHECKPOINT_DB = CHECKPOINT_DIR / "checkpoints.db"


class WorkflowCheckpointer:
    """스테이지 전환 시 자동 체크포인팅 엔진."""

    def __init__(self, db_path: str = str(CHECKPOINT_DB)):
        """SQLite 연결 초기화. DB와 테이블이 없으면 자동 생성."""
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._create_table()

    def _create_table(self):
        """체크포인트 테이블 및 인덱스 생성."""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS checkpoints (
                id TEXT PRIMARY KEY,
                phase TEXT NOT NULL,
                stage TEXT NOT NULL,
                state_json TEXT NOT NULL,
                ssot_hash TEXT,
                prd_pass_count INTEGER,
                prd_total_count INTEGER,
                test_count INTEGER,
                trigger TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_checkpoints_phase
            ON checkpoints(phase, created_at DESC)
        """)
        self.conn.commit()

    def save(self, state: dict, trigger: str) -> str:
        """스테이지 전환 시 체크포인트 저장.

        Args:
            state: 전체 워크플로우 상태 딕셔너리 (LeviathanState 호환)
            trigger: 체크포인트 생성 사유 (예: "stage_A_complete", "shadow_pass")

        Returns:
            체크포인트 ID 문자열
        """
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        phase = state.get("phase", "unknown")
        stage = state.get("stage", "unknown")
        cp_id = f"{phase}_{stage}_{ts}_{uuid.uuid4().hex[:4]}"

        ssot_hash = self._hash_file(Path("SSOT.md"))

        self.conn.execute(
            "INSERT OR REPLACE INTO checkpoints VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                cp_id,
                phase,
                stage,
                json.dumps(state, ensure_ascii=False, default=str),
                ssot_hash,
                state.get("prd_pass_count"),
                state.get("prd_total_count"),
                state.get("test_count"),
                trigger,
                datetime.now().isoformat(),
            ),
        )
        self.conn.commit()
        return cp_id

    def restore_latest(self, phase: Optional[str] = None) -> Optional[dict]:
        """가장 최근 체크포인트 복원. phase로 필터링 가능."""
        if phase:
            row = self.conn.execute(
                "SELECT state_json, id FROM checkpoints WHERE phase=? ORDER BY created_at DESC LIMIT 1",
                (phase,),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT state_json, id FROM checkpoints ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        if row:
            state = json.loads(row["state_json"])
            state["_restored_from"] = row["id"]
            return state
        return None

    def get_checkpoint(self, checkpoint_id: str) -> Optional[dict]:
        """특정 체크포인트 ID로 조회 (시간 여행 디버깅)."""
        row = self.conn.execute(
            "SELECT state_json FROM checkpoints WHERE id=?", (checkpoint_id,)
        ).fetchone()
        return json.loads(row["state_json"]) if row else None

    def list_history(self, phase: Optional[str] = None, limit: int = 20) -> list[dict]:
        """체크포인트 이력 조회."""
        if phase:
            rows = self.conn.execute(
                "SELECT id, phase, stage, trigger, ssot_hash, prd_pass_count, prd_total_count, test_count, created_at "
                "FROM checkpoints WHERE phase=? ORDER BY created_at DESC LIMIT ?",
                (phase, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT id, phase, stage, trigger, ssot_hash, prd_pass_count, prd_total_count, test_count, created_at "
                "FROM checkpoints ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def prune(self, keep_last: int = 50):
        """오래된 체크포인트 정리. 최근 N개만 유지."""
        self.conn.execute(
            "DELETE FROM checkpoints WHERE id NOT IN "
            "(SELECT id FROM checkpoints ORDER BY created_at DESC LIMIT ?)",
            (keep_last,),
        )
        self.conn.commit()

    def _hash_file(self, path: Path) -> str:
        """드리프트 감지용 파일 SHA256 해시 (앞 16자)."""
        if not path.exists():
            return ""
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]

    def close(self):
        """데이터베이스 연결 종료."""
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
