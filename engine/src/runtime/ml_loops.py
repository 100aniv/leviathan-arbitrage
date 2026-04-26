"""ML 백그라운드 루프 — Phase 4 main.py 모듈화 1단계 (2026-04-26).

Extracted from main.py:2986-3245 (260 LOC):
- regime_detect_loop      (US-173, 60s 주기)
- adaptive_threshold_loop (US-174, 1h 주기)
- hmm_training_loop       (US-251, 7일 주기)
- xgb_training_loop       (US-252, 24h 주기)

각 함수는 ``engine: "Engine"`` 인스턴스를 첫 인자로 받음. ``Engine`` 메서드는
이 모듈 함수로 위임 (thin wrapper).
"""
from __future__ import annotations

import asyncio
import logging
import os
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.main import Engine

from src.core.config import get_settings

logger = logging.getLogger(__name__)


async def regime_detect_loop(engine: "Engine") -> None:
    """US-173: 60s periodic regime detection using recent PnL returns."""
    while engine.state.running:
        try:
            await asyncio.sleep(60.0)
            if engine._regime_detector is None:
                break
            # Build returns series from PnL deltas (not cumulative snapshots)
            pnl_now = float(engine._total_pnl)
            if not hasattr(engine, "_regime_pnl_history"):
                engine._regime_pnl_history: list[float] = []
                engine._regime_last_pnl: float = 0.0
            pnl_delta = pnl_now - engine._regime_last_pnl
            engine._regime_last_pnl = pnl_now
            if pnl_delta != 0.0:
                engine._regime_pnl_history.append(pnl_delta)
            # Keep last 60 data points (1 hour at 60s intervals)
            engine._regime_pnl_history = engine._regime_pnl_history[-60:]
            returns = engine._regime_pnl_history.copy()
            if returns:
                try:
                    # US-254 fix: HMMRegimeDetector has predict(), RegimeDetector has detect()
                    if hasattr(engine._regime_detector, "detect"):
                        engine._regime_detector.detect(returns)
                    elif hasattr(engine._regime_detector, "predict"):
                        engine._regime_detector.predict(returns)
                except Exception:
                    pass
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning("regime_detect_loop error: %s", exc)


async def adaptive_threshold_loop(engine: "Engine") -> None:
    """US-174: 1-hour periodic AdaptiveThreshold adjustment.

    Reads current win_rate from paper stats (if available) or trade history,
    calls AdaptiveThreshold.adjust(), and updates SignalConfig.min_edge.
    """
    INTERVAL_S = get_settings().operational.adaptive_threshold_interval_s
    while engine.state.running:
        try:
            # Bug 1-C: adjust() first so the first run is not delayed by INTERVAL_S
            if engine._adaptive_threshold is None:
                break

            # Collect win_rate and total_trades from available sources
            win_rate = 0.5
            total_trades = 0
            if engine._paper_mode is not None and hasattr(engine._paper_mode, "_stats"):
                stats = engine._paper_mode._stats
                total_trades = getattr(stats, "total_trades", 0)
                wins = getattr(stats, "profitable_trades", 0)
                if total_trades > 0:
                    win_rate = wins / total_trades
            elif engine.context.trade_history:
                total_trades = len(engine.context.trade_history)
                wins = sum(1 for t in engine.context.trade_history if t.get("pnl", 0) > 0)
                if total_trades > 0:
                    win_rate = wins / total_trades

            # US-201: compute expected_edge_bps and profit_factor from trade history
            expected_edge_bps: float | None = None
            profit_factor: float | None = None
            if engine.context.trade_history:
                trades = engine.context.trade_history
                winning_pnl = [t.get("pnl", 0.0) for t in trades if t.get("pnl", 0.0) > 0]
                losing_pnl = [t.get("pnl", 0.0) for t in trades if t.get("pnl", 0.0) < 0]
                n = len(trades)
                if n > 0:
                    wr = len(winning_pnl) / n
                    avg_win = sum(winning_pnl) / len(winning_pnl) if winning_pnl else 0.0
                    avg_loss = abs(sum(losing_pnl) / len(losing_pnl)) if losing_pnl else 0.0
                    expected_value_usd = (wr * avg_win) - ((1 - wr) * avg_loss)
                    avg_notional = (avg_win + avg_loss) / 2.0 if (avg_win + avg_loss) > 0 else 1.0
                    expected_edge_bps = (expected_value_usd / avg_notional) * 10000 if avg_notional > 0 else 0.0
                    if losing_pnl:
                        profit_factor = sum(winning_pnl) / abs(sum(losing_pnl)) if winning_pnl else 0.0

            new_edge_bps = engine._adaptive_threshold.adjust(
                "global",
                win_rate=win_rate,
                total_trades=total_trades,
                expected_edge_bps=expected_edge_bps,
                profit_factor=profit_factor,
            )

            # Update SignalConfig.min_edge at runtime
            if engine._signal_generator is not None and hasattr(engine._signal_generator, "_config"):
                engine._signal_generator._config.min_edge = (
                    Decimal(str(new_edge_bps)) / Decimal("10000")
                )
                logger.info(
                    "AdaptiveThreshold updated min_edge to %.2f bps (wr=%.1f%%, trades=%d)",
                    new_edge_bps, win_rate * 100, total_trades,
                )

            # Persist history to DB if available
            if engine._db_pool is not None:
                try:
                    async with engine._db_pool.pool.acquire() as conn:
                        await engine._adaptive_threshold.save_history(conn)
                except Exception:
                    pass
            await asyncio.sleep(INTERVAL_S)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning("adaptive_threshold_loop error: %s", exc)


