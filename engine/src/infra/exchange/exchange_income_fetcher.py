"""Exchange income fetcher — WS-A2.

Polls exchange-reported income endpoints (Binance `/fapi/v1/income`,
Bitget `/api/v2/mix/account/bill` or UTA V3 equivalent) every 30s to capture
realized PnL, commission, funding fee, and transfer events. Aggregated into
Prometheus counters and appended to a daily CSV for audit.

Design notes:
- Futures adapters only — spot endpoints do not expose income events.
- Graceful degradation: exchange outage → WARNING log, no halt.
- Polling window overlaps slightly (35s window, 30s interval) to tolerate
  clock skew; deduplication keyed by (exchange, tran_id).
- Binance rate limit: 2400 req/min (this adds ~2 req/min per adapter).
- Bitget rate limit: 10 req/sec (this adds ~0.03 req/sec per adapter).
"""
from __future__ import annotations

import asyncio
import csv
import logging
import os
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.infra.metrics import (
    EXCHANGE_INCOME_FETCH_LATENCY,
    EXCHANGE_INCOME_POLLS_TOTAL,
    EXCHANGE_INCOME_TOTAL,
    PNL_RECONCILIATION_VARIANCE_PCT,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

POLL_INTERVAL_S = 30.0
POLL_WINDOW_MS = 35_000  # fetch last 35s (slight overlap to handle clock skew)
DEDUP_WINDOW_SIZE = 2000  # cap tran_id memory growth
CSV_DIR_DEFAULT = "logs"
CSV_PREFIX = "exchange_income"

# Binance /fapi/v1/income income types:
#   REALIZED_PNL, COMMISSION, FUNDING_FEE, TRANSFER, WELCOME_BONUS,
#   INSURANCE_CLEAR, REFERRAL_KICKBACK, COMMISSION_REBATE
# Bitget /api/v2/mix/account/bill businessType → normalized type mapping:
_BITGET_BUSINESS_TYPE_MAP = {
    "contract_settle_fee": "FUNDING_FEE",
    "open_long": "COMMISSION",
    "open_short": "COMMISSION",
    "close_long": "REALIZED_PNL",
    "close_short": "REALIZED_PNL",
    "force_close_long": "REALIZED_PNL",
    "force_close_short": "REALIZED_PNL",
    "burst_long_loss_query": "REALIZED_PNL",
    "burst_short_loss_query": "REALIZED_PNL",
    "trans_from_exchange": "TRANSFER",
    "trans_to_exchange": "TRANSFER",
    "trans_from_contract": "TRANSFER",
    "trans_to_contract": "TRANSFER",
    "trans_from_otc": "TRANSFER",
}


# ---------------------------------------------------------------------------
# ExchangeIncomeFetcher
# ---------------------------------------------------------------------------


class ExchangeIncomeFetcher:
    """Polls exchange income endpoints and emits Prometheus metrics + daily CSV.

    Usage:
        fetcher = ExchangeIncomeFetcher(adapters=[binance_fut, bitget_fut])
        await fetcher.start()
        ...
        await fetcher.stop()

    Args:
        adapters: List of NativeAdapter instances. Only futures adapters are
            polled (checked via `_market_type == 'futures'`).
        poll_interval_s: Override default 30s polling interval.
        csv_dir: Directory for daily CSV (default: 'logs').
        engine_pnl_getter: Optional callable returning engine's current
            total_pnl for reconciliation variance computation.
    """

    def __init__(
        self,
        adapters: list[Any],
        poll_interval_s: float = POLL_INTERVAL_S,
        csv_dir: str = CSV_DIR_DEFAULT,
        engine_pnl_getter: Any | None = None,
    ) -> None:
        self._adapters = adapters
        self._poll_interval_s = poll_interval_s
        self._csv_dir = Path(csv_dir)
        self._engine_pnl_getter = engine_pnl_getter
        self._tasks: list[asyncio.Task] = []
        # Dedup per-exchange: bounded deque of recent tran_ids
        self._seen_tran_ids: dict[str, deque[str]] = {}
        # 24h rolling sum per-exchange for reconciliation: list[(ts_ms, amount)]
        self._income_24h: dict[str, list[tuple[int, float]]] = {}
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Spawn one _poll_loop task per eligible futures adapter."""
        if self._running:
            logger.warning("exchange_income_fetcher.already_running")
            return
        self._running = True

        try:
            self._csv_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning(
                "exchange_income_fetcher.csv_dir_create_failed dir=%s err=%s",
                self._csv_dir, exc,
            )

        eligible = [
            a for a in self._adapters
            if getattr(a, "_market_type", "spot") == "futures"
        ]
        if not eligible:
            logger.info("exchange_income_fetcher.no_futures_adapters — skipping")
            return

        for adapter in eligible:
            eid = getattr(adapter, "exchange_id", "unknown")
            self._seen_tran_ids[eid] = deque(maxlen=DEDUP_WINDOW_SIZE)
            task = asyncio.create_task(
                self._poll_loop(adapter),
                name=f"exchange_income_{eid}",
            )
            self._tasks.append(task)

        logger.info(
            "exchange_income_fetcher.started adapters=%d interval_s=%.0f",
            len(self._tasks), self._poll_interval_s,
        )

    async def stop(self) -> None:
        """Cancel all polling tasks."""
        if not self._running:
            return
        self._running = False
        for task in self._tasks:
            if not task.done():
                task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()
        logger.info("exchange_income_fetcher.stopped")

    # ------------------------------------------------------------------
    # Polling loop
    # ------------------------------------------------------------------

    async def _poll_loop(self, adapter: Any) -> None:
        """Per-adapter polling loop."""
        eid = getattr(adapter, "exchange_id", "unknown")
        while self._running:
            start = time.monotonic()
            try:
                events = await self._fetch_income(adapter)
                if events:
                    self._emit(eid, events)
                    self._append_csv(eid, events)
                    logger.info(
                        "exchange_income_polled exchange=%s events=%d",
                        eid, len(events),
                    )
                self._update_reconciliation(eid)
                EXCHANGE_INCOME_POLLS_TOTAL.labels(exchange=eid, result="ok").inc()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                EXCHANGE_INCOME_POLLS_TOTAL.labels(exchange=eid, result="error").inc()
                logger.warning(
                    "exchange_income_poll_failed exchange=%s err=%s",
                    eid, exc,
                )
            finally:
                EXCHANGE_INCOME_FETCH_LATENCY.labels(exchange=eid).observe(
                    time.monotonic() - start,
                )

            # Sleep until next cycle (cancellation-safe)
            try:
                await asyncio.sleep(self._poll_interval_s)
            except asyncio.CancelledError:
                break

    # ------------------------------------------------------------------
    # Fetch + classify
    # ------------------------------------------------------------------

    async def _fetch_income(self, adapter: Any) -> list[dict]:
        """Dispatch to the appropriate exchange-specific fetcher."""
        eid = getattr(adapter, "exchange_id", "unknown")
        if "binance" in eid:
            return await self._fetch_binance(adapter)
        if "bitget" in eid:
            return await self._fetch_bitget(adapter)
        # Other futures exchanges: not yet supported
        return []

    async def _fetch_binance(self, adapter: Any) -> list[dict]:
        """Binance signed GET /fapi/v1/income (last POLL_WINDOW_MS)."""
        eid = getattr(adapter, "exchange_id", "binance_futures")
        now_ms = int(time.time() * 1000)
        params = {
            "startTime": now_ms - POLL_WINDOW_MS,
            "limit": 1000,
        }
        try:
            data = await adapter._signed_request(
                "GET", "/fapi/v1/income", params=params,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("binance_income_fetch_failed err=%s", exc)
            return []

        if not isinstance(data, list):
            return []

        events: list[dict] = []
        seen = self._seen_tran_ids.setdefault(eid, deque(maxlen=DEDUP_WINDOW_SIZE))
        for row in data:
            if not isinstance(row, dict):
                continue
            tran_id = str(row.get("tranId") or row.get("time", ""))
            if not tran_id or tran_id in seen:
                continue
            seen.append(tran_id)
            try:
                amount = float(row.get("income") or 0)
            except (TypeError, ValueError):
                continue
            events.append({
                "exchange": eid,
                "income_type": str(row.get("incomeType", "UNKNOWN")).upper(),
                "asset": str(row.get("asset", "")),
                "symbol": str(row.get("symbol", "")),
                "amount_usdt": amount,
                "tran_id": tran_id,
                "ts_ms": int(row.get("time") or now_ms),
            })
        return events

    async def _fetch_bitget(self, adapter: Any) -> list[dict]:
        """Bitget signed GET account bill (V2 /api/v2/mix/account/bill).

        V3 UTA equivalent is /api/v3/account/bills with `category` param.
        """
        eid = getattr(adapter, "exchange_id", "bitget_futures")
        now_ms = int(time.time() * 1000)

        is_uta = False
        try:
            is_uta = bool(getattr(adapter, "_is_uta", lambda: False)())
        except Exception:  # noqa: BLE001
            is_uta = False

        if is_uta:
            # Bitget UTA V3 official endpoint (BUG-218 fix — was /api/v3/account/bills
            # which returns 404; official docs: https://www.bitget.com/api-doc/uta/account/Get-Financial-Records).
            path = "/api/v3/account/financial-records"
            params: dict[str, Any] = {
                "category": "USDT-FUTURES",
                "startTime": now_ms - POLL_WINDOW_MS,
                "limit": 100,
            }
        else:
            path = "/api/v2/mix/account/bill"
            params = {
                "productType": "USDT-FUTURES",
                "startTime": now_ms - POLL_WINDOW_MS,
                "limit": 100,
            }

        try:
            resp = await adapter._request("GET", path, params=params, signed=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("bitget_bill_fetch_failed err=%s", exc)
            return []

        if not isinstance(resp, dict):
            return []
        raw_data = resp.get("data") or {}
        if isinstance(raw_data, dict):
            bills = raw_data.get("bills") or raw_data.get("list") or []
        elif isinstance(raw_data, list):
            bills = raw_data
        else:
            bills = []

        events: list[dict] = []
        seen = self._seen_tran_ids.setdefault(eid, deque(maxlen=DEDUP_WINDOW_SIZE))
        for row in bills:
            if not isinstance(row, dict):
                continue
            tran_id = str(row.get("billId") or row.get("id") or row.get("cTime", ""))
            if not tran_id or tran_id in seen:
                continue
            seen.append(tran_id)

            business_type = str(row.get("businessType", "")).lower()
            income_type = _BITGET_BUSINESS_TYPE_MAP.get(business_type, "UNKNOWN")

            # Bitget bill reports amount + separate fee field.
            try:
                amount = float(row.get("amount") or 0)
            except (TypeError, ValueError):
                amount = 0.0
            try:
                fee = float(row.get("fee") or 0)
            except (TypeError, ValueError):
                fee = 0.0

            ts_ms = 0
            try:
                ts_ms = int(row.get("cTime") or now_ms)
            except (TypeError, ValueError):
                ts_ms = now_ms

            base = {
                "exchange": eid,
                "asset": str(row.get("coin", "")),
                "symbol": str(row.get("symbol", "")),
                "tran_id": tran_id,
                "ts_ms": ts_ms,
            }

            events.append({
                **base,
                "income_type": income_type,
                "amount_usdt": amount,
            })
            # Emit separate COMMISSION event when fee is non-zero (close_* rows
            # carry both realized PnL and execution fee).
            if fee != 0.0 and income_type != "COMMISSION":
                events.append({
                    **base,
                    "tran_id": f"{tran_id}:fee",
                    "income_type": "COMMISSION",
                    "amount_usdt": fee,
                })
        return events

    # ------------------------------------------------------------------
    # Metrics + CSV + reconciliation
    # ------------------------------------------------------------------

    def _emit(self, exchange: str, events: list[dict]) -> None:
        """Aggregate events into Counter + 24h rolling buffer."""
        buf = self._income_24h.setdefault(exchange, [])
        now_ms = int(time.time() * 1000)
        for evt in events:
            itype = evt.get("income_type", "UNKNOWN")
            amount = float(evt.get("amount_usdt") or 0)
            # Prometheus Counter accepts only non-negative increments —
            # record absolute value to preserve volume; sign is captured in CSV.
            try:
                EXCHANGE_INCOME_TOTAL.labels(
                    exchange=exchange, income_type=itype,
                ).inc(abs(amount))
            except ValueError:
                pass
            buf.append((int(evt.get("ts_ms") or now_ms), amount))

        # Prune >24h entries
        cutoff = now_ms - 86_400_000
        self._income_24h[exchange] = [(t, a) for (t, a) in buf if t >= cutoff]

    def _append_csv(self, exchange: str, events: list[dict]) -> None:
        """Append events to daily CSV (one file per UTC day per engine)."""
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        path = self._csv_dir / f"{CSV_PREFIX}_{day}.csv"
        try:
            write_header = not path.exists()
            with path.open("a", newline="") as fh:
                writer = csv.writer(fh)
                if write_header:
                    writer.writerow([
                        "timestamp", "exchange", "income_type", "asset",
                        "symbol", "amount_usdt", "tran_id",
                    ])
                for evt in events:
                    ts_iso = datetime.fromtimestamp(
                        (evt.get("ts_ms") or 0) / 1000.0, tz=timezone.utc,
                    ).isoformat()
                    writer.writerow([
                        ts_iso,
                        evt.get("exchange", exchange),
                        evt.get("income_type", "UNKNOWN"),
                        evt.get("asset", ""),
                        evt.get("symbol", ""),
                        evt.get("amount_usdt", 0.0),
                        evt.get("tran_id", ""),
                    ])
        except OSError as exc:
            logger.warning(
                "exchange_income_csv_append_failed path=%s err=%s", path, exc,
            )

    def _update_reconciliation(self, exchange: str) -> None:
        """Compute engine vs exchange 24h variance gauge."""
        if self._engine_pnl_getter is None:
            return
        try:
            engine_pnl = float(self._engine_pnl_getter() or 0.0)
        except Exception:  # noqa: BLE001
            return
        buf = self._income_24h.get(exchange) or []
        exchange_pnl = sum(a for (_, a) in buf)
        # Variance as % of engine pnl (guard against divide-by-zero)
        denom = abs(engine_pnl) if abs(engine_pnl) > 1e-6 else 1.0
        variance_pct = (engine_pnl - exchange_pnl) / denom * 100.0
        try:
            PNL_RECONCILIATION_VARIANCE_PCT.labels(
                exchange=exchange,
            ).set(variance_pct)
        except ValueError:
            pass
