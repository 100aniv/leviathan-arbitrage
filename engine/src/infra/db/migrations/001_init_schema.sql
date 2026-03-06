-- LEVIATHAN TimescaleDB Schema Migration 001
-- Creates core hypertables for market data and execution logging.

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- 1. Orderbook snapshots hypertable
CREATE TABLE IF NOT EXISTS orderbook_snapshots (
    ts          TIMESTAMPTZ NOT NULL,
    exchange    TEXT        NOT NULL,
    symbol      TEXT        NOT NULL,
    bids_json   JSONB       NOT NULL DEFAULT '[]',
    asks_json   JSONB       NOT NULL DEFAULT '[]',
    best_bid    NUMERIC     NOT NULL,
    best_ask    NUMERIC     NOT NULL,
    spread_bps  NUMERIC     NOT NULL DEFAULT 0,
    mid_price   NUMERIC     NOT NULL DEFAULT 0
);
SELECT create_hypertable('orderbook_snapshots', 'ts', if_not_exists => TRUE);
SELECT add_retention_policy('orderbook_snapshots', INTERVAL '30 days', if_not_exists => TRUE);

-- 2. Execution log hypertable
CREATE TABLE IF NOT EXISTS execution_log (
    ts               TIMESTAMPTZ NOT NULL,
    strategy_id      TEXT        NOT NULL,
    signal_id        TEXT,
    buy_exchange     TEXT        NOT NULL,
    sell_exchange     TEXT        NOT NULL,
    symbol           TEXT        NOT NULL,
    buy_price        NUMERIC     NOT NULL,
    sell_price       NUMERIC     NOT NULL,
    size             NUMERIC     NOT NULL,
    gross_spread_bps NUMERIC,
    fee_total        NUMERIC,
    slippage_total   NUMERIC,
    net_pnl          NUMERIC,
    status           TEXT        NOT NULL DEFAULT 'pending',
    metadata         JSONB
);
SELECT create_hypertable('execution_log', 'ts', if_not_exists => TRUE);
SELECT add_retention_policy('execution_log', INTERVAL '90 days', if_not_exists => TRUE);

-- 3. OHLCV 1-minute hypertable
CREATE TABLE IF NOT EXISTS ohlcv_1m (
    ts       TIMESTAMPTZ NOT NULL,
    exchange TEXT        NOT NULL,
    symbol   TEXT        NOT NULL,
    open     NUMERIC     NOT NULL,
    high     NUMERIC     NOT NULL,
    low      NUMERIC     NOT NULL,
    close    NUMERIC     NOT NULL,
    volume   NUMERIC     NOT NULL DEFAULT 0
);
SELECT create_hypertable('ohlcv_1m', 'ts', if_not_exists => TRUE);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_ob_exchange_symbol ON orderbook_snapshots (exchange, symbol, ts DESC);
CREATE INDEX IF NOT EXISTS idx_exec_strategy ON execution_log (strategy_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_exec_status ON execution_log (status, ts DESC);
CREATE INDEX IF NOT EXISTS idx_ohlcv_exchange_symbol ON ohlcv_1m (exchange, symbol, ts DESC);