async def hmm_training_loop(engine: "Engine") -> None:
    """US-251: Background HMM model retraining (7-day cycle).

    Flow: wait 7 days → acquire DB conn → scheduled_train() →
    Performance Gate (is_fitted) → save_model() to .cache/hmm/.
    Graceful fallback: no DB or import error → loop exits silently.
    """
    try:
        from src.ml.hmm_trainer import HMMTrainer
    except ImportError as exc:
        logger.info("hmm_trainer_skipped (import): %s", exc)
        return

    trainer = HMMTrainer(
        hmm_detector=engine._regime_detector if engine._regime_detector is not None else None,
    )
    os.makedirs(".cache/hmm", exist_ok=True)

    INTERVAL_S = 7 * 24 * 3600  # 7 days
    RETRY_S = 3600              # 1 hour on failure

    # US-251: train immediately if no model file exists (avoid 7-day delay on first run)
    _hmm_first_run = not (os.path.exists(".cache/hmm") and os.listdir(".cache/hmm"))

    while not engine._shutdown_event.is_set():
        if _hmm_first_run:
            _hmm_first_run = False
        else:
            try:
                await asyncio.sleep(INTERVAL_S)
            except asyncio.CancelledError:
                break

        if not engine._db_pool:
            logger.debug("hmm_training_skipped: no DB pool")
            continue

        try:
            async with engine._db_pool.pool.acquire() as conn:
                trained = await trainer.scheduled_train(conn)

            if trained:
                if trainer.detector.is_fitted:
                    trainer.save_model()
                    logger.info(
                        "HMM model deployed: samples=%d, saved to %s",
                        trainer._train_samples, trainer._cache_dir,
                    )
                else:
                    logger.warning("HMM model rejected: not fitted after training")
            else:
                logger.debug("HMM training skipped: not due or insufficient data")
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("HMM training failed: %s — retrying in 1h", exc)
            try:
                await asyncio.sleep(RETRY_S)
            except asyncio.CancelledError:
                break


async def xgb_training_loop(engine: "Engine") -> None:
    """US-252: Background XGBoost training + ONNX export (24h cycle).

    Flow: wait 24h → acquire DB conn → scheduled_train() →
    Performance Gate (best_score > 0.65) → ONNXExporter.export() →
    reload ONNXSignalScorer hot-swap.
    Graceful fallback: no DB / missing optional deps → loop exits silently.
    """
    try:
        from src.ml.xgb_trainer import XGBTrainer
        from src.ml.onnx_exporter import ONNXExporter
    except ImportError as exc:
        logger.info("xgb_trainer_skipped (import): %s", exc)
        return

    trainer = XGBTrainer()
    exporter = ONNXExporter()
    os.makedirs("models/latest", exist_ok=True)

    INTERVAL_S = 24 * 3600  # 24 hours
    RETRY_S = 3600          # 1 hour on failure
    ACCURACY_GATE = 0.65    # Performance Gate: AUC must exceed this

    # US-252: train immediately if no ONNX model exists (avoid 24-hour delay on first run)
    _xgb_first_run = not os.path.exists("models/latest/model.onnx")

    while not engine._shutdown_event.is_set():
        if _xgb_first_run:
            _xgb_first_run = False
        else:
            try:
                await asyncio.sleep(INTERVAL_S)
            except asyncio.CancelledError:
                break

        if not engine._db_pool:
            logger.debug("xgb_training_skipped: no DB pool")
            continue

        try:
            logger.info("xgb_training_loop_cycle_start")
            async with engine._db_pool.pool.acquire() as conn:
                trained = await trainer.scheduled_train(conn)

            if not trained:
                logger.debug("XGBoost training skipped: not due or insufficient data")
                continue

            if trainer.best_score < ACCURACY_GATE:
                logger.warning(
                    "XGBoost model rejected: best_score=%.4f < %.2f",
                    trainer.best_score, ACCURACY_GATE,
                )
                continue

            try:
                n_features = len(trainer._feature_names) if trainer._feature_names else 20
                onnx_path = exporter.export(
                    trainer.model,
                    n_features=n_features,
                    feature_names=trainer._feature_names or None,
                )
                if engine._signal_generator is not None:
                    scorer = getattr(engine._signal_generator, "_ml_scorer", None)
                    if scorer is not None and hasattr(scorer, "reload_model"):
                        scorer.reload_model(onnx_path)
                logger.info(
                    "XGBoost model deployed + ONNX exported: path=%s, best_score=%.4f",
                    onnx_path, trainer.best_score,
                )
            except Exception as export_exc:
                logger.warning("ONNX export failed (model trained but not exported): %s", export_exc)

        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("XGBoost training failed: %s — retrying in 1h", exc)
            try:
                await asyncio.sleep(RETRY_S)
            except asyncio.CancelledError:
                break
