-- ReadFlow Reader Data Model Schema Migration
-- Version: 004
-- Description: Store a hash of each issued bearer token for server-side verification.

CREATE TABLE IF NOT EXISTS reader_access_tokens (
    device_id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL DEFAULT (unixepoch()),
    last_used_at INTEGER NOT NULL DEFAULT (unixepoch()),
    FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_reader_access_tokens_hash
    ON reader_access_tokens(token_hash);
