-- Enable TimescaleDB
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- ─────────────────────────────────────────────────────────────
-- 1. positions_1sec  — high-frequency, rolling 24-hour window
--    PK (time, mmsi) enables ON CONFLICT DO NOTHING for idempotent inserts.
--    Existing DBs created without PK need a one-off migration to add it (after dedupe).
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS positions_1sec (
    time        TIMESTAMPTZ     NOT NULL,
    mmsi        BIGINT          NOT NULL,
    latitude    DOUBLE PRECISION,
    longitude   DOUBLE PRECISION,
    speed       REAL,
    course      REAL,
    heading     SMALLINT,
    nav_status  SMALLINT,
    rot         REAL,
    msg_type    SMALLINT,
    PRIMARY KEY (time, mmsi)
);

SELECT create_hypertable('positions_1sec', 'time',
    if_not_exists      => TRUE,
    chunk_time_interval => INTERVAL '1 hour'   -- small chunks for fast drops
);

-- Compress chunks older than 2 hours (still within retention window but cold)
ALTER TABLE positions_1sec SET (
    timescaledb.compress,
    timescaledb.compress_orderby   = 'time DESC',
    timescaledb.compress_segmentby = 'mmsi'
);
SELECT add_compression_policy('positions_1sec', INTERVAL '2 hours',  if_not_exists => TRUE);

-- Drop data older than 24 hours automatically
SELECT add_retention_policy('positions_1sec', INTERVAL '24 hours', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_1sec_mmsi_time ON positions_1sec (mmsi, time DESC);

-- ─────────────────────────────────────────────────────────────
-- 2. positions_1min  — historical archive, kept forever
--    Populated via continuous aggregate from positions_1sec,
--    then the aggregate materialises into this permanent table.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS positions_1min (
    time        TIMESTAMPTZ     NOT NULL,
    mmsi        BIGINT          NOT NULL,
    latitude    DOUBLE PRECISION,
    longitude   DOUBLE PRECISION,
    speed       REAL,
    course      REAL,
    heading     SMALLINT,
    nav_status  SMALLINT,
    msg_count   SMALLINT,
    PRIMARY KEY (time, mmsi)
);

SELECT create_hypertable('positions_1min', 'time',
    if_not_exists      => TRUE,
    chunk_time_interval => INTERVAL '7 days'
);

ALTER TABLE positions_1min SET (
    timescaledb.compress,
    timescaledb.compress_orderby   = 'time DESC',
    timescaledb.compress_segmentby = 'mmsi'
);
SELECT add_compression_policy('positions_1min', INTERVAL '7 days', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_1min_mmsi_time ON positions_1min (mmsi, time DESC);

-- ─────────────────────────────────────────────────────────────
-- 3. Continuous aggregate: 1-minute buckets from 1-sec data
--    This drives the downsampling from 1sec → 1min.
-- ─────────────────────────────────────────────────────────────
CREATE MATERIALIZED VIEW IF NOT EXISTS positions_1min_agg
WITH (timescaledb.continuous) AS
    SELECT
        time_bucket('1 minute', time)   AS bucket,
        mmsi,
        last(latitude,   time)          AS latitude,
        last(longitude,  time)          AS longitude,
        last(speed,      time)          AS speed,
        last(course,     time)          AS course,
        last(heading,    time)          AS heading,
        last(nav_status, time)          AS nav_status,
        count(*)::SMALLINT              AS msg_count
    FROM positions_1sec
    GROUP BY bucket, mmsi
WITH NO DATA;

-- Refresh every minute, for data up to 1 minute ago
SELECT add_continuous_aggregate_policy('positions_1min_agg',
    start_offset      => INTERVAL '10 minutes',
    end_offset        => INTERVAL '1 minute',
    schedule_interval => INTERVAL '1 minute',
    if_not_exists     => TRUE
);

-- ─────────────────────────────────────────────────────────────
-- 4. Function: materialise aggregate rows → positions_1min
--    Called by the ingestion service every minute to persist
--    downsampled data beyond the 24-hour 1-sec window.
-- ─────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION flush_1min_from_agg(cutoff TIMESTAMPTZ DEFAULT NOW() - INTERVAL '2 minutes')
RETURNS INTEGER AS $$
DECLARE
    inserted INTEGER;
BEGIN
    INSERT INTO positions_1min
        (time, mmsi, latitude, longitude, speed, course, heading, nav_status, msg_count)
    SELECT
        bucket, mmsi, latitude, longitude, speed, course, heading, nav_status, msg_count
    FROM positions_1min_agg
    WHERE bucket <= cutoff
    ON CONFLICT DO NOTHING;

    GET DIAGNOSTICS inserted = ROW_COUNT;
    RETURN inserted;
END;
$$ LANGUAGE plpgsql;

-- ─────────────────────────────────────────────────────────────
-- 5. vessels — static info, upserted
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS vessels (
    mmsi                BIGINT          PRIMARY KEY,
    imo                 BIGINT,
    callsign            TEXT,
    name                TEXT,
    ship_type           SMALLINT,
    ship_type_text      TEXT,
    dim_to_bow          SMALLINT,
    dim_to_stern        SMALLINT,
    dim_to_port         SMALLINT,
    dim_to_starboard    SMALLINT,
    draught             REAL,
    destination         TEXT,
    eta                 TEXT,
    first_seen          TIMESTAMPTZ     DEFAULT NOW(),
    last_updated        TIMESTAMPTZ     DEFAULT NOW()
);
