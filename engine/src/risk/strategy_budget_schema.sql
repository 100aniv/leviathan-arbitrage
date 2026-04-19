-- Phoenix Path-B Day-3: per-strategy daily loss budget ledger.
-- Persists daily budget state for restart continuity. Falls back to JSON
-- under engine/logs/strategy_budgets/YYYYMMDD.json when TSDB unreachable.

CREATE TABLE IF NOT EXISTS strategy_budgets (
    reset_date          DATE        NOT NULL,
    strategy_id         TEXT        NOT NULL,
    daily_loss_budget_usd   NUMERIC(28, 10) NOT NULL,
    daily_pnl_balance_usd   NUMERIC(28, 10) NOT NULL,
    reset_ts_utc        TIMESTAMPTZ NOT NULL,
    is_halted           BOOLEAN     NOT NULL DEFAULT FALSE,
    last_update_ts      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (reset_date, strategy_id)
);

CREATE INDEX IF NOT EXISTS ix_strategy_budget_latest
    ON strategy_budgets (strategy_id, reset_date DESC);
