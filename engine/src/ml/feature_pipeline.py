"""Regime feature pipeline — US-082."""
from __future__ import annotations

import logging
import numpy as np

logger = logging.getLogger(__name__)


class RegimeFeaturePipeline:
    """레짐 분류를 위한 피처 추출 파이프라인.

    5개 카테고리, 10개 피처:
    - Volatility: realized_vol, historical_vol, vol_ratio
    - Spread: bid_ask_spread_mean, spread_std
    - Volume: volume_zscore, volume_ratio
    - Momentum: rolling_return, momentum_ma_diff
    - Order Flow: order_imbalance
    """

    N_FEATURES = 10
    FEATURE_NAMES = [
        "realized_vol", "historical_vol", "vol_ratio",
        "bid_ask_spread_mean", "spread_std",
        "volume_zscore", "volume_ratio",
        "rolling_return", "momentum_ma_diff",
        "order_imbalance",
    ]

    def __init__(self, short_window: int = 20, long_window: int = 100) -> None:
        self._short_window = short_window
        self._long_window = long_window

    def extract(
        self,
        returns: np.ndarray,
        spreads: np.ndarray,
        volumes: np.ndarray,
        bid_volumes: np.ndarray | None = None,
        ask_volumes: np.ndarray | None = None,
    ) -> np.ndarray:
        """Raw 시계열 → 피처 벡터 (1, N_FEATURES).

        Parameters:
            returns: 수익률 배열
            spreads: bid-ask spread 배열 (fraction, e.g. 0.001)
            volumes: 거래량 배열
            bid_volumes: 매수 호가 볼륨 (optional)
            ask_volumes: 매도 호가 볼륨 (optional)
        Returns:
            shape (1, N_FEATURES) 정규화 전 피처 벡터
        """
        features = np.zeros(self.N_FEATURES, dtype=np.float64)

        # Volatility
        if len(returns) >= 2:
            short = returns[-self._short_window:]
            long = returns[-self._long_window:]
            features[0] = float(np.std(short))  # realized_vol
            features[1] = float(np.std(long)) if len(long) >= 2 else features[0]  # historical_vol
            features[2] = features[0] / features[1] if features[1] > 1e-12 else 1.0  # vol_ratio

        # Spread
        if len(spreads) >= 1:
            features[3] = float(np.mean(spreads[-self._short_window:]))  # bid_ask_spread_mean
            features[4] = float(np.std(spreads[-self._short_window:])) if len(spreads) >= 2 else 0.0  # spread_std

        # Volume
        if len(volumes) >= 2:
            vol_mean = float(np.mean(volumes[-self._long_window:]))
            vol_std = float(np.std(volumes[-self._long_window:]))
            current_vol = float(volumes[-1])
            features[5] = (current_vol - vol_mean) / vol_std if vol_std > 1e-12 else 0.0  # volume_zscore
            vol_ma = float(np.mean(volumes[-self._short_window:])) if len(volumes) >= self._short_window else vol_mean
            features[6] = current_vol / vol_ma if vol_ma > 1e-12 else 1.0  # volume_ratio

        # Momentum
        if len(returns) >= 2:
            features[7] = float(np.sum(returns[-self._short_window:]))  # rolling_return
            if len(returns) >= self._long_window:
                short_ma = float(np.mean(returns[-self._short_window:]))
                long_ma = float(np.mean(returns[-self._long_window:]))
                features[8] = short_ma - long_ma  # momentum_ma_diff
            else:
                features[8] = 0.0

        # Order Flow
        if bid_volumes is not None and ask_volumes is not None and len(bid_volumes) >= 1:
            bid_sum = float(np.sum(bid_volumes[-self._short_window:]))
            ask_sum = float(np.sum(ask_volumes[-self._short_window:]))
            total = bid_sum + ask_sum
            features[9] = (bid_sum - ask_sum) / total if total > 1e-12 else 0.0  # order_imbalance

        return self.fill_missing(features.reshape(1, -1))

    def extract_batch(
        self,
        returns_series: list[np.ndarray],
        spreads_series: list[np.ndarray],
        volumes_series: list[np.ndarray],
        bid_volumes_series: list[np.ndarray] | None = None,
        ask_volumes_series: list[np.ndarray] | None = None,
    ) -> np.ndarray:
        """배치 피처 추출 → (n_samples, N_FEATURES)."""
        n = len(returns_series)
        result = np.zeros((n, self.N_FEATURES), dtype=np.float64)
        for i in range(n):
            bv = bid_volumes_series[i] if bid_volumes_series else None
            av = ask_volumes_series[i] if ask_volumes_series else None
            result[i] = self.extract(
                returns_series[i], spreads_series[i], volumes_series[i], bv, av
            ).flatten()
        return result

    @staticmethod
    def normalize(features: np.ndarray) -> np.ndarray:
        """Z-score 정규화 (column-wise).

        Parameters:
            features: shape (n_samples, n_features)
        Returns:
            정규화된 피처 (mean≈0, std≈1)
        """
        if features.shape[0] < 2:
            return features  # 단일 샘플은 정규화 불가
        mean = np.mean(features, axis=0)
        std = np.std(features, axis=0)
        std[std < 1e-12] = 1.0  # zero-std 방지
        return (features - mean) / std

    @staticmethod
    def fill_missing(features: np.ndarray) -> np.ndarray:
        """결측값 처리 — NaN/Inf → 0."""
        mask = ~np.isfinite(features)
        if np.any(mask):
            count = int(np.sum(mask))
            logger.warning("feature_pipeline: %d NaN/Inf values replaced with 0", count)
            features = np.where(mask, 0.0, features)
        return features

    @property
    def feature_names(self) -> list[str]:
        return list(self.FEATURE_NAMES)


