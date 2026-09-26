-- ReadFlow Reader Data Model Schema Migration
-- Version: 003
-- Description: Allow conversations that are not tied to a source
-- The table rebuild is performed by ReaderDatabase because SQLite cannot
-- alter a NOT NULL column in place.

INSERT OR IGNORE INTO schema_version (version, description)
VALUES (3, 'Allow reader conversations without a source');
