#!/usr/bin/env bash
# download_kbt_data.sh — K-BT OHLCV Data Download (US-387)
# Downloads all required historical 1H OHLCV data for K-BT-01~18 backtests.
# Run from repo root or engine/ directory.

set -euo pipefail

ENGINE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ENGINE_DIR"

echo "=== K-BT OHLCV Data Download ==="
echo "Engine dir: $ENGINE_DIR"
echo ""

# Step 0: Apply migration 007 (remove retention policy)
echo "--- Step 0: Removing orderbook_snapshots retention policy ---"
docker compose -f "$ENGINE_DIR/../docker-compose.yml" exec -T timescaledb \
  psql -U postgres -d arbitrage \
  -f /docker-entrypoint-initdb.d/007_remove_orderbook_retention.sql 2>/dev/null || \
  psql "$DATABASE_URL" -f "$ENGINE_DIR/src/infra/db/migrations/007_remove_orderbook_retention.sql" 2>/dev/null || \
  echo "  (migration 007 skipped — run manually if needed)"

echo ""

# Step 1: funding_rate / spot_futures / cross_exchange global / futures_futures period (2024-01-10 ~ 2024-03-31)
echo "--- Step 1: 2024-01-10 ~ 2024-03-31 (Binance, Bybit, OKX, Bitget + Futures) ---"
python scripts/download_historical.py \
  --exchanges binance,binance_futures,bybit,bybit_futures,okx,okx_futures,bitget,bitget_futures \
  --symbols "BTC/USDT,ETH/USDT,SOL/USDT,ETH/BTC,SOL/BTC" \
  --start 2024-01-10 --end 2024-03-31 --interval 1h

echo ""

# Step 2: triangular period (2024-01-10 ~ 2024-06-30)
echo "--- Step 2: 2024-01-10 ~ 2024-06-30 (all exchanges incl KRW, triangular symbols) ---"
python scripts/download_historical.py \
  --exchanges binance,bybit,okx,bitget,mexc,gateio,upbit,bithumb,coinone \
  --symbols "BTC/USDT,ETH/USDT,SOL/USDT,ETH/BTC,SOL/BTC" \
  --start 2024-01-10 --end 2024-06-30 --interval 1h

python scripts/download_historical.py \
  --exchanges upbit,bithumb,coinone \
  --symbols "BTC/KRW,ETH/KRW,SOL/KRW,XRP/KRW" \
  --start 2024-01-10 --end 2024-06-30 --interval 1h

echo ""

# Step 3: statistical_arb period (2024-04-01 ~ 2024-09-30)
echo "--- Step 3: 2024-04-01 ~ 2024-09-30 (stat_arb: Binance, Bybit, OKX, MEXC, Gate.io, Coinone) ---"
python scripts/download_historical.py \
  --exchanges binance,binance_futures,bybit,bybit_futures,okx,okx_futures,mexc,gateio,coinone \
  --symbols "BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT,ETH/BTC,SOL/BTC" \
  --start 2024-04-01 --end 2024-09-30 --interval 1h

echo ""

# Step 4: cross_exchange KRW period (2025-01-01 ~ 2025-03-31)
echo "--- Step 4: 2025-01-01 ~ 2025-03-31 (cross-exchange KRW: Binance + Upbit/Bithumb/Coinone) ---"
python scripts/download_historical.py \
  --exchanges binance \
  --symbols "BTC/USDT,ETH/USDT,SOL/USDT" \
  --start 2025-01-01 --end 2025-03-31 --interval 1h

python scripts/download_historical.py \
  --exchanges upbit,bithumb,coinone \
  --symbols "BTC/KRW,ETH/KRW,SOL/KRW,XRP/KRW" \
  --start 2025-01-01 --end 2025-03-31 --interval 1h

echo ""

# Step 5: DB row count verification
echo "--- Step 5: DB row count by exchange ---"
python -c "
import asyncio, asyncpg, os

async def show_counts():
    dsn = os.environ.get('DATABASE_URL', 'postgresql://leviathan:leviathan@localhost:5432/leviathan')
    dsn = dsn.replace('postgresql+asyncpg://', 'postgresql://')
    conn = await asyncpg.connect(dsn)
    rows = await conn.fetch(
        \"\"\"SELECT exchange, COUNT(*) as cnt, MIN(ts) as first_ts, MAX(ts) as last_ts
           FROM orderbook_snapshots
           WHERE ts < '2026-01-01'
           GROUP BY exchange ORDER BY exchange\"\"\"
    )
    await conn.close()
    print(f'{'Exchange':<20} {'Count':>10} {'First':>22} {'Last':>22}')
    print('-' * 76)
    for r in rows:
        print(f\"{r['exchange']:<20} {r['cnt']:>10} {str(r['first_ts'])[:19]:>22} {str(r['last_ts'])[:19]:>22}\")

asyncio.run(show_counts())
"

echo ""
echo "=== K-BT download complete ==="