class MLFeaturePipeline:
    """ML 시그널 스코어링을 위한 확장 피처 파이프라인.

    US-091: 6개 카테고리, 20개 피처
    - Orderbook: spread_bps, depth_l1, depth_l3, depth_l5
    - Volatility: vol_1m, vol_5m, vol_1h, vol_ratio_1m_1h
    - Volume: volume_zscore, vwap_ratio, volume_ma_ratio
    - Regime: hmm_state, regime_confidence, transition_prob
    - Momentum: rolling_return_5m, momentum_diff, rsi_14
    - Execution: fill_rate_1h, avg_slippage_bps, rejection_rate
    """

    N_FEATURES = 20
    FEATURE_NAMES = [
        "spread_bps", "depth_l1", "depth_l3", "depth_l5",
        "vol_1m", "vol_5m", "vol_1h", "vol_ratio_1m_1h",
        "volume_zscore", "vwap_ratio", "volume_ma_ratio",
        "hmm_state", "regime_confidence", "transition_prob",
        "rolling_return_5m", "momentum_diff", "rsi_14",
        "fill_rate_1h", "avg_slippage_bps", "rejection_rate",
    ]

    def extract(
        self,
        orderbook_data: dict | None = None,
        returns: np.ndarray | None = None,
        volumes: np.ndarray | None = None,
        spreads: np.ndarray | None = None,
        regime_state: int = 1,
        regime_confidence: float = 0.5,
        transition_prob: float = 0.1,
        fill_rate: float = 0.95,
        avg_slippage_bps: float = 2.0,
        rejection_rate: float = 0.02,
        vwap_price: float | None = None,
        current_price: float | None = None,
    ) -> np.ndarray:
        """Extract 20-feature vector. Returns shape (1, 20)."""
        features = np.zeros(self.N_FEATURES, dtype=np.float64)

        # Orderbook features
        if orderbook_data:
            features[0] = orderbook_data.get("spread_bps", 0)
            features[1] = orderbook_data.get("depth_l1", 0)
            features[2] = orderbook_data.get("depth_l3", 0)
            features[3] = orderbook_data.get("depth_l5", 0)

        # Volatility (multi-timeframe)
        if returns is not None and len(returns) >= 2:
            features[4] = float(np.std(returns[-60:])) if len(returns) >= 60 else float(np.std(returns))
            features[5] = float(np.std(returns[-300:])) if len(returns) >= 300 else features[4]
            features[6] = float(np.std(returns[-3600:])) if len(returns) >= 3600 else features[5]
            features[7] = features[4] / features[6] if features[6] > 1e-12 else 1.0

        # Volume
        if volumes is not None and len(volumes) >= 2:
            vol_mean = float(np.mean(volumes))
            vol_std = float(np.std(volumes))
            features[8] = (float(volumes[-1]) - vol_mean) / vol_std if vol_std > 1e-12 else 0.0
            if vwap_price and current_price and current_price > 0:
                features[9] = vwap_price / current_price
            else:
                features[9] = 1.0
            vol_ma = float(np.mean(volumes[-20:])) if len(volumes) >= 20 else vol_mean
            features[10] = float(volumes[-1]) / vol_ma if vol_ma > 1e-12 else 1.0

        # Regime (from HMM)
        features[11] = float(regime_state)
        features[12] = regime_confidence
        features[13] = transition_prob

        # Momentum
        if returns is not None and len(returns) >= 2:
            features[14] = float(np.sum(returns[-300:])) if len(returns) >= 300 else float(np.sum(returns))
            short_ma = float(np.mean(returns[-60:])) if len(returns) >= 60 else float(np.mean(returns))
            long_ma = float(np.mean(returns[-300:])) if len(returns) >= 300 else short_ma
            features[15] = short_ma - long_ma
            # RSI-14
            if len(returns) >= 14:
                recent = returns[-14:]
                gains = recent.copy()
                losses = recent.copy()
                gains[gains < 0] = 0
                losses[losses > 0] = 0
                avg_gain = float(np.mean(gains))
                avg_loss = abs(float(np.mean(losses)))
                if avg_loss > 1e-12:
                    rs = avg_gain / avg_loss
                    features[16] = 100.0 - (100.0 / (1.0 + rs))
                else:
                    features[16] = 100.0 if avg_gain > 0 else 50.0
            else:
                features[16] = 50.0

        # Execution metrics
        features[17] = fill_rate
        features[18] = avg_slippage_bps
        features[19] = rejection_rate

        return RegimeFeaturePipeline.fill_missing(features.reshape(1, -1))

    @property
    def feature_names(self) -> list[str]:
        return list(self.FEATURE_NAMES)
