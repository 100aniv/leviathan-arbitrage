-- LEVIATHAN Unified DB Schema
-- Consolidated from: 001_init_schema.sql, timescale.py, 002_tuning_logs.sql,
--                    003_shadow_stage_results.sql, schema.py ORM, attribution.py
-- All statements are idempotent (IF NOT EXISTS / if_not_exists => TRUE).

-- ============================================================
-- Extensions
-- ============================================================
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ============================================================
-- 1. Orderbook snapshots  (from 001_init_schema.sql)
-- ============================================================
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

-- ============================================================
-- 2. Execution log  (from 001_init_schema.sql)
-- ============================================================
CREATE TABLE IF NOT EXISTS execution_log (
    ts               TIMESTAMPTZ NOT NULL,
    strategy_id      TEXT        NOT NULL,
    signal_id        TEXT,
    buy_exchange     TEXT        NOT NULL,
    sell_exchange    TEXT        NOT NULL,
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

-- ============================================================
-- 3. OHLCV 1-minute table  (from 001_init_schema.sql)
-- ============================================================
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

-- Indexes from 001_init_schema.sql
CREATE INDEX IF NOT EXISTS idx_ob_exchange_symbol   ON orderbook_snapshots (exchange, symbol, ts DESC);
CREATE INDEX IF NOT EXISTS idx_exec_strategy        ON execution_log (strategy_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_exec_status          ON execution_log (status, ts DESC);
CREATE INDEX IF NOT EXISTS idx_ohlcv_exchange_symbol ON ohlcv_1m (exchange, symbol, ts DESC);

-- ============================================================
-- 4. OHLCV raw (timescale.py)
-- ============================================================
CREATE TABLE IF NOT EXISTS ohlcv (
    time        TIMESTAMPTZ     NOT NULL,
    exchange    TEXT            NOT NULL,
    symbol      TEXT            NOT NULL,
    open        NUMERIC(28, 10) NOT NULL,
    high        NUMERIC(28, 10) NOT NULL,
    low         NUMERIC(28, 10) NOT NULL,
    close       NUMERIC(28, 10) NOT NULL,
    volume      NUMERIC(28, 10) NOT NULL
);
SELECT create_hypertable('ohlcv', 'time', if_not_exists => TRUE);
SELECT add_retention_policy('ohlcv', INTERVAL '90 days', if_not_exists => TRUE);

-- ============================================================
-- 5. Spreads  (from timescale.py)
-- ============================================================
CREATE TABLE IF NOT EXISTS spreads (
    time            TIMESTAMPTZ     NOT NULL,
    strategy        TEXT            NOT NULL,
    exchange_pair   TEXT            NOT NULL,
    gross_spread    NUMERIC(28, 10) NOT NULL,
    net_spread      NUMERIC(28, 10) NOT NULL
);
SELECT create_hypertable('spreads', 'time', if_not_exists => TRUE);
SELECT add_retention_policy('spreads', INTERVAL '30 days', if_not_exists => TRUE);

-- ============================================================
-- 6. Signals  (from timescale.py)
-- ============================================================
CREATE TABLE IF NOT EXISTS signals (
    time            TIMESTAMPTZ     NOT NULL,
    strategy        TEXT            NOT NULL,
    signal_type     TEXT            NOT NULL,
    value           NUMERIC(28, 10),
    metadata        JSONB
);
SELECT create_hypertable('signals', 'time', if_not_exists => TRUE);
SELECT add_retention_policy('signals', INTERVAL '30 days', if_not_exists => TRUE);

-- ============================================================
-- 7. Continuous aggregates  (from timescale.py)
-- ============================================================
CREATE MATERIALIZED VIEW IF NOT EXISTS ohlcv_1m_agg
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 minute', time) AS bucket,
    exchange,
    symbol,
    first(open,  time)  AS open,
    max(high)           AS high,
    min(low)            AS low,
    last(close, time)   AS close,
    sum(volume)         AS volume
FROM ohlcv
GROUP BY bucket, exchange, symbol
WITH NO DATA;

CREATE MATERIALIZED VIEW IF NOT EXISTS ohlcv_1h
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', time) AS bucket,
    exchange,
    symbol,
    first(open,  time)  AS open,
    max(high)           AS high,
    min(low)            AS low,
    last(close, time)   AS close,
    sum(volume)         AS volume
FROM ohlcv
GROUP BY bucket, exchange, symbol
WITH NO DATA;

-- ============================================================
-- 8. Adaptive threshold log  (from 002_tuning_logs.sql)
-- ============================================================
CREATE TABLE IF NOT EXISTS adaptive_threshold_log (
    timestamp   TIMESTAMPTZ      NOT NULL,
    old_edge    DOUBLE PRECISION NOT NULL,
    new_edge    DOUBLE PRECISION NOT NULL,
    win_rate    DOUBLE PRECISION NOT NULL,
    trades      INTEGER          NOT NULL
);
SELECT create_hypertable('adaptive_threshold_log', 'timestamp', if_not_exists => TRUE);

-- ============================================================
-- 9. Regime detector log  (from 002_tuning_logs.sql)
-- ============================================================
CREATE TABLE IF NOT EXISTS regime_detector_log (
    timestamp   TIMESTAMPTZ      NOT NULL,
    old_regime  TEXT             NOT NULL,
    new_regime  TEXT             NOT NULL,
    volatility  DOUBLE PRECISION NOT NULL,
    spread_std  DOUBLE PRECISION NOT NULL DEFAULT 0.0
);
SELECT create_hypertable('regime_detector_log', 'timestamp', if_not_exists => TRUE);

-- ============================================================
-- 10. Shadow stage results  (from 003_shadow_stage_results.sql)
-- ============================================================
CREATE TABLE IF NOT EXISTS shadow_stage_results (
    id                SERIAL      PRIMARY KEY,
    stage_name        TEXT        NOT NULL,
    passed            BOOLEAN     NOT NULL,
    started_at        TIMESTAMPTZ NOT NULL,
    ended_at          TIMESTAMPTZ NOT NULL,
    stats_snapshot    JSONB       NOT NULL DEFAULT '{}',
    gate_results      JSONB       NOT NULL DEFAULT '{}',
    resource_snapshot JSONB       NOT NULL DEFAULT '{}',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (stage_name, started_at)
);

-- ============================================================
-- 11. Position WAL  (from schema.py ORM)
-- ============================================================
CREATE TABLE IF NOT EXISTS position_wal (
    wal_id      BIGSERIAL        PRIMARY KEY,
    ts          TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    event_type  TEXT             NOT NULL,
    strategy_id TEXT             NOT NULL,
    exchange_id TEXT             NOT NULL,
    symbol      TEXT             NOT NULL,
    side        TEXT             NOT NULL,
    quantity    NUMERIC(28, 10)  NOT NULL,
    avg_price   NUMERIC(28, 10)  NOT NULL,
    metadata    JSON,
    checksum    TEXT             NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wal_strategy_ts      ON position_wal (strategy_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_wal_exchange_symbol  ON position_wal (exchange_id, symbol);

-- ============================================================
-- 12. Capital allocation lock  (from schema.py ORM)
-- ============================================================
CREATE TABLE IF NOT EXISTS capital_allocation_lock (
    lock_id     BIGSERIAL        PRIMARY KEY,
    ts          TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    strategy_id TEXT             NOT NULL,
    exchange_id TEXT             NOT NULL,
    amount      NUMERIC(28, 10)  NOT NULL,
    currency    TEXT             NOT NULL,
    status      TEXT             NOT NULL,
    expires_at  TIMESTAMPTZ      NOT NULL
);

-- ============================================================
-- 13. Trades  (from schema.py ORM)
-- ============================================================
CREATE TABLE IF NOT EXISTS trades (
    trade_id    BIGSERIAL        PRIMARY KEY,
    ts          TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    strategy_id TEXT             NOT NULL,
    exchange_id TEXT             NOT NULL,
    symbol      TEXT             NOT NULL,
    side        TEXT             NOT NULL,
    quantity    NUMERIC(28, 10)  NOT NULL,
    price       NUMERIC(28, 10)  NOT NULL,
    fee         NUMERIC(28, 10)  NOT NULL,
    order_id    TEXT             NOT NULL
);

-- ============================================================
-- 14. Orders  (from schema.py ORM)
-- ============================================================
CREATE TABLE IF NOT EXISTS orders (
    order_id    TEXT             PRIMARY KEY,
    ts          TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    strategy_id TEXT             NOT NULL,
    exchange_id TEXT             NOT NULL,
    symbol      TEXT             NOT NULL,
    side        TEXT             NOT NULL,
    type        TEXT             NOT NULL,
    quantity    NUMERIC(28, 10)  NOT NULL,
    price       NUMERIC(28, 10),
    status      TEXT             NOT NULL,
    filled_qty  NUMERIC(28, 10)  NOT NULL DEFAULT 0
);

-- ============================================================
-- 15. Strategy config  (from schema.py ORM)
-- ============================================================
CREATE TABLE IF NOT EXISTS strategy_config (
    strategy_id TEXT             PRIMARY KEY,
    type        TEXT             NOT NULL,
    params      JSON             NOT NULL DEFAULT '{}',
    is_active   BOOLEAN          NOT NULL DEFAULT TRUE,
    updated_at  TIMESTAMPTZ      NOT NULL DEFAULT NOW()
);

-- ============================================================
-- 16. Materialized views  (from attribution.py migration_sql())
-- ============================================================
CREATE MATERIALIZED VIEW IF NOT EXISTS strategy_daily_pnl AS
SELECT
    time_bucket('1 day', ts) AS day,
    strategy_id,
    SUM(net_pnl) AS total_pnl,
    COUNT(*) AS trade_count,
    SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END)::float / COUNT(*) AS win_rate
FROM execution_log
GROUP BY day, strategy_id
ORDER BY day DESC;

CREATE MATERIALIZED VIEW IF NOT EXISTS exchange_daily_pnl AS
SELECT
    time_bucket('1 day', ts) AS day,
    buy_exchange AS exchange_id,
    SUM(net_pnl) / 2 AS total_pnl,
    COUNT(*) AS trade_count
FROM execution_log
GROUP BY day, buy_exchange
ORDER BY day DESC;

CREATE MATERIALIZED VIEW IF NOT EXISTS pair_daily_pnl AS
SELECT
    time_bucket('1 day', ts) AS day,
    symbol,
    SUM(net_pnl) AS total_pnl,
    COUNT(*) AS trade_count,
    SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END)::float / COUNT(*) AS win_rate
FROM execution_log
GROUP BY day, symbol
ORDER BY day DESC;

-- Unique indexes required for REFRESH MATERIALIZED VIEW CONCURRENTLY
CREATE UNIQUE INDEX IF NOT EXISTS strategy_daily_pnl_idx ON strategy_daily_pnl (day, strategy_id);
CREATE UNIQUE INDEX IF NOT EXISTS exchange_daily_pnl_idx ON exchange_daily_pnl (day, exchange_id);
CREATE UNIQUE INDEX IF NOT EXISTS pair_daily_pnl_idx ON pair_daily_pnl (day, symbol);
