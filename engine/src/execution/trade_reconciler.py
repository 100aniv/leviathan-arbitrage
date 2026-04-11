"""
TradeReconciler — 내부 execution_log vs 거래소 실체결 이력 대조.

10분 주기로 호출됨. symbol + timestamp 기준 매칭.
매칭 성공 시 DB reconciliation_status='matched' + IS 계산.
미매칭 건 발견 시 logger.warning.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

# Known quote-asset suffixes ordered longest-first to avoid partial matches.
_QUOTE_SUFFIXES = ("USDT", "USDC", "BUSD", "BTC", "ETH", "BNB", "FDUSD")
_QUOTE_RE = re.compile(r"(" + "|".join(_QUOTE_SUFFIXES) + r")$")


def _normalize_symbol(raw: str) -> str:
    """Convert raw exchange symbol to 'BASE/QUOTE' canonical form.

    Handles:
      - Futures contract perpetual suffix: 'BTC/USDT:USDT' → 'BTC/USDT'
      - Raw exchange symbols without slash: 'BTCUSDT' → 'BTC/USDT'
      - Already normalised: 'BTC/USDT' returned as-is
    Returns the raw string unchanged if no known suffix is found.
    """
    # Strip futures perpetual suffix (e.g. ':USDT', ':BTC') before further processing
    if ":" in raw:
        raw = raw.split(":")[0]
    if "/" in raw:
        return raw  # already normalized (possibly after stripping ':' suffix)
    m = _QUOTE_RE.search(raw)
    if m:
        base = raw[: m.start()]
        quote = m.group(1)
        return f"{base}/{quote}"
    return raw

logger = logging.getLogger(__name__)


@dataclass
class ReconciliationReport:
    exchange_id: str
    period_start_ms: int
    matched: int = 0
    unmatched_internal: list[dict] = field(default_factory=list)
    unmatched_exchange: list[dict] = field(default_factory=list)
    is_p50_bps: float | None = None
    is_p95_bps: float | None = None


class TradeReconciler:
    """내부 DB 체결 기록 vs 거래소 실체결 이력 대조.

    Usage:
        reconciler = TradeReconciler(db_pool=pool, telegram=telegram_bot)
        report = await reconciler.reconcile_period(
            exchange_adapter=binance_adapter,
            exchange_id="binance_futures",
            since_ms=int((time.time() - 600) * 1000),
        )
    """

    def __init__(self, db_pool=None, telegram=None) -> None:
        self._db = db_pool
        self._telegram = telegram

    async def reconcile_period(
        self,
        exchange_adapter,
        exchange_id: str,
        since_ms: int,
        symbols: list[str] | None = None,
    ) -> ReconciliationReport:
        """DB 체결 기록과 거래소 체결 이력을 symbol+timestamp 기준으로 대조."""
        report = ReconciliationReport(
            exchange_id=exchange_id,
            period_start_ms=since_ms,
        )

        # get_trades()가 구현된 어댑터만 처리
        if not hasattr(exchange_adapter, "get_trades"):
            logger.debug("trade_reconciler_skip exchange=%s no get_trades", exchange_id)
            return report

        try:
            if symbols:
                exchange_fills: list[dict] = []
                for sym in symbols:
                    fills = await exchange_adapter.get_trades(
                        symbol=sym,
                        start_time_ms=since_ms,
                        limit=200,
                    )
                    exchange_fills.extend(fills)
            else:
                exchange_fills = await exchange_adapter.get_trades(
                    symbol="",
                    start_time_ms=since_ms,
                    limit=200,
                )
        except Exception as exc:
            logger.warning("trade_reconciler.get_trades_failed exchange=%s error=%s", exchange_id, exc)
            return report

        if not exchange_fills:
            # Exchange returned 0 fills — could be legitimate (no trades) or silent API failure.
            # Still query DB: if DB rows exist but exchange returned nothing, those rows are
            # unmatched_internal (possible phantom positions or API auth expiry blind spot).
            logger.debug("trade_reconciler_no_fills exchange=%s symbols=%s", exchange_id, symbols)
            if self._db is not None:
                try:
                    since_ts = since_ms / 1000.0
                    db_rows_check = await self._db.fetch(
                        """
                        SELECT ts, symbol, buy_exchange, sell_exchange
                        FROM execution_log
                        WHERE mode = 'live'
                          AND ts >= to_timestamp($1)
                          AND ($2 = '' OR buy_exchange = $2 OR sell_exchange = $2)
                        LIMIT 50
                        """,
                        since_ts,
                        exchange_id,
                    )
                    if db_rows_check:
                        for row in db_rows_check:
                            report.unmatched_internal.append({
                                "symbol": row["symbol"],
                                "ts": str(row["ts"]),
                                "buy_exchange": row.get("buy_exchange"),
                                "sell_exchange": row.get("sell_exchange"),
                            })
                        logger.warning(
                            "trade_reconciler.api_returned_empty exchange=%s db_rows=%d "
                            "(exchange returned 0 fills — possible API failure or phantom positions)",
                            exchange_id, len(db_rows_check),
                        )
                        if self._telegram is not None:
                            try:
                                await self._telegram.send(
                                    f"⚠️ TradeRecon: {exchange_id} 거래소 체결이력 0건이나 "
                                    f"DB에 {len(db_rows_check)}건 존재 — API 실패 또는 팬텀 포지션 의심"
                                )
                            except Exception as _tg_exc:
                                logger.debug("trade_reconciler.telegram_failed error=%s", _tg_exc)
                except Exception as exc:
                    logger.warning("trade_reconciler.db_check_on_empty_fills_failed error=%s", exc)
            return report

        # DB에서 같은 기간 체결 기록 조회 (db_pool이 없으면 skip)
        if self._db is None:
            # db_pool 없을 때: 매칭 불가 — 거짓 matched 보고 금지
            logger.warning(
                "trade_recon ex=%s skipped: no db_pool (exchange_fills=%d)",
                exchange_id, len(exchange_fills),
            )
            return report

        # DB에서 해당 기간 체결 기록 조회
        try:
            since_ts = since_ms / 1000.0
            db_rows = await self._db.fetch(
                """
                SELECT ts, symbol, buy_exchange, sell_exchange,
                       buy_price, sell_price, size, slippage_total
                FROM execution_log
                WHERE mode = 'live'
                  AND ts >= to_timestamp($1)
                  AND ($2 = '' OR buy_exchange = $2 OR sell_exchange = $2)
                ORDER BY ts ASC
                LIMIT 500
                """,
                since_ts,
                exchange_id,
            )
        except Exception as exc:
            logger.warning("trade_reconciler.db_query_failed error=%s", exc)
            # Do NOT inflate matched count on DB failure — leave at 0 so alerts fire
            return report

        # symbol → list of db rows (시간순)
        db_by_symbol: dict[str, list] = {}
        for row in db_rows:
            sym = row["symbol"]
            db_by_symbol.setdefault(sym, []).append(row)

        # symbol+timestamp 기준 매칭 (±5초 window)
        # matched_ts_keys uses integer milliseconds to avoid float64 precision collisions
        # (two DB rows 0.1ms apart would hash-collide as float seconds).
        is_values: list[float] = []
        matched_ts_keys: set[int] = set()

        def _row_ts_ms(row_ts) -> int:
            """DB timestamp → integer milliseconds (collision-safe set key)."""
            t = row_ts.timestamp() if hasattr(row_ts, "timestamp") else float(row_ts)
            return int(t * 1000)

        for ex_fill in exchange_fills:
            sym = _normalize_symbol(ex_fill.get("symbol", ""))
            ex_ts_ms = ex_fill.get("ts_ms", 0)
            ex_ts = ex_ts_ms / 1000.0 if ex_ts_ms else 0
            ex_price = ex_fill.get("price", 0)
            ex_side = ex_fill.get("side", "").lower()

            candidates = db_by_symbol.get(sym, [])
            best_match = None
            best_dt = float("inf")

            for db_row in candidates:
                db_ts = db_row["ts"].timestamp() if hasattr(db_row["ts"], "timestamp") else float(db_row["ts"])
                dt = abs(db_ts - ex_ts)
                if dt < 5.0 and dt < best_dt and _row_ts_ms(db_row["ts"]) not in matched_ts_keys:
                    best_match = db_row
                    best_dt = dt

            if best_match is not None:
                matched_ts_keys.add(_row_ts_ms(best_match["ts"]))
                report.matched += 1

                # IS 계산: exchange fill vs our expected price
                if ex_price and ex_price > 0:
                    db_price = float(best_match["buy_price"] if ex_side == "buy" else best_match["sell_price"])
                    if db_price and db_price > 0:
                        is_bps = abs(ex_price - db_price) / db_price * 10000
                        is_values.append(is_bps)

                # DB reconciliation_status 업데이트
                # WHERE 절에 buy_exchange + sell_exchange 포함 → (ts, symbol) 단독으로는
                # 동시 체결 시 여러 행이 있을 수 있어 multi-row 오염 방지.
                try:
                    await self._db.execute(
                        """
                        UPDATE execution_log
                        SET reconciliation_status = 'matched',
                            reconciled_at = NOW()
                        WHERE ts = $1
                          AND symbol = $2
                          AND buy_exchange = $3
                          AND sell_exchange = $4
                        """,
                        best_match["ts"],
                        sym,
                        best_match["buy_exchange"],
                        best_match["sell_exchange"],
                    )
                except Exception as exc:
                    logger.debug("trade_reconciler.db_update_failed ts=%s error=%s", best_match["ts"], exc)
            else:
                report.unmatched_exchange.append(ex_fill)

        # DB rows with no matching exchange fill → phantom internal records
        for _sym, _rows in db_by_symbol.items():
            for _row in _rows:
                if _row_ts_ms(_row["ts"]) not in matched_ts_keys:
                    report.unmatched_internal.append({
                        "symbol": _sym,
                        "ts": str(_row["ts"]),
                        "buy_exchange": _row.get("buy_exchange"),
                        "sell_exchange": _row.get("sell_exchange"),
                    })

        # IS 통계 계산
        if is_values:
            sorted_is = sorted(is_values)
            n = len(sorted_is)
            report.is_p50_bps = sorted_is[n // 2]
            report.is_p95_bps = sorted_is[int(n * 0.95)]

        if report.unmatched_internal:
            logger.warning(
                "trade_reconciler.unmatched exchange=%s unmatched_internal=%d",
                exchange_id, len(report.unmatched_internal),
            )
            if self._telegram is not None:
                try:
                    await self._telegram.send(
                        f"⚠️ TradeRecon: {exchange_id} 내부 미매칭 {len(report.unmatched_internal)}건 "
                        f"(DB 체결 기록이 거래소 체결 이력에 없음)"
                    )
                except Exception as _tg_exc:
                    logger.debug("trade_reconciler.telegram_failed error=%s", _tg_exc)

        if report.unmatched_exchange:
            logger.warning(
                "trade_reconciler.unmatched_exchange exchange=%s count=%d",
                exchange_id, len(report.unmatched_exchange),
            )
            if self._telegram is not None:
                try:
                    await self._telegram.send(
                        f"⚠️ TradeRecon: {exchange_id} 거래소 미매칭 {len(report.unmatched_exchange)}건 "
                        f"(거래소 체결이 내부 DB에 없음)"
                    )
                except Exception as _tg_exc:
                    logger.debug("trade_reconciler.telegram_failed error=%s", _tg_exc)

        logger.info(
            "trade_recon ex=%s matched=%d is_p95=%.1fbps",
            exchange_id, report.matched, report.is_p95_bps or 0.0,
        )

        return report
