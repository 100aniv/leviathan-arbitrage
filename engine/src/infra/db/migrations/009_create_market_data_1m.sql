-- Migration 009: Create market_data_1m hypertable for ML training
-- Used by HMMTrainer and XGBoostTrainer to fetch OHLCV + spread data
-- Created: 2026-04-10 (PHOENIX v25 — P2 completion)

CREATE TABLE IF NOT EXISTS market_data_1m (
    timestamp       TIMESTAMPTZ     NOT NULL,
    symbol          VARCHAR(20)     NOT NULL DEFAULT 'BTC/USDT',
    exchange_id     VARCHAR(20)     NOT NULL DEFAULT 'binance',
    open_price      DOUBLE PRECISION NOT NULL DEFAULT 0,
    high_price      DOUBLE PRECISION NOT NULL DEFAULT 0,
    low_price       DOUBLE PRECISION NOT NULL DEFAULT 0,
    close_price     DOUBLE PRECISION NOT NULL,
    volume          DOUBLE PRECISION NOT NULL DEFAULT 0,
    bid_ask_spread  DOUBLE PRECISION NOT NULL DEFAULT 0,
    trade_count     INTEGER          NOT NULL DEFAULT 0
);

-- Convert to TimescaleDB hypertable (7-day chunks for 30-day ML lookback)
SELECT create_hypertable(
    'market_data_1m',
    'timestamp',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists       => TRUE
);

-- Index for ML trainer queries (timestamp range + symbol)
CREATE INDEX IF NOT EXISTS idx_market_data_1m_ts
    ON market_data_1m (timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_market_data_1m_symbol_ts
    ON market_data_1m (symbol, timestamp DESC);

-- Retention policy: 90 days (covers 30-day training lookback with margin)
SELECT add_retention_policy(
    'market_data_1m',
    INTERVAL '90 days',
    if_not_exists => TRUE
);
