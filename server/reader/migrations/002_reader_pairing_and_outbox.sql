-- ReadFlow Reader Data Model Schema Migration
-- Version: 002
-- Description: Add pairing_tokens table and migrate sync_outbox schema
-- This migration is idempotent and can be safely re-run
--
-- IMPORTANT: This migration includes a schema transformation for reader_sync_outbox
-- that requires conditional logic. The migration runner in database.py handles
-- schema detection and executes the appropriate transformation.

-- Enable foreign key constraints
PRAGMA foreign_keys = ON;

-- ============================================================================
-- 1. Add Pairing Tokens Table
-- ============================================================================
CREATE TABLE IF NOT EXISTS pairing_tokens (
    token_hash TEXT PRIMARY KEY,
    device_name TEXT NOT NULL,
    device_type TEXT NOT NULL CHECK (device_type IN ('macos', 'ios', 'android')),
    expires_at INTEGER NOT NULL,
    used_at INTEGER,
    used_by_device_id TEXT,
    created_at INTEGER NOT NULL DEFAULT (unixepoch()),
    FOREIGN KEY (used_by_device_id) REFERENCES devices(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_pairing_tokens_expires_at ON pairing_tokens(expires_at);
CREATE INDEX IF NOT EXISTS idx_pairing_tokens_used_at ON pairing_tokens(used_at);

-- ============================================================================
-- 2. Sync Outbox Schema Migration Marker
-- ============================================================================
-- The reader_sync_outbox table needs to be migrated from:
--   OLD: id, entity_type, entity_id, operation, payload, created_at, processed_at, retry_count
--   NEW: id, device_id, entity_type, entity_id, operation, data, created_at, sync_state
--
-- This migration is handled by Python code in database.py because SQLite doesn't
-- support conditional DDL based on column existence checks.
--
-- The Python migration runner will:
-- 1. Check table schema using PRAGMA table_info
-- 2. If old schema (has 'payload' column): migrate data to new schema
-- 3. If new schema (has 'device_id' column): skip migration
-- 4. If table doesn't exist: create with new schema

-- ============================================================================
-- Schema Version Tracking
-- ============================================================================
INSERT OR IGNORE INTO schema_version (version, description)
VALUES (2, 'Add pairing_tokens table and migrate sync_outbox to new schema with device_id, data, and sync_state');
