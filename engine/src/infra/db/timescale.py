"""
TimescaleDB hypertable setup.

Creates hypertables for time-series data:
  - ohlcv: OHLCV candle data per (exchange, symbol)
  - spreads: Arbitrage spread observations per strategy
  - signals: Trading signals emitted by strategies
"""

import logging

logger = logging.getLogger(__name__)

# DDL for raw tables (TimescaleDB converts them to hypertables)
_CREATE_OHLCV = """
CREATE TABLE IF NOT EXISTS ohlcv (
    time        TIMESTAMPTZ     NOT NULL,
    exchange    TEXT            NOT NULL,
    symbol      TEXT            NOT NULL,
    open        NUMERIC(28, 10) NOT NULL,
    high        NUMERIC(28, 10) NOT NULL,
    low         NUMERIC(28, 10) NOT NULL,
    close       NUMERIC(28, 10) NOT NULL,
    volume      NUMERIC(28, 10) NOT NULL
);
"""

_CREATE_SPREADS = """
CREATE TABLE IF NOT EXISTS spreads (
    time            TIMESTAMPTZ     NOT NULL,
    strategy        TEXT            NOT NULL,
    exchange_pair   TEXT            NOT NULL,
    gross_spread    NUMERIC(28, 10) NOT NULL,
    net_spread      NUMERIC(28, 10) NOT NULL
);
"""

_CREATE_SIGNALS = """
CREATE TABLE IF NOT EXISTS signals (
    time            TIMESTAMPTZ NOT NULL,
    strategy        TEXT        NOT NULL,
    signal_type     TEXT        NOT NULL,
    value           NUMERIC(28, 10),
    metadata        JSONB
);
"""

_CREATE_HYPERTABLES = [
    "SELECT create_hypertable('ohlcv', 'time', if_not_exists => TRUE);",
    "SELECT create_hypertable('spreads', 'time', if_not_exists => TRUE);",
    "SELECT create_hypertable('signals', 'time', if_not_exists => TRUE);",
]

_ADD_RETENTION_POLICIES = [
    # Keep ohlcv for 90 days
    "SELECT add_retention_policy('ohlcv', INTERVAL '90 days', if_not_exists => TRUE);",
    # Keep spreads for 30 days
    "SELECT add_retention_policy('spreads', INTERVAL '30 days', if_not_exists => TRUE);",
    # Keep signals for 30 days
    "SELECT add_retention_policy('signals', INTERVAL '30 days', if_not_exists => TRUE);",
]

_CREATE_CONTINUOUS_AGGREGATES = [
    # 1-minute OHLCV aggregates
    """
    CREATE MATERIALIZED VIEW IF NOT EXISTS ohlcv_1m
    WITH (timescaledb.continuous) AS
    SELECT
        time_bucket('1 minute', time) AS bucket,
        exchange,
        symbol,
        first(open,  time)  AS open,
        max(high)           AS high,
        min(low)            AS low,
        last(close, time)   AS close,
        sum(volume)         AS volume
    FROM ohlcv
    GROUP BY bucket, exchange, symbol
    WITH NO DATA;
    """,
    # 1-hour OHLCV aggregates
    """
    CREATE MATERIALIZED VIEW IF NOT EXISTS ohlcv_1h
    WITH (timescaledb.continuous) AS
    SELECT
        time_bucket('1 hour', time) AS bucket,
        exchange,
        symbol,
        first(open,  time)  AS open,
        max(high)           AS high,
        min(low)            AS low,
        last(close, time)   AS close,
        sum(volume)         AS volume
    FROM ohlcv
    GROUP BY bucket, exchange, symbol
    WITH NO DATA;
    """,
]


async def setup_timescaledb(conn) -> None:
    """
    Create TimescaleDB hypertables, retention policies, and continuous aggregates.

    Must be called with a live asyncpg connection after PostgreSQL + TimescaleDB is running.
    """
    logger.info("Setting up TimescaleDB hypertables")

    # Create raw tables
    for ddl in [_CREATE_OHLCV, _CREATE_SPREADS, _CREATE_SIGNALS]:
        await conn.execute(ddl)

    # Convert to hypertables
    for sql in _CREATE_HYPERTABLES:
        try:
            await conn.execute(sql)
        except Exception as exc:
            logger.warning("Hypertable creation skipped (may already exist): %s", exc)

    # Add retention policies
    for sql in _ADD_RETENTION_POLICIES:
        try:
            await conn.execute(sql)
        except Exception as exc:
            logger.warning("Retention policy skipped: %s", exc)

    # Continuous aggregates
    for sql in _CREATE_CONTINUOUS_AGGREGATES:
        try:
            await conn.execute(sql)
        except Exception as exc:
            logger.warning("Continuous aggregate skipped (may already exist): %s", exc)

    logger.info("TimescaleDB setup complete")
