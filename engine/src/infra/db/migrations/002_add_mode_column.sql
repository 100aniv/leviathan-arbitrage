-- LEVIATHAN Migration 002
-- Adds mode column to execution_log for backtest/paper/live data separation.

ALTER TABLE execution_log ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'live';
CREATE INDEX IF NOT EXISTS idx_exec_mode ON execution_log (mode, ts DESC);
