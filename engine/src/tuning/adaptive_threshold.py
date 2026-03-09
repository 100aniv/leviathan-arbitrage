"""Adaptive MIN_EDGE_BPS threshold — adjusts hourly based on win-rate."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol

import structlog

logger = structlog.get_logger(__name__)


class _AsyncConn(Protocol):
    async def executemany(self, query: str, args: list[tuple[Any, ...]]) -> None: ...


class AdaptiveThreshold:
    """매 1시간 MIN_EDGE_BPS 자동 조정."""

    def __init__(
        self,
        initial_edge_bps: float = 5.0,
        min_edge: float = 2.0,
        max_edge: float = 50.0,
        step_bps: float = 1.0,
    ) -> None:
        self.current_edge_bps = initial_edge_bps
        self.min_edge = min_edge
        self.max_edge = max_edge
        self.step = step_bps
        self.history: list[dict] = []

    def adjust(self, win_rate: float, total_trades: int) -> float:
        """WR 기반 MIN_EDGE 조정.

        WR < 50%  → edge 상향 (step_bps)
        50% <= WR <= 90% → 유지
        WR > 90%  → edge 하향 (step_bps)
        total_trades < 10 → 조정 안 함 (데이터 부족)
        """
        if total_trades < 10:
            return self.current_edge_bps

        old = self.current_edge_bps
        if win_rate < 0.5:
            self.current_edge_bps = min(self.current_edge_bps + self.step, self.max_edge)
        elif win_rate > 0.9:
            self.current_edge_bps = max(self.current_edge_bps - self.step, self.min_edge)

        if old != self.current_edge_bps:
            entry = {
                "timestamp": datetime.now(timezone.utc),
                "old_edge": old,
                "new_edge": self.current_edge_bps,
                "win_rate": win_rate,
                "total_trades": total_trades,
            }
            self.history.append(entry)
            logger.info(
                "adaptive_threshold.adjusted",
                old_edge=old,
                new_edge=self.current_edge_bps,
                win_rate=win_rate,
                total_trades=total_trades,
            )

        return self.current_edge_bps

    async def save_history(self, conn: _AsyncConn) -> None:
        """변경 이력 TimescaleDB 저장."""
        if not self.history:
            return
        try:
            await conn.executemany(
                """
                INSERT INTO adaptive_threshold_log
                    (timestamp, old_edge, new_edge, win_rate, trades)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT DO NOTHING
                """,
                [
                    (
                        row["timestamp"],
                        row["old_edge"],
                        row["new_edge"],
                        row["win_rate"],
                        row["total_trades"],
                    )
                    for row in self.history
                ],
            )
            self.history.clear()
        except Exception as exc:  # noqa: BLE001
            logger.error("adaptive_threshold.save_history failed", error=str(exc))
