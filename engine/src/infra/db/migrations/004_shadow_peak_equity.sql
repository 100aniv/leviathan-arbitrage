-- Migration 004: Shadow peak equity persistence table
-- US-256: Persists peak_equity across restarts for accurate MDD calculation.

CREATE TABLE IF NOT EXISTS shadow_peak_equity (
    id SERIAL PRIMARY KEY,
    peak_equity DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seed a single row for upsert pattern
INSERT INTO shadow_peak_equity (id, peak_equity) VALUES (1, 0.0)
ON CONFLICT (id) DO NOTHING;
