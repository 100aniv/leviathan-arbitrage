-- Migration 010: Restore orderbook_snapshots retention (7 days)
-- BUG-185: migration 007 removed retention for K-BT backtest (2024-01~2025-03 data).
-- Running live without retention rolled orderbook_snapshots to 80GB+ in ~5 days,
-- filling the Docker VM disk (125GB) and crashing PostgreSQL into a PANIC loop.
-- Testing phase no longer needs historical backtest window → reinstate 7-day retention.
-- Also add retention for market_data_1m (migration 009) to prevent the same leak.
SELECT add_retention_policy('orderbook_snapshots', INTERVAL '7 days', if_not_exists => true);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM _timescaledb_catalog.hypertable WHERE table_name = 'market_data_1m') THEN
        PERFORM add_retention_policy('market_data_1m', INTERVAL '30 days', if_not_exists => true);
    END IF;
END$$;
