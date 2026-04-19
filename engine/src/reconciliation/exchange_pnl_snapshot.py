"""Exchange PnL snapshot fetcher + persister (Path-B Day-1).

Polls Binance ``/fapi/v1/income`` and Bitget
``/api/v3/account/financial-records`` (UTA) / ``/api/v2/mix/account/bill``
(legacy) every 60s. REUSES the low-level fetch + classification logic from
:mod:`src.infra.exchange.exchange_income_fetcher` so there is exactly one
place that knows how to sign, window, and de-duplicate income events.

Persistence strategy
--------------------

1. Preferred: the ``exchange_pnl_snapshots`` TimescaleDB hypertable defined
   in :mod:`src.reconciliation.schema`.
2. Fallback: append JSON lines to ``engine/logs/pnl_snapshots/YYYYMMDD.jsonl``.
   Triggered when the DB pool is absent, the schema bootstrap fails, or an
   insert raises a connection-level error.

The fallback path is intentionally deterministic so unit tests can pin the
branch without spinning up Postgres.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from src.infra.exchange.exchange_income_fetcher import (
    DEDUP_WINDOW_SIZE,
    ExchangeIncomeFetcher,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_POLL_INTERVAL_S: float = 60.0
# Exchange-reported income types that contribute to realised performance.
# TRANSFER / WELCOME_BONUS / INSURANCE_CLEAR are excluded on purpose — they
# are capital movements or one-off credits, not trading PnL.
PNL_CONTRIBUTING_TYPES: tuple[str, ...] = (
    "REALIZED_PNL",
    "COMMISSION",
    "FUNDING_FEE",
    "COMMISSION_REBATE",
    "REFERRAL_KICKBACK",
)
DEFAULT_FALLBACK_DIR: Path = Path("logs") / "pnl_snapshots"


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

_SCHEMA_BOOTSTRAP_SQL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS exchange_pnl_snapshots (
        ts              TIMESTAMPTZ NOT NULL,
        exchange        TEXT        NOT NULL,
        income_type     TEXT        NOT NULL,
        symbol          TEXT        NOT NULL DEFAULT '',
        asset           TEXT        NOT NULL DEFAULT '',
        amount_usd      NUMERIC(28, 10) NOT NULL,
        tran_id         TEXT        NOT NULL DEFAULT '',
        raw_json        JSONB       NOT NULL DEFAULT '{}'::jsonb,
        PRIMARY KEY (exchange, tran_id, ts)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_pnl_snap_exchange_ts "
    "ON exchange_pnl_snapshots (exchange, ts DESC)",
    "CREATE INDEX IF NOT EXISTS ix_pnl_snap_income_type "
    "ON exchange_pnl_snapshots (income_type)",
)

_HYPERTABLE_SQL: str = (
    "SELECT create_hypertable('exchange_pnl_snapshots', 'ts', "
    "if_not_exists => TRUE, migrate_data => TRUE)"
)


# ---------------------------------------------------------------------------
# ExchangePnLSnapshot
# ---------------------------------------------------------------------------


class ExchangePnLSnapshot:
    """Exchange-reported PnL poller + persister.

    Args:
        adapters: futures adapters (``_market_type == 'futures'``). Non-futures
            adapters are silently ignored — spot endpoints do not expose
            income events.
        db_pool: Optional :class:`src.infra.db.connection.DatabasePool` (or a
            duck-typed object exposing ``.pool.acquire()``). If ``None`` the
            fallback JSONL log is used exclusively.
        poll_interval_s: Poll cadence (default 60s).
        fallback_dir: Directory for JSONL fallback. Created on demand.
        clock: Injection seam for tests (``callable() -> epoch seconds``).
    """

    def __init__(
        self,
        adapters: list[Any],
        db_pool: Any | None = None,
        *,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        fallback_dir: Path | str = DEFAULT_FALLBACK_DIR,
        clock: Any | None = None,
    ) -> None:
        self._adapters = list(adapters)
        self._db_pool = db_pool
        self._poll_interval_s = poll_interval_s
        self._fallback_dir = Path(fallback_dir)
        self._clock = clock or time.time
        self._running: bool = False
        self._tasks: list[asyncio.Task[Any]] = []
        self._schema_ready: bool = False
        self._schema_failed: bool = False
        # Dedup per-exchange across poll cycles, independent from the engine
        # income fetcher so prime + steady-state do not collide.
        self._seen_tran_ids: dict[str, deque[str]] = {}
        # In-memory fallback store: list[(ts_ms, row_dict)] per exchange. Used
        # when DB is unavailable — aggregations still work.
        self._fallback_events: list[dict[str, Any]] = []
        # Fetch helper bound to ExchangeIncomeFetcher to avoid copy-paste of
        # signed-request code. Zero CSV side-effect: we only borrow the
        # fetch methods; emit + CSV append are skipped.
        self._fetcher_helper = ExchangeIncomeFetcher(
            adapters=self._adapters,
            csv_dir=str(self._fallback_dir / ".unused-csv"),
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Bootstrap schema and spawn per-adapter poll loops."""
        if self._running:
            logger.debug("exchange_pnl_snapshot.already_running")
            return
        self._running = True
        self._fallback_dir.mkdir(parents=True, exist_ok=True)

        await self._ensure_schema()

        eligible = [
            a for a in self._adapters
            if getattr(a, "_market_type", "spot") == "futures"
        ]
        if not eligible:
            logger.info("exchange_pnl_snapshot.no_futures_adapters")
            return

        for adapter in eligible:
            eid = getattr(adapter, "exchange_id", "unknown")
            self._seen_tran_ids.setdefault(
                eid, deque(maxlen=DEDUP_WINDOW_SIZE),
            )
            task = asyncio.create_task(
                self._poll_loop(adapter),
                name=f"pnl_snapshot_{eid}",
            )
            self._tasks.append(task)
        logger.info(
            "exchange_pnl_snapshot.started adapters=%d interval_s=%.0f",
            len(self._tasks), self._poll_interval_s,
        )

    async def stop(self) -> None:
        """Cancel all poll loops."""
        if not self._running:
            return
        self._running = False
        for task in self._tasks:
            if not task.done():
                task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._tasks.clear()
        logger.info("exchange_pnl_snapshot.stopped")

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    async def _poll_loop(self, adapter: Any) -> None:
        eid = getattr(adapter, "exchange_id", "unknown")
        while self._running:
            try:
                events = await self._fetch_once(adapter)
                if events:
                    await self._persist(events)
                    logger.info(
                        "exchange_pnl_snapshot.polled exchange=%s events=%d",
                        eid, len(events),
                    )
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "exchange_pnl_snapshot.poll_failed exchange=%s err=%s",
                    eid, exc,
                )
            try:
                await asyncio.sleep(self._poll_interval_s)
            except asyncio.CancelledError:
                break

    async def _fetch_once(self, adapter: Any) -> list[dict[str, Any]]:
        """Delegate to the existing ExchangeIncomeFetcher dispatcher.

        The helper already knows how to sign requests, window 35s of data,
        and convert Bitget business-type codes into normalised income types.
        We reuse it verbatim instead of duplicating the wire format.
        """
        raw = await self._fetcher_helper._fetch_income(adapter)
        # Apply a second per-snapshot dedup in case a prime + steady-state
        # poll overlap on the same tran_id.
        eid = getattr(adapter, "exchange_id", "unknown")
        seen = self._seen_tran_ids.setdefault(
            eid, deque(maxlen=DEDUP_WINDOW_SIZE),
        )
        out: list[dict[str, Any]] = []
        for evt in raw:
            tran_id = str(evt.get("tran_id") or "")
            key = f"{eid}:{tran_id}" if tran_id else ""
            if key and key in seen:
                continue
            if key:
                seen.append(key)
            out.append(self._normalise(evt))
        return out

    def _normalise(self, evt: dict[str, Any]) -> dict[str, Any]:
        """Convert a raw fetcher event into the persistence row shape."""
        ts_ms = int(evt.get("ts_ms") or int(self._clock() * 1000))
        ts = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
        amount = Decimal(str(evt.get("amount_usdt") or 0))
        return {
            "ts": ts,
            "ts_ms": ts_ms,
            "exchange": str(evt.get("exchange", "")),
            "income_type": str(evt.get("income_type", "UNKNOWN")).upper(),
            "symbol": str(evt.get("symbol", "") or ""),
            "asset": str(evt.get("asset", "") or ""),
            "amount_usd": amount,
            "tran_id": str(evt.get("tran_id", "") or ""),
            "raw_json": evt,
        }

    # ------------------------------------------------------------------
    # Schema + persistence
    # ------------------------------------------------------------------

    async def _ensure_schema(self) -> None:
        """Idempotent schema bootstrap. Falls back silently on failure."""
        if self._schema_ready or self._schema_failed:
            return
        pool = self._get_pool()
        if pool is None:
            self._schema_failed = True
            return
        try:
            async with pool.acquire() as conn:
                for stmt in _SCHEMA_BOOTSTRAP_SQL:
                    await conn.execute(stmt)
                try:
                    await conn.execute(_HYPERTABLE_SQL)
                except Exception as exc:  # noqa: BLE001
                    # TimescaleDB extension may be missing; the plain table
                    # is still fully functional.
                    logger.info(
                        "exchange_pnl_snapshot.hypertable_skipped err=%s", exc,
                    )
            self._schema_ready = True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "exchange_pnl_snapshot.schema_bootstrap_failed err=%s", exc,
            )
            self._schema_failed = True

    def _get_pool(self) -> Any | None:
        """Unwrap ``.pool`` off DatabasePool; accept raw pools unchanged."""
        if self._db_pool is None:
            return None
        pool = getattr(self._db_pool, "pool", None)
        return pool if pool is not None else self._db_pool

    async def _persist(self, events: list[dict[str, Any]]) -> None:
        """Insert into TSDB, fall back to JSONL on any failure."""
        # Always keep an in-memory copy for aggregation — guarantees
        # ``get_cumulative_pnl_usd`` works even when TSDB is unreachable.
        for row in events:
            self._fallback_events.append(row)

        pool = self._get_pool() if self._schema_ready else None
        if pool is not None:
            try:
                async with pool.acquire() as conn:
                    for row in events:
                        await conn.execute(
                            """
                            INSERT INTO exchange_pnl_snapshots
                                (ts, exchange, income_type, symbol, asset,
                                 amount_usd, tran_id, raw_json)
                            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
                            ON CONFLICT (exchange, tran_id, ts) DO NOTHING
                            """,
                            row["ts"],
                            row["exchange"],
                            row["income_type"],
                            row["symbol"],
                            row["asset"],
                            row["amount_usd"],
                            row["tran_id"],
                            json.dumps(row["raw_json"], default=str),
                        )
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "exchange_pnl_snapshot.insert_failed falling_back err=%s",
                    exc,
                )
                self._schema_ready = False  # force JSONL for the rest of loop

        self._append_jsonl(events)

    def _append_jsonl(self, events: list[dict[str, Any]]) -> None:
        """Write events to a daily JSONL file."""
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        path = self._fallback_dir / f"{day}.jsonl"
        try:
            self._fallback_dir.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                for row in events:
                    fh.write(
                        json.dumps(
                            {
                                "ts": row["ts"].isoformat(),
                                "exchange": row["exchange"],
                                "income_type": row["income_type"],
                                "symbol": row["symbol"],
                                "asset": row["asset"],
                                "amount_usd": str(row["amount_usd"]),
                                "tran_id": row["tran_id"],
                            },
                            default=str,
                        )
                        + "\n",
                    )
        except OSError as exc:
            logger.warning(
                "exchange_pnl_snapshot.jsonl_append_failed path=%s err=%s",
                path, exc,
            )

    # ------------------------------------------------------------------
    # Aggregations (public API)
    # ------------------------------------------------------------------

    async def get_cumulative_pnl_usd(
        self,
        since: datetime,
        until: datetime | None = None,
    ) -> Decimal:
        """Return the signed sum of PnL-contributing income in ``[since, until)``."""
        if until is None:
            until = datetime.now(timezone.utc)
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)

        pool = self._get_pool() if self._schema_ready else None
        if pool is not None:
            try:
                async with pool.acquire() as conn:
                    row = await conn.fetchrow(
                        """
                        SELECT COALESCE(SUM(amount_usd), 0)::numeric AS total
                        FROM exchange_pnl_snapshots
                        WHERE ts >= $1 AND ts < $2
                          AND income_type = ANY($3::text[])
                        """,
                        since, until, list(PNL_CONTRIBUTING_TYPES),
                    )
                    if row is not None:
                        return Decimal(str(row["total"] or 0))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "exchange_pnl_snapshot.sum_db_failed fallback=memory err=%s",
                    exc,
                )

        # In-memory fallback.
        total = Decimal("0")
        for row in self._fallback_events:
            ts: datetime = row["ts"]
            if ts < since or ts >= until:
                continue
            if row["income_type"] not in PNL_CONTRIBUTING_TYPES:
                continue
            total += Decimal(row["amount_usd"])
        return total

    async def get_daily_pnl_usd(self, day_utc: date) -> Decimal:
        """UTC-day wrapper around :meth:`get_cumulative_pnl_usd`."""
        start = datetime.combine(day_utc, datetime.min.time(), tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        return await self.get_cumulative_pnl_usd(start, end)

    async def prime_from_startup(self, lookback_hours: int = 24) -> int:
        """Bulk-ingest the last ``lookback_hours`` of income at engine boot.

        Returns the total number of events ingested. Graceful-degrades on
        adapter failure — each adapter is polled independently.
        """
        await self._ensure_schema()
        self._fallback_dir.mkdir(parents=True, exist_ok=True)

        total_ingested = 0
        now_ms = int(self._clock() * 1000)
        window_ms = lookback_hours * 60 * 60 * 1000
        # Monkey-patch the helper's fetch window for the prime call only.
        eligible = [
            a for a in self._adapters
            if getattr(a, "_market_type", "spot") == "futures"
        ]
        for adapter in eligible:
            try:
                events = await self._fetch_history(adapter, now_ms - window_ms)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "exchange_pnl_snapshot.prime_failed exchange=%s err=%s",
                    getattr(adapter, "exchange_id", "?"), exc,
                )
                continue
            if events:
                await self._persist(events)
                total_ingested += len(events)
        logger.info(
            "exchange_pnl_snapshot.primed lookback_h=%d events=%d",
            lookback_hours, total_ingested,
        )
        return total_ingested

    async def _fetch_history(
        self,
        adapter: Any,
        start_ms: int,
    ) -> list[dict[str, Any]]:
        """Adapter-agnostic historical fetch for the prime path.

        Binance: signed GET /fapi/v1/income with startTime=start_ms.
        Bitget: _request /api/v3/account/financial-records or /api/v2/mix/account/bill
        with startTime=start_ms (limit 1000).
        """
        eid = getattr(adapter, "exchange_id", "")
        raw: list[dict[str, Any]] = []
        if "binance" in eid:
            try:
                data = await adapter._signed_request(
                    "GET", "/fapi/v1/income",
                    params={"startTime": start_ms, "limit": 1000},
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("prime.binance_history_failed err=%s", exc)
                return []
            if not isinstance(data, list):
                return []
            for row in data:
                if not isinstance(row, dict):
                    continue
                try:
                    amount = float(row.get("income") or 0)
                except (TypeError, ValueError):
                    continue
                raw.append({
                    "exchange": eid,
                    "income_type": str(
                        row.get("incomeType", "UNKNOWN"),
                    ).upper(),
                    "asset": str(row.get("asset", "")),
                    "symbol": str(row.get("symbol", "")),
                    "amount_usdt": amount,
                    "tran_id": str(row.get("tranId") or row.get("time", "")),
                    "ts_ms": int(row.get("time") or start_ms),
                })
        elif "bitget" in eid:
            is_uta = False
            try:
                is_uta = bool(getattr(adapter, "_is_uta", lambda: False)())
            except Exception:  # noqa: BLE001
                is_uta = False
            if is_uta:
                path = "/api/v3/account/financial-records"
                params: dict[str, Any] = {
                    "category": "USDT-FUTURES",
                    "startTime": start_ms,
                    "limit": 500,
                }
            else:
                path = "/api/v2/mix/account/bill"
                params = {
                    "productType": "USDT-FUTURES",
                    "startTime": start_ms,
                    "limit": 500,
                }
            try:
                resp = await adapter._request(
                    "GET", path, params=params, signed=True,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("prime.bitget_history_failed err=%s", exc)
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
            # Delegate the business-type → income-type map back to the helper.
            from src.infra.exchange.exchange_income_fetcher import (
                _BITGET_BUSINESS_TYPE_MAP,
            )
            for row in bills:
                if not isinstance(row, dict):
                    continue
                business_type = str(row.get("businessType", "")).lower()
                income_type = _BITGET_BUSINESS_TYPE_MAP.get(
                    business_type, "UNKNOWN",
                )
                try:
                    amount = float(row.get("amount") or 0)
                except (TypeError, ValueError):
                    amount = 0.0
                tran_id = str(
                    row.get("billId") or row.get("id") or row.get("cTime", ""),
                )
                raw.append({
                    "exchange": eid,
                    "income_type": income_type,
                    "asset": str(row.get("coin", "")),
                    "symbol": str(row.get("symbol", "")),
                    "amount_usdt": amount,
                    "tran_id": tran_id,
                    "ts_ms": int(row.get("cTime") or start_ms),
                })

        # Normalise + dedup against the steady-state poll.
        out: list[dict[str, Any]] = []
        seen = self._seen_tran_ids.setdefault(
            eid, deque(maxlen=DEDUP_WINDOW_SIZE),
        )
        for evt in raw:
            tran_id = str(evt.get("tran_id") or "")
            key = f"{eid}:{tran_id}" if tran_id else ""
            if key and key in seen:
                continue
            if key:
                seen.append(key)
            out.append(self._normalise(evt))
        return out

    # ------------------------------------------------------------------
    # Introspection helpers (used by PnLReconciler / tests)
    # ------------------------------------------------------------------

    def has_data(self) -> bool:
        """Has at least one snapshot been stored since boot?"""
        return bool(self._fallback_events) or self._schema_ready

    @property
    def fallback_events(self) -> list[dict[str, Any]]:
        """Read-only view of in-memory fallback buffer (tests)."""
        return list(self._fallback_events)


__all__ = [
    "DEFAULT_FALLBACK_DIR",
    "DEFAULT_POLL_INTERVAL_S",
    "PNL_CONTRIBUTING_TYPES",
    "ExchangePnLSnapshot",
]
