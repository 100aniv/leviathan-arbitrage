"""Exchange metadata constants — 단일 진실 공급원(SSOT).

모든 거래소별 상수는 여기서 정의한다.
새 거래소 추가 시 이 파일만 수정하면 전체 반영된다.
"""
from __future__ import annotations

# KRW 마켓 거래소 — BTC/KRW 심볼 사용 (USDT 아님)
KRW_EXCHANGES: frozenset[str] = frozenset({"upbit", "bithumb", "coinone"})

# Futures exchange ID → Spot exchange ID 매핑
# 새 선물 거래소 추가 시 여기에 1줄 추가하면 전체 반영됨
FUTURES_TO_SPOT: dict[str, str] = {
    "binance_futures": "binance",
    "bitget_futures": "bitget",
    "bybit_futures": "bybit",
    "okx_futures": "okx",
}
