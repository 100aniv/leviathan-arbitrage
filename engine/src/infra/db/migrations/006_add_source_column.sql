-- Migration 006: Add source column to orderbook_snapshots
-- Separates backtest/live/paper data at DB level (US-365)

ALTER TABLE orderbook_snapshots
ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'live';

CREATE INDEX IF NOT EXISTS idx_orderbook_snapshots_source
    ON orderbook_snapshots (source);
