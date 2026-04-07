-- Migration 007: Remove orderbook_snapshots retention policy
-- Required for K-BT historical data (2024-01 ~ 2025-03)
-- Migration 005 set 30-day retention, migration 001 originally set 7-day.
-- Backtest data from 2024-01-10 would be auto-deleted by the background worker.
SELECT remove_retention_policy('orderbook_snapshots', if_exists => true);
