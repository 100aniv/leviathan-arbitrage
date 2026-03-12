"""ML Signal Canary — US-096.

Production canary for gradual ML signal rollout:
Paper → Shadow mode validation with staged traffic splitting.
10% → 50% → 100% progressive transition.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


class CanaryStage(str, Enum):
    """Canary rollout stages."""
    DISABLED = "disabled"
    CANARY_10 = "canary_10"    # 10% ML, 90% baseline
    CANARY_50 = "canary_50"    # 50% ML, 50% baseline
    FULL_ML = "full_ml"        # 100% ML
    ROLLBACK = "rollback"      # reverted to baseline


@dataclass
class CanaryMetrics:
    """Canary 단계별 성과 지표."""
    stage: CanaryStage
    ml_signals: int = 0
    baseline_signals: int = 0
    ml_pnl: float = 0.0
    baseline_pnl: float = 0.0
    ml_wins: int = 0
    baseline_wins: int = 0
    started_at: str = ""
    elapsed_seconds: float = 0.0

    @property
    def ml_win_rate(self) -> float:
        return self.ml_wins / self.ml_signals if self.ml_signals > 0 else 0.0

    @property
    def baseline_win_rate(self) -> float:
        return self.baseline_wins / self.baseline_signals if self.baseline_signals > 0 else 0.0

    @property
    def pnl_delta(self) -> float:
        return self.ml_pnl - self.baseline_pnl

    @property
    def ml_improves(self) -> bool:
        return self.pnl_delta > 0


class MLCanary:
    """ML 시그널 Canary 배포 관리자.

    단계적 전환: disabled → canary_10 → canary_50 → full_ml.
    기존 시그널 대비 PnL 개선 확인 후 다음 단계로 승격.
    """

    STAGE_ORDER = [
        CanaryStage.DISABLED,
        CanaryStage.CANARY_10,
        CanaryStage.CANARY_50,
        CanaryStage.FULL_ML,
    ]
    TRAFFIC_SPLIT = {
        CanaryStage.DISABLED: 0.0,
        CanaryStage.CANARY_10: 0.1,
        CanaryStage.CANARY_50: 0.5,
        CanaryStage.FULL_ML: 1.0,
        CanaryStage.ROLLBACK: 0.0,
    }

    def __init__(
        self,
        ml_scorer: Any | None = None,
        min_signals_to_promote: int = 50,
        min_pnl_delta: float = 0.0,
        auto_promote: bool = True,
    ) -> None:
        self._ml_scorer = ml_scorer
        self._min_signals = min_signals_to_promote
        self._min_pnl_delta = min_pnl_delta
        self._auto_promote = auto_promote
        self._stage = CanaryStage.DISABLED
        self._metrics = CanaryMetrics(stage=self._stage)
        self._rng = np.random.default_rng()
        self._stage_history: list[tuple[str, CanaryStage]] = []

    @property
    def stage(self) -> CanaryStage:
        return self._stage

    @property
    def metrics(self) -> CanaryMetrics:
        return self._metrics

    @property
    def ml_traffic_pct(self) -> float:
        return self.TRAFFIC_SPLIT.get(self._stage, 0.0)

    @property
    def is_active(self) -> bool:
        return self._stage not in (CanaryStage.DISABLED, CanaryStage.ROLLBACK)

    def start(self) -> None:
        """Canary 시작 (10% ML)."""
        self._transition(CanaryStage.CANARY_10)

    def should_use_ml(self) -> bool:
        """현재 시그널에 ML 스코어를 적용할지 결정.

        트래픽 비율에 따라 확률적으로 결정.
        """
        if self._stage == CanaryStage.FULL_ML:
            return True
        if self._stage in (CanaryStage.DISABLED, CanaryStage.ROLLBACK):
            return False
        return self._rng.random() < self.ml_traffic_pct

    def record_signal(self, used_ml: bool, pnl: float) -> None:
        """시그널 결과 기록.

        Parameters:
            used_ml: ML 스코어 사용 여부
            pnl: 해당 시그널의 PnL
        """
        if used_ml:
            self._metrics.ml_signals += 1
            self._metrics.ml_pnl += pnl
            if pnl > 0:
                self._metrics.ml_wins += 1
        else:
            self._metrics.baseline_signals += 1
            self._metrics.baseline_pnl += pnl
            if pnl > 0:
                self._metrics.baseline_wins += 1

        # Auto-promote check
        if self._auto_promote:
            self._check_promotion()

    def promote(self) -> bool:
        """다음 Canary 단계로 수동 승격.

        Returns:
            True if promoted, False if already at max or criteria not met.
        """
        current_idx = self.STAGE_ORDER.index(self._stage) if self._stage in self.STAGE_ORDER else -1
        if current_idx < 0 or current_idx >= len(self.STAGE_ORDER) - 1:
            return False

        if not self._meets_promotion_criteria():
            logger.warning("canary: promotion criteria not met (pnl_delta=%.4f, ml_signals=%d)",
                          self._metrics.pnl_delta, self._metrics.ml_signals)
            return False

        next_stage = self.STAGE_ORDER[current_idx + 1]
        self._transition(next_stage)
        return True

    def rollback(self) -> None:
        """ML 시그널 비활성화 (baseline으로 복귀)."""
        self._transition(CanaryStage.ROLLBACK)

    def reset_metrics(self) -> None:
        """지표 초기화 (단계 변경 시)."""
        self._metrics = CanaryMetrics(
            stage=self._stage,
            started_at=datetime.now(timezone.utc).isoformat(),
        )

    def status(self) -> dict[str, Any]:
        """현재 Canary 상태."""
        m = self._metrics
        return {
            "stage": self._stage.value,
            "ml_traffic_pct": self.ml_traffic_pct,
            "ml_signals": m.ml_signals,
            "baseline_signals": m.baseline_signals,
            "ml_pnl": round(m.ml_pnl, 6),
            "baseline_pnl": round(m.baseline_pnl, 6),
            "pnl_delta": round(m.pnl_delta, 6),
            "ml_win_rate": round(m.ml_win_rate, 4),
            "baseline_win_rate": round(m.baseline_win_rate, 4),
            "ml_improves": m.ml_improves,
            "stage_history": [(ts, s.value) for ts, s in self._stage_history],
        }

    def _meets_promotion_criteria(self) -> bool:
        """승격 기준 충족 여부."""
        m = self._metrics
        if m.ml_signals < self._min_signals:
            return False
        if m.pnl_delta < self._min_pnl_delta:
            return False
        return True

    def _check_promotion(self) -> None:
        """자동 승격 체크."""
        if self._stage in (CanaryStage.DISABLED, CanaryStage.ROLLBACK, CanaryStage.FULL_ML):
            return
        if self._meets_promotion_criteria():
            self.promote()

    def _transition(self, new_stage: CanaryStage) -> None:
        """상태 전환 + 지표 초기화."""
        old = self._stage
        self._stage = new_stage
        self._stage_history.append(
            (datetime.now(timezone.utc).isoformat(), new_stage)
        )
        self.reset_metrics()
        logger.info("canary: %s → %s", old.value, new_stage.value)
