"""ML-based auto-tuner for strategy parameter optimization."""
from src.tuning.adaptive_threshold import AdaptiveThreshold
from src.tuning.backtest import BacktestEngine, BacktestResult, StrategyParams
from src.tuning.data_loader import DataLoader, OHLCVWindow, SpreadRecord
from src.tuning.evaluator import EvaluationReport, OutOfSampleEvaluator
from src.tuning.optimizer import ObjectiveType, TunerConfig, WalkForwardOptimizer
from src.tuning.regime_detector import (
    HMM_REGIME_MAP,
    THRESHOLD_TO_HMM,
    HMMRegimeDetector,
    MarketRegime,
    RegimeDetector,
)

__all__ = [
    "AdaptiveThreshold",
    "BacktestEngine",
    "BacktestResult",
    "StrategyParams",
    "DataLoader",
    "OHLCVWindow",
    "SpreadRecord",
    "EvaluationReport",
    "OutOfSampleEvaluator",
    "ObjectiveType",
    "TunerConfig",
    "WalkForwardOptimizer",
    "MarketRegime",
    "RegimeDetector",
    "HMMRegimeDetector",
    "HMM_REGIME_MAP",
    "THRESHOLD_TO_HMM",
]
