-- =============================================================================
-- LEVIATHAN TimescaleDB Compression + Retention Policies — W4 Infra Audit
-- Run once after TimescaleDB tables are created (via Alembic migration).
-- All tables must have compression enabled before adding a policy.
-- Reference: https://docs.timescale.com/use-timescale/latest/compression/
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Enable compression on each hypertable (must precede add_compression_policy)
-- ---------------------------------------------------------------------------

-- orderbook_updates: compress by exchange+symbol, order by time DESC
ALTER TABLE orderbook_updates SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'exchange, symbol',
    timescaledb.compress_orderby   = 'time DESC'
);

-- trades: compress by exchange+symbol, order by time DESC
ALTER TABLE trades SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'exchange, symbol',
    timescaledb.compress_orderby   = 'time DESC'
);

-- execution_events: compress by exchange+strategy, order by time DESC
-- (ExecutionJournal table from Day 6; populated when EXECUTION_JOURNAL_ENABLED=true)
ALTER TABLE execution_events SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'exchange, strategy',
    timescaledb.compress_orderby   = 'time DESC'
);

-- ---------------------------------------------------------------------------
-- Compression policies
-- ---------------------------------------------------------------------------

-- Compress orderbook_updates chunks older than 7 days
-- Rationale: raw L2 data is large; 7d keeps recent data hot for TCA queries
SELECT add_compression_policy('orderbook_updates', INTERVAL '7 days');

-- Compress trades chunks older than 14 days
-- Rationale: trade records needed for 7-day TCA window; compress beyond that
SELECT add_compression_policy('trades', INTERVAL '14 days');

-- Compress execution_events chunks older than 30 days
-- Rationale: journal audit trail needed for month-end reconciliation
SELECT add_compression_policy('execution_events', INTERVAL '30 days');

-- ---------------------------------------------------------------------------
-- Retention (drop) policies
-- ---------------------------------------------------------------------------

-- Drop raw orderbook_updates older than 90 days
-- Rationale: compressed data sufficient beyond 90d; reduces storage linearly
SELECT add_retention_policy('orderbook_updates', INTERVAL '90 days');

-- Drop trades older than 365 days (keep 1 year for tax/audit)
SELECT add_retention_policy('trades', INTERVAL '365 days');

-- Drop execution_events older than 180 days
SELECT add_retention_policy('execution_events', INTERVAL '180 days');

-- ---------------------------------------------------------------------------
-- Verification queries (run after applying policies)
-- ---------------------------------------------------------------------------

-- Check compression policies
-- SELECT hypertable_name, compress_after FROM timescaledb_information.compression_settings;

-- Check retention policies
-- SELECT hypertable_name, drop_after FROM timescaledb_information.drop_chunks_policies;

-- Check compression status per chunk
-- SELECT chunk_name, compression_status, before_compression_total_bytes, after_compression_total_bytes
-- FROM chunk_compression_stats('orderbook_updates') ORDER BY chunk_name;
