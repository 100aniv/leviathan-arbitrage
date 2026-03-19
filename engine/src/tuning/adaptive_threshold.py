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

    def adjust(
        self,
        win_rate: float = 0.0,
        total_trades: int = 0,
        expected_edge_bps: float | None = None,
        profit_factor: float | None = None,
    ) -> float:
        """복합 지표 기반 MIN_EDGE 조정 (US-201).

        복합 모드 (expected_edge_bps AND profit_factor 모두 제공 시):
          edge < 0              → current += max(2.0, current * 0.5)  (공격적 상향)
          0 <= edge < 1.0       → current += 2.0
          profit_factor < 1.0   → current += 2.0  (독립 적용 가능)
          edge > 5.0 AND pf > 1.5 → current -= 0.5  (소극적 하향)

        WR fallback (edge/pf 미제공 시):
          WR < 50%  → edge 상향 (step_bps)
          50% <= WR <= 90% → 유지
          WR > 90%  → edge 하향 (step_bps)

        total_trades < 30 → 조정 안 함 (데이터 부족)
        """
        if total_trades < 30:
            return self.current_edge_bps

        old = self.current_edge_bps

        if expected_edge_bps is not None and profit_factor is not None:
            # 복합 지표 로직
            edge = expected_edge_bps
            pf = profit_factor
            delta = 0.0
            if edge < 0:
                delta += max(2.0, self.current_edge_bps * 0.5)
            elif 0.0 <= edge < 1.0:
                delta += 2.0
            if pf < 1.0:
                delta += 2.0
            if edge > 5.0 and pf > 1.5:
                delta -= 0.5
            self.current_edge_bps += delta
        else:
            # WR 기반 fallback
            if win_rate < 0.5:
                self.current_edge_bps += self.step
            elif win_rate > 0.9:
                self.current_edge_bps -= self.step

        # clamp [min_edge, max_edge]
        self.current_edge_bps = max(self.min_edge, min(self.max_edge, self.current_edge_bps))

        if old != self.current_edge_bps:
            entry = {
                "timestamp": datetime.now(timezone.utc),
                "old_edge": old,
                "new_edge": self.current_edge_bps,
                "win_rate": win_rate,
                "total_trades": total_trades,
                "expected_edge_bps": expected_edge_bps,
                "profit_factor": profit_factor,
            }
            self.history.append(entry)
            logger.info(
                "AdaptiveThreshold adjust",
                edge_bps=expected_edge_bps,
                pf=profit_factor,
                new_threshold=self.current_edge_bps,
                old_threshold=old,
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


class PerStrategyAdaptiveThreshold:
    """US-255: Wrapper that manages per-strategy AdaptiveThreshold instances.

    Provides independent edge_bps tuning per strategy while remaining backward
    compatible with the existing global AdaptiveThreshold usage.
    """

    def __init__(self, default_edge_bps: float = 5.0) -> None:
        self._default_edge = default_edge_bps
        self._thresholds: dict[str, AdaptiveThreshold] = {}

    def get_or_create(self, strategy_id: str) -> AdaptiveThreshold:
        if strategy_id not in self._thresholds:
            self._thresholds[strategy_id] = AdaptiveThreshold(
                initial_edge_bps=self._default_edge
            )
        return self._thresholds[strategy_id]

    def adjust(self, strategy_id: str = "global", **kwargs) -> float:
        """Adjust threshold for a specific strategy."""
        return self.get_or_create(strategy_id).adjust(**kwargs)

    def get_edge(self, strategy_id: str = "global") -> float:
        return self.get_or_create(strategy_id).current_edge_bps

    async def save_history(self, conn) -> None:
        """Persist history for all managed per-strategy thresholds."""
        for threshold in self._thresholds.values():
            await threshold.save_history(conn)

    @property
    def strategy_ids(self) -> list[str]:
        return list(self._thresholds.keys())
