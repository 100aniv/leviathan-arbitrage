"""WS-A4 seed + WS-B: TCA observation layer feeding pre-trade cost model.

WS-A4 layer: Collects TCA slippage observations per (strategy, exchange) and
exposes p95 via Prometheus gauge. No threshold adjustment.

WS-B layer: `compute_dynamic_min_spread` consumes the observed p95 + taker
fee rates + funding buffer + profit margin to produce the effective
pre-trade min_spread_bps per (strategy, exchange_pair). Falls back to the
static engine.json `strategy_filters.*_min_spread_bps` when observation count
is insufficient (cold start).

Formula (all units in bps):
    dynamic_min = fee_roundtrip + p95_slippage + funding_buffer + profit_margin

Safety floor:
    dynamic_min >= fee_roundtrip + 1 bp

Rationale: observation must remain decoupled from threshold adjustment so the
pre-trade cost model can be tuned independently (see PHOENIX_PLAN §5 /
hidden-cuddling-pascal.md WS-B).
"""
from __future__ import annotations

import logging
import math
import time
from collections import defaultdict, deque
from decimal import Decimal
from typing import Any, Deque, Dict, Tuple

logger = logging.getLogger(__name__)

# WS-B defaults (overridable via engine.json strategy_filters.*)
_DEFAULT_MIN_SAMPLES = 20
_DEFAULT_FUNDING_BUFFER_BPS = Decimal("5")
_DEFAULT_MARGIN_BPS = Decimal("5")
_DEFAULT_STATIC_FALLBACK_BPS = Decimal("27")
_LOG_RATE_LIMIT_S = 3600.0  # once per hour per strategy-pair key


