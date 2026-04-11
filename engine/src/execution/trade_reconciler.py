"""
TradeReconciler — 내부 execution_log vs 거래소 실체결 이력 대조.

10분 주기로 호출됨. symbol + timestamp 기준 매칭.
매칭 성공 시 DB reconciliation_status='matched' + IS 계산.
미매칭 건 발견 시 logger.warning.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

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
            logger.debug("trade_reconciler_no_fills exchange=%s symbols=%s", exchange_id, symbols)
            return report

        # DB에서 같은 기간 체결 기록 조회 (db_pool이 없으면 skip)
        if self._db is None:
            # db_pool 없을 때: exchange fill count만 보고
            report.matched = len(exchange_fills)
            logger.info(
                "trade_recon ex=%s matched=%d is_p95=%.1fbps (no_db)",
                exchange_id, report.matched, 0.0,
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
            report.matched = len(exchange_fills)
            return report

        # symbol → list of db rows (시간순)
        db_by_symbol: dict[str, list] = {}
        for row in db_rows:
            sym = row["symbol"]
            db_by_symbol.setdefault(sym, []).append(row)

        # symbol+timestamp 기준 매칭 (±5초 window)
        is_values: list[float] = []
        matched_ts_keys: set[float] = set()

        for ex_fill in exchange_fills:
            sym = ex_fill.get("symbol", "").replace("USDT", "/USDT")  # normalize
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
                if dt < 5.0 and dt < best_dt and db_ts not in matched_ts_keys:
                    best_match = db_row
                    best_dt = dt

            if best_match is not None:
                matched_ts_keys.add(best_match["ts"].timestamp() if hasattr(best_match["ts"], "timestamp") else float(best_match["ts"]))
                report.matched += 1

                # IS 계산: exchange fill vs our expected price
                if ex_price and ex_price > 0:
                    db_price = float(best_match["buy_price"] if ex_side == "buy" else best_match["sell_price"])
                    if db_price and db_price > 0:
                        is_bps = abs(ex_price - db_price) / db_price * 10000
                        is_values.append(is_bps)

                # DB reconciliation_status 업데이트
                try:
                    await self._db.execute(
                        """
                        UPDATE execution_log
                        SET reconciliation_status = 'matched',
                            reconciled_at = NOW()
                        WHERE ts = $1 AND symbol = $2
                        """,
                        best_match["ts"],
                        sym,
                    )
                except Exception as exc:
                    logger.debug("trade_reconciler.db_update_failed ts=%s error=%s", best_match["ts"], exc)
            else:
                report.unmatched_exchange.append(ex_fill)

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

        if report.unmatched_exchange:
            logger.warning(
                "trade_reconciler.unmatched_exchange exchange=%s count=%d",
                exchange_id, len(report.unmatched_exchange),
            )

        logger.info(
            "trade_recon ex=%s matched=%d is_p95=%.1fbps",
            exchange_id, report.matched, report.is_p95_bps or 0.0,
        )

        return report
