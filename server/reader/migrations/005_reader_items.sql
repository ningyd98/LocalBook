-- ReadFlow Reader Data Model Schema Migration
-- Version: 005
-- Description: Persist generic ReaderItem records emitted by the macOS client.

CREATE TABLE IF NOT EXISTS reader_items (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('source', 'highlight', 'note', 'question', 'answer')),
    source_id TEXT,
    session_id TEXT,
    parent_id TEXT,
    content TEXT NOT NULL,
    metadata TEXT,
    created_at INTEGER NOT NULL DEFAULT (unixepoch()),
    updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
    sync_state TEXT NOT NULL DEFAULT 'pending'
        CHECK (sync_state IN ('local_only', 'pending', 'synced', 'conflict', 'failed')),
    FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_reader_items_device_id ON reader_items(device_id);
CREATE INDEX IF NOT EXISTS idx_reader_items_source_id ON reader_items(source_id);
CREATE INDEX IF NOT EXISTS idx_reader_items_updated_at ON reader_items(updated_at DESC);
