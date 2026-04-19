"""WS-C1: /api/v1/pnl/attributed — 7-layer TCA PnL attribution.

Aggregates PnL sources that the dashboard needs to display separately so the
operator can see where the engine's reported `total_pnl` differs from the
exchange's ground truth.

Layers:
    1. realized_exchange  — Prometheus `leviathan_exchange_income_total_usdt`
                             with income_type=REALIZED_PNL per exchange.
    2. unrealized         — live mark-to-market from adapter `get_positions()`.
    3. commission         — same metric, income_type=COMMISSION.
    4. funding            — same metric, income_type=FUNDING_FEE.
    5. slippage_estimated — engine-side _strategy_slippage_window rolling sum.
    6. basis_capture      — placeholder (WS-D will populate from close prices).
    7. reconciliation_variance_pct — `leviathan_pnl_reconciliation_variance_pct`.

Plus:
    - engine_total_pnl — from LiveMode `_stats.total_pnl`.
    - grand_total      — sum of (realized_exchange + unrealized + commission
                          + funding + slippage_estimated + basis_capture) across
                          all exchanges/symbols; reconciliation_variance_pct is
                          diagnostic only and not summed.

Graceful degrade: any missing source returns `{}` or `0.0`, never raises 500.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from src.api.auth import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

_FUTURES_EXCHANGES = ("binance_futures", "bitget_futures")
_INCOME_TYPES = ("REALIZED_PNL", "COMMISSION", "FUNDING_FEE")


def _read_counter_value(counter: Any, labels: dict[str, str]) -> float:
    """Read a Prometheus Counter value for specific labels. Returns 0.0 if missing."""
    try:
        child = counter.labels(**labels)
        # prometheus_client Counter: _value is a ValueClass with .get()
        val = getattr(child, "_value", None)
        if val is not None:
            return float(val.get())
        # Histogram/Gauge path (should not happen for Counter)
        return 0.0
    except Exception as exc:  # noqa: BLE001
        logger.debug("pnl_attributed.counter_read_failed labels=%s err=%s", labels, exc)
        return 0.0


def _read_gauge_value(gauge: Any, labels: dict[str, str]) -> float:
    """Read a Prometheus Gauge value for specific labels. Returns 0.0 if missing."""
    try:
        child = gauge.labels(**labels)
        val = getattr(child, "_value", None)
        if val is not None:
            return float(val.get())
        return 0.0
    except Exception as exc:  # noqa: BLE001
        logger.debug("pnl_attributed.gauge_read_failed labels=%s err=%s", labels, exc)
        return 0.0


def _exchange_income_by_type(income_type: str) -> dict[str, float]:
    """Read EXCHANGE_INCOME_TOTAL counters for each futures exchange.

    Prometheus Counter stores absolute value (ExchangeIncomeFetcher records
    abs(amount) because Counters can only increment). Return the recorded
    magnitude per exchange; sign interpretation lives in the dashboard layer.
    """
    result: dict[str, float] = {}
    try:
        from src.infra.metrics import EXCHANGE_INCOME_TOTAL
    except Exception as exc:  # noqa: BLE001
        logger.warning("pnl_attributed.metrics_import_failed err=%s", exc)
        return result
    for eid in _FUTURES_EXCHANGES:
        val = _read_counter_value(
            EXCHANGE_INCOME_TOTAL,
            {"exchange": eid, "income_type": income_type},
        )
        # Commission/funding are costs — report signed (negative) for readability.
        if income_type in ("COMMISSION", "FUNDING_FEE"):
            val = -val
        result[eid] = round(val, 4)
    return result


def _reconciliation_variance() -> dict[str, float]:
    """Read PNL_RECONCILIATION_VARIANCE_PCT gauge per exchange."""
    result: dict[str, float] = {}
    try:
        from src.infra.metrics import PNL_RECONCILIATION_VARIANCE_PCT
    except Exception as exc:  # noqa: BLE001
        logger.warning("pnl_attributed.metrics_import_failed err=%s", exc)
        return result
    for eid in _FUTURES_EXCHANGES:
        result[eid] = round(
            _read_gauge_value(PNL_RECONCILIATION_VARIANCE_PCT, {"exchange": eid}),
            4,
        )
    return result


def _unrealized_by_symbol(ctx: Any) -> dict[str, dict[str, float]]:
    """Collect live unrealized PnL from engine adapters grouped by symbol.

    Uses the already-connected exchange adapters held by the engine — does
    NOT open new connections here to avoid blocking the request path.
    """
    unrealized: dict[str, dict[str, float]] = {}
    engine = getattr(ctx, "engine", None)
    if engine is None or not hasattr(engine, "_exchanges"):
        return unrealized

    for eid in _FUTURES_EXCHANGES:
        adapter = engine._exchanges.get(eid) if hasattr(engine, "_exchanges") else None
        if adapter is None:
            continue
        # Prefer cached positions from engine state to avoid network IO.
        positions_snapshot: list[Any] = []
        pm = getattr(ctx, "position_manager", None)
        if pm is not None:
            try:
                positions_snapshot = [
                    p for p in pm.get_all_positions() if p.exchange_id == eid
                ]
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "pnl_attributed.position_manager_failed eid=%s err=%s", eid, exc,
                )
        for pos in positions_snapshot:
            try:
                sym = str(pos.symbol)
                leg_key = "binance_leg" if eid.startswith("binance") else "bitget_leg"
                slot = unrealized.setdefault(sym, {"binance_leg": 0.0, "bitget_leg": 0.0, "net": 0.0})
                slot[leg_key] = round(float(pos.unrealized_pnl), 4)
                slot["net"] = round(slot["binance_leg"] + slot["bitget_leg"], 4)
            except Exception as exc:  # noqa: BLE001
                logger.debug("pnl_attributed.position_parse_failed err=%s", exc)
    return unrealized


def _slippage_estimated(ctx: Any) -> dict[str, float]:
    """Sum recent slippage_bps per strategy × average notional → USD estimate.

    Best-effort: the rolling window stores slippage_bps values. Without per-trade
    notional retained we approximate by summing bps and multiplying by the
    engine's recent average position size. For now we return the bps-sum scaled
    by 1 USD notional, which the dashboard can rescale — this preserves the
    shape of the data while sidestepping a larger refactor.
    """
    result: dict[str, float] = {}
    engine = getattr(ctx, "engine", None)
    live = getattr(engine, "_live_mode", None) if engine is not None else None
    if live is None:
        return result
    window = getattr(live, "_strategy_slippage_window", None)
    if not isinstance(window, dict):
        return result
    for sid, dq in window.items():
        try:
            total_bps = sum(float(x) for x in dq)
            # Convention from plan example: negative value since slippage is a cost.
            result[sid] = round(-total_bps / 10_000.0, 6)
        except Exception as exc:  # noqa: BLE001
            logger.debug("pnl_attributed.slippage_sum_failed sid=%s err=%s", sid, exc)
    return result


def _engine_total_pnl(ctx: Any) -> float:
    """Read engine's reported total_pnl from LiveMode stats or PositionManager fallback."""
    engine = getattr(ctx, "engine", None)
    live = getattr(engine, "_live_mode", None) if engine is not None else None
    if live is not None:
        stats = getattr(live, "_stats", None)
        if stats is not None and hasattr(stats, "total_pnl"):
            try:
                return round(float(stats.total_pnl), 4)
            except Exception:  # noqa: BLE001
                pass
    # Fallback: PositionManager realized+unrealized
    pm = getattr(ctx, "position_manager", None)
    if pm is not None:
        try:
            positions = list(pm.get_all_positions())
            r = float(sum(p.realized_pnl for p in positions))
            u = float(sum(p.unrealized_pnl for p in positions))
            return round(r + u, 4)
        except Exception:  # noqa: BLE001
            pass
    try:
        return round(float(ctx.realized_pnl) + float(ctx.unrealized_pnl), 4)
    except Exception:  # noqa: BLE001
        return 0.0


