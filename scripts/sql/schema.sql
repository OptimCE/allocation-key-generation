-- ============================================================================
-- Local database schema (LOCAL_DATABASE_URL).
--
-- This file is the single source of truth for the LOCAL database only.
-- CRM tables (allocation_key, iteration, consumer) live in a separate DB
-- hosted elsewhere and are NOT declared here.
--
-- Mirrors shared/models/local_models.py. When changing models, update
-- this file and add a migration under scripts/sql/migrations/.
-- ============================================================================

-- ---- Shared utilities ------------------------------------------------------
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS trigger AS $$
BEGIN
  NEW.updated_at := CURRENT_TIMESTAMP;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TABLE IF NOT EXISTS schema_version (
    version      INTEGER     PRIMARY KEY,
    description  TEXT        NOT NULL,
    applied_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT into schema_version (version, description) VALUES(
       1, 'First version'
) ON CONFLICT DO NOTHING;

INSERT into schema_version (version, description) VALUES(
       2, 'Rename generation.file_url to file_storage_key, widen to 512'
) ON CONFLICT DO NOTHING;

INSERT into schema_version (version, description) VALUES(
       3, 'Allow CRM-sourced generations (source, sharing operation, period)'
) ON CONFLICT DO NOTHING;

-- ---- generation ------------------------------------------------------------
-- One row per allocation-key generation request. Holds the source file
-- reference, the chosen algorithm + its input payload snapshot, and the
-- execution status. Results (allocation_key_generated and children) are
-- linked by id_generation with ON DELETE CASCADE.
CREATE TABLE IF NOT EXISTS generation (
    id                 INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name               VARCHAR(255) NOT NULL,
    id_community       INTEGER NOT NULL,

    -- Source data: 1=FILE (uploaded CSV/XLSX), 2=CRM (meter_consumption).
    -- Exactly one of the two column groups below is populated; the
    -- ck_generation_source CHECK is what enforces that, now that the file
    -- columns can no longer be NOT NULL.
    source             SMALLINT     NOT NULL DEFAULT 1,

    -- FILE only. file_storage_key is the object key inside STORAGE_BUCKET
    -- (MinIO). The service uploads on creation; the worker deletes on terminal
    -- outcomes. injection_name names the production column inside the file.
    file_storage_key   VARCHAR(512) NULL,
    file_name          VARCHAR(255) NULL,
    injection_name     VARCHAR(255) NULL,

    -- CRM only. The sharing operation and the inclusive local date range read
    -- from meter_consumption. No FK: the CRM lives in a separate database.
    id_sharing_operation INTEGER    NULL,
    period_start       DATE         NULL,
    period_end         DATE         NULL,

    -- Algorithm snapshot (keyed to algorithms.registry)
    algorithm_name     VARCHAR(64)  NOT NULL,
    algorithm_version  VARCHAR(32)  NOT NULL,
    inputs             JSONB        NOT NULL,

    -- Execution state: 0=PENDING, 1=SUCCESS, 2=FAILED
    status             INTEGER      NOT NULL DEFAULT 0,
    error_message      TEXT         NULL,

    -- Non-blocking findings from the CRM pre-flight (meters with gaps, which
    -- are zero-filled). Persisted so the warning outlives the preview screen.
    data_warnings      JSONB        NULL,

    created_at         TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT ck_generation_source CHECK (
        (source = 1
            AND file_storage_key IS NOT NULL
            AND file_name        IS NOT NULL
            AND injection_name   IS NOT NULL)
     OR (source = 2
            AND id_sharing_operation IS NOT NULL
            AND period_start         IS NOT NULL
            AND period_end           IS NOT NULL
            AND period_start <= period_end)
    )
);

CREATE INDEX IF NOT EXISTS idx_generation_id_community ON generation (id_community);
CREATE INDEX IF NOT EXISTS idx_generation_status        ON generation (status);
CREATE INDEX IF NOT EXISTS idx_generation_algorithm     ON generation (algorithm_name);

DROP TRIGGER IF EXISTS trg_generation_set_updated_at ON generation;
CREATE TRIGGER trg_generation_set_updated_at
    BEFORE UPDATE ON generation
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- ---- allocation_key_generated ----------------------------------------------
-- Candidate allocation keys produced by a generation. Lives in the local
-- DB until a user explicitly saves one, at which point the service copies
-- it into the CRM database (see shared.crm_repository).
CREATE TABLE IF NOT EXISTS allocation_key_generated (
    id             INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name           VARCHAR(255) NOT NULL,
    description    VARCHAR(255) NOT NULL,

    -- Denormalised sum of all child iteration.surplus_total, kept on the
    -- parent so list/sort endpoints don't need to load iterations.
    surplus_total  DOUBLE PRECISION NOT NULL,

    id_generation  INTEGER NOT NULL REFERENCES generation (id) ON DELETE CASCADE,
    id_community   INTEGER NOT NULL,

    created_at     TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_allocation_key_generated_generation
    ON allocation_key_generated (id_generation);
CREATE INDEX IF NOT EXISTS idx_allocation_key_generated_community
    ON allocation_key_generated (id_community);

DROP TRIGGER IF EXISTS trg_allocation_key_generated_set_updated_at ON allocation_key_generated;
CREATE TRIGGER trg_allocation_key_generated_set_updated_at
    BEFORE UPDATE ON allocation_key_generated
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- ---- iteration_generated ---------------------------------------------------
CREATE TABLE IF NOT EXISTS iteration_generated (
    id                          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    number                      INTEGER NOT NULL,
    energy_allocated_percentage DOUBLE PRECISION NOT NULL,
    surplus_total               DOUBLE PRECISION NOT NULL,

    id_allocation_key           INTEGER NOT NULL REFERENCES allocation_key_generated (id) ON DELETE CASCADE,
    id_community                INTEGER NOT NULL,

    created_at                  TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_iteration_generated_allocation_key
    ON iteration_generated (id_allocation_key);
CREATE INDEX IF NOT EXISTS idx_iteration_generated_community
    ON iteration_generated (id_community);

DROP TRIGGER IF EXISTS trg_iteration_generated_set_updated_at ON iteration_generated;
CREATE TRIGGER trg_iteration_generated_set_updated_at
    BEFORE UPDATE ON iteration_generated
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- ---- consumer_generated ----------------------------------------------------
CREATE TABLE IF NOT EXISTS consumer_generated (
    id                          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name                        VARCHAR(255) NOT NULL,
    energy_allocated_percentage DOUBLE PRECISION NOT NULL,

    id_iteration                INTEGER NOT NULL REFERENCES iteration_generated (id) ON DELETE CASCADE,
    id_community                INTEGER NOT NULL,

    created_at                  TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_consumer_generated_iteration
    ON consumer_generated (id_iteration);
CREATE INDEX IF NOT EXISTS idx_consumer_generated_community
    ON consumer_generated (id_community);

DROP TRIGGER IF EXISTS trg_consumer_generated_set_updated_at ON consumer_generated;
CREATE TRIGGER trg_consumer_generated_set_updated_at
    BEFORE UPDATE ON consumer_generated
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
