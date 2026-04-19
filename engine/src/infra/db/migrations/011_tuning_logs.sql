-- US-047: Adaptive Threshold + Regime Detector logging tables
-- TimescaleDB hypertables for tuning history

CREATE TABLE IF NOT EXISTS adaptive_threshold_log (
    timestamp   TIMESTAMPTZ NOT NULL,
    old_edge    DOUBLE PRECISION NOT NULL,
    new_edge    DOUBLE PRECISION NOT NULL,
    win_rate    DOUBLE PRECISION NOT NULL,
    trades      INTEGER NOT NULL
);

SELECT create_hypertable(
    'adaptive_threshold_log', 'timestamp',
    if_not_exists => TRUE
);

CREATE TABLE IF NOT EXISTS regime_detector_log (
    timestamp   TIMESTAMPTZ NOT NULL,
    old_regime  TEXT NOT NULL,
    new_regime  TEXT NOT NULL,
    volatility  DOUBLE PRECISION NOT NULL,
    spread_std  DOUBLE PRECISION NOT NULL DEFAULT 0.0
);

SELECT create_hypertable(
    'regime_detector_log', 'timestamp',
    if_not_exists => TRUE
);
