-- Migration 003: Progressive Shadow stage results table
-- US-054: Stores per-stage gate evaluation results for progressive shadow runs.

CREATE TABLE IF NOT EXISTS shadow_stage_results (
    id SERIAL PRIMARY KEY,
    stage_name TEXT NOT NULL,
    passed BOOLEAN NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ NOT NULL,
    stats_snapshot JSONB NOT NULL DEFAULT '{}',
    gate_results JSONB NOT NULL DEFAULT '{}',
    resource_snapshot JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (stage_name, started_at)
);
