"""TCA-based parameter auto-calibration.

US-333: Uses TCA P95 Implementation Shortfall data to recalibrate
min_profitability and slippage_buffer parameters.
"""
from __future__ import annotations

import json
import logging
from decimal import Decimal
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class TCACalibrator:
    """Auto-calibrate trading parameters from TCA data.

    Uses P95 Implementation Shortfall to set conservative slippage buffers,
    ensuring the engine only trades when expected profit exceeds observed costs.
    """

    def __init__(
        self,
        tca_analyzer: Any = None,
        safety_margin_bps: float = 2.0,
        min_samples: int = 20,
    ) -> None:
        self._tca = tca_analyzer
        self._safety_margin_bps = safety_margin_bps
        self._min_samples = min_samples

    def calibrate(self) -> dict:
        """Calculate calibrated parameters from TCA data.

        Returns dict with recommended slippage_buffer_bps and min_edge_bps,
        or error if insufficient data.
        """
        if self._tca is None:
            return {"error": "No TCA analyzer available"}

        summary = self._tca.get_summary()
        sample_count = summary.get("sample_count", 0)

        if sample_count < self._min_samples:
            return {
                "error": f"Insufficient samples: {sample_count} < {self._min_samples}",
                "sample_count": sample_count,
            }

        is_p95 = summary.get("is_p95_bps", 0.0)
        is_p50 = summary.get("is_p50_bps", 0.0)

        # Recommended slippage_buffer = P95 IS + safety margin
        recommended_buffer = round(is_p95 + self._safety_margin_bps, 1)

        # Recommended min_edge = P50 IS + buffer (must exceed typical slippage)
        recommended_min_edge = round(is_p50 + recommended_buffer, 1)

        result = {
            "is_p50_bps": is_p50,
            "is_p95_bps": is_p95,
            "safety_margin_bps": self._safety_margin_bps,
            "recommended_slippage_buffer_bps": recommended_buffer,
            "recommended_min_edge_bps": recommended_min_edge,
            "sample_count": sample_count,
        }

        # Per-strategy calibration if available
        try:
            all_strat = self._tca.get_all_strategy_summaries()
            if all_strat:
                result["per_strategy"] = {}
                for sid, s in all_strat.items():
                    if "error" not in s and s.get("sample_count", 0) >= 5:
                        s_p95 = s.get("is_p95_bps", 0.0)
                        result["per_strategy"][sid] = {
                            "is_p95_bps": s_p95,
                            "recommended_buffer_bps": round(s_p95 + self._safety_margin_bps, 1),
                        }
        except Exception:
            pass

        logger.info(
            "tca_calibration_complete samples=%d buffer=%.1f min_edge=%.1f",
            sample_count, recommended_buffer, recommended_min_edge,
        )
        return result

    def apply_to_params(self, params_path: str | Path = "config/strategy_params.json") -> dict:
        """Calibrate and write updated slippage_buffer to strategy_params.json."""
        cal = self.calibrate()
        if "error" in cal:
            return cal

        path = Path(params_path)
        if not path.exists():
            return {"error": f"Params file not found: {path}"}

        try:
            params = json.loads(path.read_text())
            # Update cross_exchange slippage_buffer
            ce = params.get("cross_exchange", {})
            old_buffer = ce.get("slippage_buffer_bps", 0)
            ce["slippage_buffer_bps"] = cal["recommended_slippage_buffer_bps"]
            params["cross_exchange"] = ce
            path.write_text(json.dumps(params, indent=2) + "\n")
            cal["applied"] = True
            cal["old_buffer_bps"] = old_buffer
            logger.info("tca_calibration_applied old=%.1f new=%.1f", old_buffer, cal["recommended_slippage_buffer_bps"])
        except Exception as exc:
            cal["applied"] = False
            cal["apply_error"] = str(exc)

        return cal