class TCAAdaptiveFeedback:
    """Rolling TCA slippage observations + dynamic min_spread computation.

    Usage:
        fb = TCAAdaptiveFeedback(window=100, fee_model=fee_model)
        fb.record_observation("futures_futures_v1", 12.5, exchange="binance_futures")
        p95 = fb.p95_bps("futures_futures_v1", "binance_futures")
        dyn = fb.compute_dynamic_min_spread(
            strategy_id="futures_futures_v1",
            exchange_pair=("binance_futures", "bitget_futures"),
        )

    WS-A4 scope: observation + p95 gauge export.
    WS-B scope: compute_dynamic_min_spread using p95 + fee + funding + margin.
    """

    def __init__(
        self,
        window: int = 100,
        fee_model: Any = None,
        min_samples: int = _DEFAULT_MIN_SAMPLES,
        funding_buffer_bps: Decimal = _DEFAULT_FUNDING_BUFFER_BPS,
        margin_bps: Decimal = _DEFAULT_MARGIN_BPS,
        static_fallback_bps: Decimal = _DEFAULT_STATIC_FALLBACK_BPS,
    ) -> None:
        self.window = window
        self._fee_model = fee_model
        self._min_samples = int(min_samples)
        self._funding_buffer_bps = Decimal(str(funding_buffer_bps))
        self._margin_bps = Decimal(str(margin_bps))
        self._static_fallback_bps = Decimal(str(static_fallback_bps))
        # (strategy, exchange) -> deque[slippage_bps]
        self._observations: Dict[Tuple[str, str], Deque[float]] = defaultdict(
            lambda: deque(maxlen=window)
        )
        # Last INFO log timestamp per (strategy, exchange_pair) — rate-limit hourly
        self._last_log_ts: Dict[Tuple[str, Tuple[str, str]], float] = {}

    def record_observation(
        self,
        strategy: str,
        slippage_bps: float,
        exchange: str = "unknown",
    ) -> None:
        """Append one TCA slippage observation and refresh p95 gauge.

        `slippage_bps` may be negative (favorable fill) or positive (leakage).
        Negative values are clipped to 0.0 for p95 computation — only adverse
        slippage influences the cost model (matches live.py adverse-only IS).
        """
        if not strategy:
            strategy = "unknown"
        if not exchange:
            exchange = "unknown"
        key = (strategy, exchange)
        # Clip favorable slippage to 0 — only adverse slippage widens min_spread.
        bps = max(0.0, float(slippage_bps))
        self._observations[key].append(bps)
        # Refresh gauge after each observation (cheap, p95 is O(n log n) on ≤100 items).
        try:
            from src.infra.metrics import OBSERVED_SLIPPAGE_P95_BPS

            p95 = self.p95_bps(strategy, exchange)
            OBSERVED_SLIPPAGE_P95_BPS.labels(
                strategy=strategy, exchange=exchange,
            ).set(p95)
            logger.debug(
                "tca_feedback_recorded strategy=%s exchange=%s slippage_bps=%.2f p95=%.2f n=%d",
                strategy, exchange, bps, p95, len(self._observations[key]),
            )
        except Exception as exc:
            # Prometheus not available / labels error — non-fatal for observation.
            logger.debug("tca_feedback_metric_export_failed error=%s", exc)

    def p95_bps(self, strategy: str, exchange: str) -> float:
        """Return observed slippage p95 (bps) for (strategy, exchange). 0.0 if no data."""
        key = (strategy, exchange)
        data = list(self._observations.get(key, ()))
        n = len(data)
        if n == 0:
            return 0.0
        if n == 1:
            return data[0]
        data_sorted = sorted(data)
        k = 0.95 * (n - 1)
        f = math.floor(k)
        c = min(f + 1, n - 1)
        if f == c:
            return data_sorted[f]
        return data_sorted[f] + (k - f) * (data_sorted[c] - data_sorted[f])

    def sample_count(self, strategy: str, exchange: str) -> int:
        """Number of observations currently held for (strategy, exchange)."""
        return len(self._observations.get((strategy, exchange), ()))

    # ------------------------------------------------------------------
    # WS-B: dynamic min_spread computation
    # ------------------------------------------------------------------

    def _taker_roundtrip_bps(self, exchange_pair: Tuple[str, str]) -> Decimal:
        """Return combined taker fee (bps) for round-trip across the exchange pair.

        Uses FeeModel.round_trip_fee_rate (fraction) * 10000 = bps. When FeeModel
        is unavailable we fall back to a conservative 10 bps (Binance 4 + Bitget 6).
        """
        if self._fee_model is None:
            return Decimal("10")
        try:
            rate_frac = self._fee_model.round_trip_fee_rate(
                exchange_pair[0], exchange_pair[1]
            )
            return Decimal(str(rate_frac)) * Decimal("10000")
        except Exception as exc:
            logger.debug(
                "cost_feedback.fee_lookup_failed pair=%s error=%s — falling back to 10bps",
                exchange_pair, exc,
            )
            return Decimal("10")

    def compute_dynamic_min_spread(
        self,
        strategy_id: str,
        exchange_pair: Tuple[str, str],
        static_fallback_bps: Decimal | None = None,
    ) -> Decimal:
        """Compute per-(strategy, exchange-pair) min_spread_bps from observations.

        Formula:
            dynamic = fee_roundtrip + p95_slippage + funding_buffer + margin
            dynamic >= fee_roundtrip + 1 (safety floor)

        If either leg has fewer than `_min_samples` observations, fall back to
        the static engine.json value. Emits a Prometheus gauge + rate-limited
        INFO log once per hour per (strategy, exchange_pair).
        """
        fallback = Decimal(str(static_fallback_bps)) if static_fallback_bps is not None else self._static_fallback_bps
        buy_ex, sell_ex = exchange_pair
        n_buy = self.sample_count(strategy_id, buy_ex)
        n_sell = self.sample_count(strategy_id, sell_ex)
        # Cold start: either leg missing adequate samples → fall back to static.
        if n_buy < self._min_samples or n_sell < self._min_samples:
            self._export_gauge(strategy_id, exchange_pair, fallback)
            return fallback

        # p95 for each leg, then take the max — the tighter constraint wins.
        p95_buy = Decimal(str(self.p95_bps(strategy_id, buy_ex)))
        p95_sell = Decimal(str(self.p95_bps(strategy_id, sell_ex)))
        p95 = max(p95_buy, p95_sell)

        fee = self._taker_roundtrip_bps(exchange_pair)
        total = fee + p95 + self._funding_buffer_bps + self._margin_bps

        # Safety floor: never allow threshold below fee_roundtrip + 1 bp.
        safety_floor = fee + Decimal("1")
        if total < safety_floor:
            total = safety_floor

        self._export_gauge(strategy_id, exchange_pair, total)
        self._maybe_log(strategy_id, exchange_pair, total, fee, p95)
        return total

    def _export_gauge(
        self,
        strategy_id: str,
        exchange_pair: Tuple[str, str],
        value_bps: Decimal,
    ) -> None:
        """Export computed threshold to Prometheus (best-effort)."""
        try:
            from src.infra.metrics import DYNAMIC_MIN_SPREAD_BPS

            pair_label = f"{exchange_pair[0]}:{exchange_pair[1]}"
            DYNAMIC_MIN_SPREAD_BPS.labels(
                strategy=strategy_id, exchange_pair=pair_label,
            ).set(float(value_bps))
        except Exception as exc:
            logger.debug("cost_feedback.gauge_export_failed error=%s", exc)

    def _maybe_log(
        self,
        strategy_id: str,
        exchange_pair: Tuple[str, str],
        total_bps: Decimal,
        fee_bps: Decimal,
        p95_bps: Decimal,
    ) -> None:
        """Emit INFO log once per hour per (strategy, exchange_pair)."""
        key = (strategy_id, exchange_pair)
        now = time.monotonic()
        last = self._last_log_ts.get(key, 0.0)
        if (now - last) < _LOG_RATE_LIMIT_S:
            return
        self._last_log_ts[key] = now
        logger.info(
            "dynamic_min_spread strategy=%s exchange_pair=%s value=%.1fbps "
            "breakdown={fee:%.1f,slip_p95:%.1f,fund:%.1f,margin:%.1f}",
            strategy_id,
            f"{exchange_pair[0]}_{exchange_pair[1]}",
            float(total_bps),
            float(fee_bps),
            float(p95_bps),
            float(self._funding_buffer_bps),
            float(self._margin_bps),
        )
