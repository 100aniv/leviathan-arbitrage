"""ExchangeAdapterPort — Phase 5.1 first port (2026-04-26).

거래소 연결 추상화. PaperExchangeAdapter / NativeAdapter (binance/upbit/...) 모두
이 Protocol을 구현. runtime/* 모듈은 Engine god-object 대신 이 Port에 의존.

산업 표준 비교:
- Nautilus ExecClient + DataClient (DDD + ports & adapters)
- LEAN IBrokerage (place_order/cancel_order/get_balance + IDataFeed 분리)
- Hummingbot ConnectorBase (Exchange/Derivative + 4 sub-component)

LEVIATHAN ExchangeAdapterPort: place_order + cancel_order + get_balance + supports_symbol
+ get_min_notional + _market_type + health_score + connect/disconnect.

paper-only fields (universe_matrix entries=34 보장):
- supports_symbol(symbol) — 어댑터 보유 심볼 검증
- get_min_notional(symbol) — 최소 명목금액
- _market_type — "spot" | "futures" (universe_matrix routing)
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from src.core.models import Order, Trade


@runtime_checkable
class ExchangeAdapterPort(Protocol):
    """Hexagonal port for exchange adapters (paper/native/sandbox).

    구현체:
    - PaperExchangeAdapter (engine/src/execution/paper_adapter.py)
    - BinanceNativeAdapter / UpbitNativeAdapter / ... (engine/src/infra/exchange/)

    Phase 5.2 god-object 해체 후 runtime/* 모듈이 직접 의존.
    """

    # === Identity ===

    exchange_id: str
    """거래소 식별자 (binance, upbit, paper_binance, ...)."""

    # === Lifecycle ===

    async def connect(self) -> None:
        """WS + REST 연결 초기화. 이미 연결된 경우 no-op."""
        ...

    async def disconnect(self) -> None:
        """graceful shutdown. 미체결 주문 정리는 caller 책임."""
        ...

    # === Order placement (LEAN IBrokerage.PlaceOrder 미러) ===

    async def place_order(self, order: Order) -> Trade:
        """주문 제출. 체결 결과 Trade 반환.

        paper: 시뮬레이션 fill. live: 실 거래소 API 호출.
        실패 시 OrderRejectedError 발생.
        """
        ...

    async def cancel_order(self, order_id: str, symbol: str | None = None) -> bool:
        """주문 취소. symbol은 Binance live 등 일부 거래소 필수 (BUG-71 fallback).

        Returns:
            True: 취소 성공 또는 이미 종결된 주문
            False: 취소 실패 (네트워크/통신 오류)
        """
        ...

    # === Universe Matrix support (paper canary entries=34 보장) ===

    def supports_symbol(self, symbol: str) -> bool:
        """이 어댑터가 symbol을 지원하는가 (universe_matrix 사전 검증).

        Phase 4-3 paper adapter 확장 (3d37e91)에서 도입.
        BUG-225 (Korean exchange × USDT pair) class 영구차단.
        """
        ...

    def get_min_notional(self, symbol: str) -> Decimal:
        """심볼의 최소 명목금액 (USDT). 0 이상 반환 (paper stub은 0.0).

        live: 거래소 별 lot size + minNotional filter 반영.
        paper: 0 (검증 단계 스킵).
        """
        ...

    @property
    def _market_type(self) -> str:
        """'spot' | 'futures' — universe_matrix routing.

        suffix '_futures' 어댑터는 'futures', 그 외 'spot'.
        """
        ...

    # === Balance + health ===

    async def get_balance(self) -> dict[str, Decimal]:
        """현재 잔고 (asset → amount). paper: 시뮬레이션 잔고.

        Returns:
            예: {'USDT': Decimal('1000.50'), 'BTC': Decimal('0.05')}
        """
        ...

    @property
    def health_score(self) -> Decimal:
        """0~1 거래소 건강도 (RiskGuardian #5 check).

        paper: 항상 1.0
        live: WS heartbeat + REST latency + 에러율 가중 평균
        """
        ...
