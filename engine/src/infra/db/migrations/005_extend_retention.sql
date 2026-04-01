-- Migration 005: Extend orderbook_snapshots retention from 7 days to 30 days
-- Required for Phase J backtest (7-day retention + last Shadow March 8 = 0건)
SELECT add_retention_policy('orderbook_snapshots', INTERVAL '30 days', if_not_exists => true);
