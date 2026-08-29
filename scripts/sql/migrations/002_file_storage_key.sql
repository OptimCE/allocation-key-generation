-- Migration 002: rename generation.file_url to file_storage_key.
--
-- Before this migration, file_url stored an externally hosted URL passed by
-- the client. After it, the column stores an opaque S3 object key inside
-- STORAGE_BUCKET (MinIO). The service uploads the file at creation time and
-- the worker deletes it on terminal outcomes. Widening to VARCHAR(512) gives
-- headroom for the longer keys (allocations/<community>/<uuid>/<filename>).

BEGIN;

ALTER TABLE generation RENAME COLUMN file_url TO file_storage_key;
ALTER TABLE generation ALTER COLUMN file_storage_key TYPE VARCHAR(512);

INSERT INTO schema_version (version, description)
VALUES (2, 'Rename generation.file_url to file_storage_key, widen to 512');

COMMIT;
