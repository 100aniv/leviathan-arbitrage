-- Day 6 ExecutionJournal schema (Path-B v2).
-- Append-only hash-chained event log backing order intent → ACK → fill → cancel.
-- Maintained by src/execution/journal.py; this file exists for inspectability.

CREATE TABLE IF NOT EXISTS execution_events (
    seq           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms         INTEGER NOT NULL,
    order_id      TEXT    NOT NULL,
    state         TEXT    NOT NULL,
    payload_json  TEXT    NOT NULL,
    prev_hash     TEXT    NOT NULL,
    self_hash     TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_execution_events_ts_order
    ON execution_events (ts_ms, order_id);

CREATE INDEX IF NOT EXISTS idx_execution_events_order_id
    ON execution_events (order_id);
