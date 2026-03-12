"""ML signal backtest — US-095.

Walk-forward ML ranking signal vs existing signal comparison.
A/B test framework for ML signal PnL delta measurement.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    """백테스트 결과."""

    strategy: str
    total_signals: int
    traded_signals: int
    total_pnl: float
    win_rate: float
    avg_pnl_per_trade: float
    sharpe_ratio: float
    max_drawdown: float
    latency_p50_ms: float = 0.0
    latency_p99_ms: float = 0.0


@dataclass
class ABTestResult:
    """A/B 테스트 결과 — ML vs Baseline."""

    baseline: BacktestResult
    ml_enhanced: BacktestResult
    pnl_delta: float  # ml - baseline
    pnl_delta_pct: float  # (ml - baseline) / |baseline| * 100
    win_rate_delta: float
    sharpe_delta: float
    ml_improves: bool  # pnl_delta > 0


class MLSignalBacktester:
    """Walk-forward ML 시그널 백테스트.

    기존 시그널(baseline) vs ML 스코어 보강 시그널 비교.
    """

    def __init__(
        self,
        ml_scorer: Any | None = None,
        score_threshold: float = 0.5,
        fee_bps: float = 10.0,
    ) -> None:
        self._ml_scorer = ml_scorer
        self._score_threshold = score_threshold
        self._fee_bps = fee_bps

    def run_baseline(
        self,
        signals: list[dict],
        prices: np.ndarray,
    ) -> BacktestResult:
        """기존 시그널 기반 백테스트 (ML 필터 없음).

        Parameters:
            signals: list of {"timestamp_idx": int, "spread_bps": float, "direction": 1|-1}
            prices: price series aligned with timestamps
        """
        return self._simulate(signals, prices, use_ml=False, label="baseline")

    def run_ml_enhanced(
        self,
        signals: list[dict],
        prices: np.ndarray,
        features: np.ndarray,
    ) -> BacktestResult:
        """ML 스코어 보강 시그널 백테스트.

        ML score >= threshold인 시그널만 거래.
        """
        return self._simulate(
            signals, prices, use_ml=True, features=features, label="ml_enhanced"
        )

    def ab_test(
        self,
        signals: list[dict],
        prices: np.ndarray,
        features: np.ndarray | None = None,
    ) -> ABTestResult:
        """A/B 테스트: baseline vs ML-enhanced."""
        baseline = self.run_baseline(signals, prices)

        if features is not None and self._ml_scorer is not None:
            ml = self.run_ml_enhanced(signals, prices, features)
        else:
            # No ML scorer → simulate with random filtering for comparison
            ml = self._simulate(signals, prices, use_ml=False, label="ml_enhanced")

        pnl_delta = ml.total_pnl - baseline.total_pnl
        base_abs = abs(baseline.total_pnl) if baseline.total_pnl != 0 else 1.0
        pnl_delta_pct = (pnl_delta / base_abs) * 100

        result = ABTestResult(
            baseline=baseline,
            ml_enhanced=ml,
            pnl_delta=pnl_delta,
            pnl_delta_pct=pnl_delta_pct,
            win_rate_delta=ml.win_rate - baseline.win_rate,
            sharpe_delta=ml.sharpe_ratio - baseline.sharpe_ratio,
            ml_improves=pnl_delta > 0,
        )

        logger.info(
            "ml_backtest: A/B result — baseline_pnl=%.4f, ml_pnl=%.4f, delta=%.4f (%.1f%%)",
            baseline.total_pnl, ml.total_pnl, pnl_delta, pnl_delta_pct,
        )
        return result

    def walk_forward(
        self,
        signals: list[dict],
        prices: np.ndarray,
        features: np.ndarray | None = None,
        n_folds: int = 5,
    ) -> list[ABTestResult]:
        """Walk-forward A/B 테스트.

        시계열을 n_folds로 분할, 각 fold에서 A/B 비교.
        """
        n = len(signals)
        fold_size = n // n_folds
        results = []

        for fold in range(n_folds):
            start = fold * fold_size
            end = min(start + fold_size, n)
            if end <= start:
                continue

            fold_signals = signals[start:end]
            fold_features = features[start:end] if features is not None else None

            result = self.ab_test(fold_signals, prices, fold_features)
            results.append(result)

        return results

    def _simulate(
        self,
        signals: list[dict],
        prices: np.ndarray,
        use_ml: bool = False,
        features: np.ndarray | None = None,
        label: str = "unknown",
    ) -> BacktestResult:
        """시뮬레이션 엔진."""
        pnls: list[float] = []
        latencies: list[float] = []
        traded = 0

        for i, sig in enumerate(signals):
            idx = sig.get("timestamp_idx", i)
            spread_bps = sig.get("spread_bps", 0.0)
            direction = sig.get("direction", 1)

            # ML filter
            if use_ml and self._ml_scorer is not None and features is not None:
                start = time.perf_counter()
                feat = features[i] if i < len(features) else np.zeros(20)
                score = self._ml_scorer.predict_signal(feat)
                latencies.append((time.perf_counter() - start) * 1000)

                if score < self._score_threshold:
                    continue

            # PnL: spread - fees
            net_pnl = (spread_bps - self._fee_bps) * direction / 10000.0
            if idx < len(prices) and prices[idx] > 0:
                net_pnl *= float(prices[idx])

            pnls.append(net_pnl)
            traded += 1

        total_pnl = sum(pnls) if pnls else 0.0
        wins = sum(1 for p in pnls if p > 0)
        win_rate = wins / len(pnls) if pnls else 0.0
        avg_pnl = total_pnl / len(pnls) if pnls else 0.0

        # Sharpe
        if len(pnls) >= 2:
            pnl_arr = np.array(pnls)
            sharpe = float(np.mean(pnl_arr) / np.std(pnl_arr)) if np.std(pnl_arr) > 1e-12 else 0.0
        else:
            sharpe = 0.0

        # Max drawdown
        if pnls:
            cumulative = np.cumsum(pnls)
            peak = np.maximum.accumulate(cumulative)
            drawdowns = peak - cumulative
            max_dd = float(np.max(drawdowns))
        else:
            max_dd = 0.0

        lat_sorted = sorted(latencies) if latencies else [0.0]
        p50 = lat_sorted[len(lat_sorted) // 2]
        p99 = lat_sorted[min(int(len(lat_sorted) * 0.99), len(lat_sorted) - 1)]

        return BacktestResult(
            strategy=label,
            total_signals=len(signals),
            traded_signals=traded,
            total_pnl=total_pnl,
            win_rate=win_rate,
            avg_pnl_per_trade=avg_pnl,
            sharpe_ratio=sharpe,
            max_drawdown=max_dd,
            latency_p50_ms=p50,
            latency_p99_ms=p99,
        )