async def _ledger_live_pnl(ctx: Any) -> dict[str, Any] | None:
    """Read Path-B PnLLedger if injected; returns None when unavailable."""
    engine = getattr(ctx, "engine", None)
    ledger = getattr(engine, "_pnl_ledger", None) if engine is not None else None
    if ledger is None:
        return None
    try:
        raw = await ledger.get_live_pnl_usd()
    except Exception as exc:  # noqa: BLE001
        logger.warning("pnl_attributed.ledger_read_failed err=%s", exc)
        return None
    ts = raw.get("last_reconciled_ts")
    return {
        "exchange_pnl_usd": float(raw.get("exchange_pnl_usd", 0) or 0),
        "engine_pnl_usd": float(raw.get("engine_pnl_usd", 0) or 0),
        "divergence_usd": float(raw.get("divergence_usd", 0) or 0),
        "status": str(raw.get("status", "pending")),
        "last_reconciled_ts": ts.isoformat() if hasattr(ts, "isoformat") else ts,
    }


@router.get("/pnl/attributed", dependencies=[Depends(require_auth)])
async def get_pnl_attributed(request: Request) -> JSONResponse:
    """Return 7-layer PnL attribution suitable for the dashboard /pnl page."""
    ctx = request.app.state.engine_context

    realized_exchange = _exchange_income_by_type("REALIZED_PNL")
    commission = _exchange_income_by_type("COMMISSION")
    funding = _exchange_income_by_type("FUNDING_FEE")
    unrealized = _unrealized_by_symbol(ctx)
    slippage_est = _slippage_estimated(ctx)
    basis_capture: dict[str, float] = {}  # WS-D will populate
    recon_variance = _reconciliation_variance()
    # Path-B Day-1: PnLLedger is the canonical operator-facing PnL source.
    # engine_total_pnl remains as the diagnostic engine-TCA number.
    ledger_live = await _ledger_live_pnl(ctx)
    engine_total = (
        round(ledger_live["engine_pnl_usd"], 4)
        if ledger_live is not None
        else _engine_total_pnl(ctx)
    )

    # grand_total: sum the six accounting layers (variance is diagnostic only).
    grand = 0.0
    grand += sum(realized_exchange.values())
    grand += sum(s.get("net", 0.0) for s in unrealized.values())
    grand += sum(commission.values())
    grand += sum(funding.values())
    grand += sum(slippage_est.values())
    grand += sum(basis_capture.values())

    return JSONResponse({
        "realized_exchange": realized_exchange,
        "unrealized": unrealized,
        "commission": commission,
        "funding": funding,
        "slippage_estimated": slippage_est,
        "basis_capture": basis_capture,
        "reconciliation_variance_pct": recon_variance,
        "engine_total_pnl": engine_total,
        "grand_total": round(grand, 4),
        # Path-B Day-1: canonical operator PnL view fed by PnLLedger.
        "ledger": ledger_live,
    })
