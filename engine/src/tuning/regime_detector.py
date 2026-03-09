"""Market regime detector — classifies volatility into LOW/MEDIUM/HIGH/CRISIS."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol

import numpy as np

logger = logging.getLogger(__name__)


class _AsyncConn(Protocol):
    async def executemany(self, query: str, args: list[tuple[Any, ...]]) -> None: ...


class MarketRegime(str, Enum):
    LOW = "LOW"        # 변동성 낮음
    MEDIUM = "MEDIUM"  # 정상
    HIGH = "HIGH"      # 변동성 높음
    CRISIS = "CRISIS"  # 극단 상황


class RegimeDetector:
    """시장 체제 분류기 — 변동성/스프레드 기반."""

    def __init__(self, volatility_thresholds: dict | None = None) -> None:
        self.thresholds = volatility_thresholds or {
            "low": 0.005,    # 0.5%
            "high": 0.03,    # 3%
            "crisis": 0.08,  # 8%
        }
        self.current_regime = MarketRegime.MEDIUM
        self.history: list[dict] = []

    def detect(self, returns: list[float], spread_std: float = 0.0) -> MarketRegime:
        """최근 수익률 배열로 변동성 계산 → 체제 분류."""
        if not returns:
            return self.current_regime

        vol = float(np.std(returns))

        if vol >= self.thresholds["crisis"]:
            regime = MarketRegime.CRISIS
        elif vol >= self.thresholds["high"]:
            regime = MarketRegime.HIGH
        elif vol <= self.thresholds["low"]:
            regime = MarketRegime.LOW
        else:
            regime = MarketRegime.MEDIUM

        if regime != self.current_regime:
            entry = {
                "timestamp": datetime.now(timezone.utc),
                "old_regime": self.current_regime.value,
                "new_regime": regime.value,
                "volatility": vol,
                "spread_std": spread_std,
            }
            self.history.append(entry)
            logger.info(
                "regime_detector: %s → %s (vol=%.4f)",
                self.current_regime.value,
                regime.value,
                vol,
            )
            self.current_regime = regime

        return regime

    def should_kill_switch(self) -> bool:
        """CRISIS 체제에서 KillSwitch 발동 필요."""
        return self.current_regime == MarketRegime.CRISIS

    async def save_history(self, conn: _AsyncConn) -> None:
        """변경 이력 TimescaleDB 저장."""
        if not self.history:
            return
        try:
            await conn.executemany(
                """
                INSERT INTO regime_detector_log
                    (timestamp, old_regime, new_regime, volatility, spread_std)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT DO NOTHING
                """,
                [
                    (
                        row["timestamp"],
                        row["old_regime"],
                        row["new_regime"],
                        row["volatility"],
                        row["spread_std"],
                    )
                    for row in self.history
                ],
            )
            self.history.clear()
        except Exception as exc:  # noqa: BLE001
            logger.error("regime_detector.save_history failed: %s", exc)
