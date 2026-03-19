"""Market regime detector — classifies volatility into LOW/MEDIUM/HIGH/CRISIS."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
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
    # HMM 3-state regimes (US-081)
    CALM = "CALM"
    NORMAL = "NORMAL"
    VOLATILE = "VOLATILE"


HMM_REGIME_MAP: dict[int, MarketRegime] = {
    0: MarketRegime.CALM,
    1: MarketRegime.NORMAL,
    2: MarketRegime.VOLATILE,
}

THRESHOLD_TO_HMM: dict[MarketRegime, MarketRegime] = {
    MarketRegime.LOW: MarketRegime.CALM,
    MarketRegime.MEDIUM: MarketRegime.NORMAL,
    MarketRegime.HIGH: MarketRegime.VOLATILE,
}

REGIME_MIN_EDGE: dict[MarketRegime, Decimal] = {
    MarketRegime.CALM: Decimal("0.0003"),      # 3 bps
    MarketRegime.NORMAL: Decimal("0.0005"),     # 5 bps
    MarketRegime.VOLATILE: Decimal("0.0008"),   # 8 bps
    MarketRegime.LOW: Decimal("0.0003"),        # threshold alias → CALM
    MarketRegime.MEDIUM: Decimal("0.0005"),     # threshold alias → NORMAL
    MarketRegime.HIGH: Decimal("0.0008"),       # threshold alias → VOLATILE
    MarketRegime.CRISIS: Decimal("0.0015"),     # 15 bps — 극단 상황
}


# US-263: Regime parameter matrix — per-regime strategy parameters
# Keys: (regime, param_name) → value.  Strategies query this at runtime.
REGIME_PARAM_MATRIX: dict[str, dict[str, float]] = {
    "CALM": {
        "max_position_multiplier": 1.2,   # slightly larger positions
        "min_edge_bps": 3.0,
        "cooldown_seconds": 3.0,          # faster re-entry
        "volatility_multiplier": 0.8,     # tighter thresholds
    },
    "NORMAL": {
        "max_position_multiplier": 1.0,
        "min_edge_bps": 5.0,
        "cooldown_seconds": 5.0,
        "volatility_multiplier": 1.0,
    },
    "VOLATILE": {
        "max_position_multiplier": 0.7,   # reduce exposure
        "min_edge_bps": 8.0,
        "cooldown_seconds": 10.0,         # slower re-entry
        "volatility_multiplier": 1.5,     # wider thresholds
    },
    "CRISIS": {
        "max_position_multiplier": 0.0,   # no new positions
        "min_edge_bps": 15.0,
        "cooldown_seconds": 30.0,
        "volatility_multiplier": 2.0,
    },
    # Aliases for HMM regime names
    "LOW": {
        "max_position_multiplier": 1.2,
        "min_edge_bps": 3.0,
        "cooldown_seconds": 3.0,
        "volatility_multiplier": 0.8,
    },
    "MEDIUM": {
        "max_position_multiplier": 1.0,
        "min_edge_bps": 5.0,
        "cooldown_seconds": 5.0,
        "volatility_multiplier": 1.0,
    },
    "HIGH": {
        "max_position_multiplier": 0.7,
        "min_edge_bps": 8.0,
        "cooldown_seconds": 10.0,
        "volatility_multiplier": 1.5,
    },
}


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
        self.confidence: float = 0.5
        self.transition_prob: float = 0.1

    def get_regime_params(self, param_name: str, default: float = 0.0) -> float:
        """US-263: Look up a regime-specific parameter from the matrix.

        Args:
            param_name: One of max_position_multiplier, min_edge_bps,
                        cooldown_seconds, volatility_multiplier.
            default: Fallback if regime/param not found.

        Returns:
            Parameter value for the current regime.
        """
        regime_key = self.current_regime.value if hasattr(self.current_regime, "value") else str(self.current_regime)
        params = REGIME_PARAM_MATRIX.get(regime_key, {})
        val = params.get(param_name, default)
        if val != default:
            logger.debug(
                "regime_param_matrix regime=%s param=%s value=%.3f",
                regime_key, param_name, val,
            )
        return val

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


class HMMRegimeDetector:
    """GaussianHMM 3-state 레짐 분류기.

    US-081: 설계 + 구조. US-083에서 학습 파이프라인 구현.
    """

    N_STATES = 3
    COVARIANCE_TYPE = "full"
    N_ITER = 100

    def __init__(self) -> None:
        self._model: Any | None = None
        self.current_regime = MarketRegime.NORMAL
        self.transition_matrix: np.ndarray | None = None
        self._fitted = False
        self._feature_pipeline: Any | None = None  # US-082: RegimeFeaturePipeline

    def _load_hmmlearn(self) -> Any:
        """Lazy import hmmlearn — [ml] optional dep."""
        try:
            from hmmlearn.hmm import GaussianHMM
            return GaussianHMM
        except ImportError:
            logger.warning("hmmlearn not installed. Install with: pip install leviathan-engine[ml]")
            return None

    def fit(self, features: np.ndarray) -> "HMMRegimeDetector":
        """HMM 학습 (US-083에서 본격 구현).

        Parameters:
            features: shape (n_samples, n_features) — 피처 행렬
        """
        GaussianHMM = self._load_hmmlearn()
        if GaussianHMM is None:
            raise ImportError("hmmlearn required: pip install leviathan-engine[ml]")

        self._model = GaussianHMM(
            n_components=self.N_STATES,
            covariance_type=self.COVARIANCE_TYPE,
            n_iter=self.N_ITER,
            random_state=42,
        )
        self._model.fit(features)
        self.transition_matrix = self._model.transmat_
        self._fitted = True
        logger.info("HMM regime detector fitted: %d samples, %d features",
                     features.shape[0], features.shape[1])
        return self

    def predict(self, features: np.ndarray | list) -> MarketRegime:
        """현재 피처로 레짐 분류.

        Parameters:
            features: shape (1, n_features) or (n_samples, n_features)
        Returns:
            가장 최근 샘플의 MarketRegime
        """
        if not self._fitted or self._model is None:
            return self.current_regime

        arr = np.asarray(features)
        states = self._model.predict(arr)
        if len(states) == 0:
            return self.current_regime
        state_id = int(states[-1])
        regime = HMM_REGIME_MAP.get(state_id, MarketRegime.NORMAL)

        if regime != self.current_regime:
            logger.info("hmm_regime: %s → %s (state=%d)",
                       self.current_regime.value, regime.value, state_id)
            self.current_regime = regime

        return regime

    def set_feature_pipeline(self, pipeline: Any) -> None:
        """피처 파이프라인 연결 (US-082)."""
        self._feature_pipeline = pipeline

    def predict_from_raw(
        self,
        returns: np.ndarray,
        spreads: np.ndarray,
        volumes: np.ndarray,
        bid_volumes: np.ndarray | None = None,
        ask_volumes: np.ndarray | None = None,
    ) -> "MarketRegime":
        """Raw 시계열 → 피처 추출 → 레짐 분류."""
        if self._feature_pipeline is None:
            logger.warning("hmm_regime: no feature pipeline set, using threshold fallback")
            return self.current_regime
        features = self._feature_pipeline.extract(returns, spreads, volumes, bid_volumes, ask_volumes)
        return self.predict(features)

    @property
    def is_fitted(self) -> bool:
        return self._fitted
