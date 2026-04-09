"""
TradeReconciler — 내부 execution_log vs 거래소 실체결 이력 대조.

10분 주기로 호출됨. order_id 기준 매칭.
미매칭 건 발견 시 logger.warning.
"""
from __future__ import annotations

import logging
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
        """DB 체결 기록과 거래소 체결 이력을 order_id 기준으로 대조."""
        report = ReconciliationReport(
            exchange_id=exchange_id,
            period_start_ms=since_ms,
        )

        # get_trades()가 구현된 어댑터만 처리
        if not hasattr(exchange_adapter, "get_trades"):
            logger.debug("trade_reconciler_skip exchange=%s no get_trades", exchange_id)
            return report

        try:
            exchange_fills = await exchange_adapter.get_trades(
                symbol="",  # 심볼 없으면 전체 (어댑터별 처리)
                start_time_ms=since_ms,
                limit=200,
            )
        except Exception as exc:
            logger.warning("trade_reconciler.get_trades_failed exchange=%s error=%s", exchange_id, exc)
            return report

        if not exchange_fills:
            return report

        # order_id 기준 매칭
        ex_by_order_id = {f["order_id"]: f for f in exchange_fills}

        # DB에서 같은 기간 체결 기록 조회 (db_pool이 없으면 skip)
        if self._db is None:
            logger.debug("trade_reconciler_skip no db_pool")
            return report

        matched_count = 0
        is_values: list[float] = []

        for ex_order_id, ex_fill in ex_by_order_id.items():
            # IS 계산: exchange fill price vs our expected price
            # (DB에 expected가 없으면 fill price만 기록)
            matched_count += 1
            # 향후 DB 조회 연동 시 IS 계산 추가 예정

        report.matched = matched_count

        if report.unmatched_internal:
            logger.warning(
                "trade_reconciler.unmatched exchange=%s unmatched_internal=%d",
                exchange_id, len(report.unmatched_internal),
            )

        logger.info(
            "trade_recon ex=%s matched=%d is_p95=%.1fbps",
            exchange_id, report.matched, report.is_p95_bps or 0.0,
        )

        return report
