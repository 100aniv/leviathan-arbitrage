-- Path-B Day-1 : ground-truth PnL snapshot hypertable.
-- Runs on TimescaleDB. Falls back to JSONL append log when unreachable
-- (see ExchangePnLSnapshot._fallback_path). Keep idempotent.

CREATE TABLE IF NOT EXISTS exchange_pnl_snapshots (
    ts              TIMESTAMPTZ NOT NULL,
    exchange        TEXT        NOT NULL,
    income_type     TEXT        NOT NULL,
    symbol          TEXT        NOT NULL DEFAULT '',
    asset           TEXT        NOT NULL DEFAULT '',
    amount_usd      NUMERIC(28, 10) NOT NULL,
    tran_id         TEXT        NOT NULL DEFAULT '',
    raw_json        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (exchange, tran_id, ts)
);

-- Promote to TimescaleDB hypertable (no-op if the extension is missing —
-- the caller catches the exception and records via the JSONL fallback).
SELECT create_hypertable(
    'exchange_pnl_snapshots',
    'ts',
    if_not_exists => TRUE,
    migrate_data  => TRUE
);

CREATE INDEX IF NOT EXISTS ix_pnl_snap_exchange_ts
    ON exchange_pnl_snapshots (exchange, ts DESC);
CREATE INDEX IF NOT EXISTS ix_pnl_snap_income_type
    ON exchange_pnl_snapshots (income_type);
