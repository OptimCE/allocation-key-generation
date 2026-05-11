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
);

-- ---- generation ------------------------------------------------------------
-- One row per allocation-key generation request. Holds the source file
-- reference, the chosen algorithm + its input payload snapshot, and the
-- execution status. Results (allocation_key_generated and children) are
-- linked by id_generation with ON DELETE CASCADE.
CREATE TABLE IF NOT EXISTS generation (
    id                 INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name               VARCHAR(255) NOT NULL,
    id_community       INTEGER NOT NULL,

    -- Source data
    file_url           VARCHAR(255) NOT NULL,
    file_name          VARCHAR(255) NOT NULL,
    injection_name     VARCHAR(255) NOT NULL,

    -- Algorithm snapshot (keyed to algorithms.registry)
    algorithm_name     VARCHAR(64)  NOT NULL,
    algorithm_version  VARCHAR(32)  NOT NULL,
    inputs             JSONB        NOT NULL,

    -- Execution state: 0=PENDING, 1=SUCCESS, 2=FAILED
    status             INTEGER      NOT NULL DEFAULT 0,
    error_message      TEXT         NULL,

    created_at         TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP
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
