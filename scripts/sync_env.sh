#!/bin/bash
# LEVIATHAN .env 동기화 스크립트
# root .env ↔ engine/.env 공통 secret 동기화
set -e
cd "$(dirname "$0")/.."

echo "=== .env 동기화 검사 ==="

# 공통 키 비교 (secret 키만)
SECRET_KEYS="BINANCE_API_KEY BINANCE_API_SECRET OKX_API_KEY OKX_API_SECRET OKX_PASSPHRASE BYBIT_API_KEY BYBIT_API_SECRET UPBIT_API_KEY UPBIT_API_SECRET BITHUMB_API_KEY BITHUMB_API_SECRET COINONE_API_KEY COINONE_API_SECRET JWT_SECRET REDIS_PASSWORD EXA_API_KEY GITHUB_TOKEN TRADE_TELEGRAM_BOT_TOKEN TRADE_TELEGRAM_CHAT_ID INFRA_TELEGRAM_BOT_TOKEN INFRA_TELEGRAM_CHAT_ID DEV_TELEGRAM_BOT_TOKEN DEV_TELEGRAM_CHAT_ID"

DRIFT=0
for key in $SECRET_KEYS; do
    ROOT_VAL=$(grep "^${key}=" .env 2>/dev/null | head -1 | cut -d= -f2-)
    ENGINE_VAL=$(grep "^${key}=" engine/.env 2>/dev/null | head -1 | cut -d= -f2-)
    if [ -n "$ROOT_VAL" ] && [ -n "$ENGINE_VAL" ] && [ "$ROOT_VAL" != "$ENGINE_VAL" ]; then
        echo "DRIFT: $key differs between root and engine"
        DRIFT=$((DRIFT+1))
    fi
done

if [ $DRIFT -eq 0 ]; then
    echo "OK: 0 drift items"
else
    echo "DRIFT: $DRIFT items differ"
    exit 1
fi
