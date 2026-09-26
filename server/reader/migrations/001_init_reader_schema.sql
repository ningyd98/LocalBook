-- ReadFlow Reader Data Model Schema Migration
-- Version: 001
-- Description: Initial schema for LocalBook reader infrastructure
-- All tables use UUIDv7 for primary keys (except sync_outbox)

-- Enable foreign key constraints
PRAGMA foreign_keys = ON;

-- ============================================================================
-- 1. Device Management
-- ============================================================================
CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,  -- UUIDv7
    device_name TEXT NOT NULL,
    device_type TEXT NOT NULL CHECK (device_type IN ('macos', 'ios', 'android')),
    created_at INTEGER NOT NULL DEFAULT (unixepoch()),
    last_seen INTEGER NOT NULL DEFAULT (unixepoch()),
    pairing_token_hash TEXT,
    public_key TEXT
);

CREATE INDEX idx_devices_device_type ON devices(device_type);
CREATE INDEX idx_devices_last_seen ON devices(last_seen DESC);

-- ============================================================================
-- 2. Reading Sources
-- ============================================================================
CREATE TABLE IF NOT EXISTS reader_sources (
    id TEXT PRIMARY KEY,  -- UUIDv7
    type TEXT NOT NULL CHECK (type IN ('web', 'pdf', 'document', 'markdown')),
    title TEXT,
    url TEXT,
    canonical_url TEXT,
    file_path TEXT,
    file_hash TEXT,  -- SHA-256
    created_at INTEGER NOT NULL DEFAULT (unixepoch()),
    last_read_at INTEGER,
    metadata TEXT  -- JSON string
);

CREATE INDEX idx_reader_sources_type ON reader_sources(type);
CREATE INDEX idx_reader_sources_url ON reader_sources(url);
CREATE INDEX idx_reader_sources_canonical_url ON reader_sources(canonical_url);
CREATE INDEX idx_reader_sources_file_hash ON reader_sources(file_hash);
CREATE INDEX idx_reader_sources_last_read_at ON reader_sources(last_read_at DESC);

-- ============================================================================
-- 3. Reading Sessions
-- ============================================================================
CREATE TABLE IF NOT EXISTS reader_sessions (
    id TEXT PRIMARY KEY,  -- UUIDv7
    device_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    started_at INTEGER NOT NULL DEFAULT (unixepoch()),
    ended_at INTEGER,
    duration INTEGER,  -- seconds
    highlight_count INTEGER DEFAULT 0,
    query_count INTEGER DEFAULT 0,
    note_count INTEGER DEFAULT 0,
    FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE,
    FOREIGN KEY (source_id) REFERENCES reader_sources(id) ON DELETE CASCADE
);

CREATE INDEX idx_reader_sessions_device_id ON reader_sessions(device_id);
CREATE INDEX idx_reader_sessions_source_id ON reader_sessions(source_id);
CREATE INDEX idx_reader_sessions_started_at ON reader_sessions(started_at DESC);
CREATE INDEX idx_reader_sessions_device_source ON reader_sessions(device_id, source_id);

-- ============================================================================
-- 4. Highlights
-- ============================================================================
CREATE TABLE IF NOT EXISTS reader_highlights (
    id TEXT PRIMARY KEY,  -- UUIDv7
    source_id TEXT NOT NULL,
    session_id TEXT,
    selected_text TEXT NOT NULL,
    context_before TEXT,
    context_after TEXT,
    page INTEGER,
    location TEXT,  -- JSON string for complex location data
    created_at INTEGER NOT NULL DEFAULT (unixepoch()),
    sync_state TEXT NOT NULL DEFAULT 'local_only' CHECK (sync_state IN ('local_only', 'pending', 'synced')),
    FOREIGN KEY (source_id) REFERENCES reader_sources(id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES reader_sessions(id) ON DELETE SET NULL
);

CREATE INDEX idx_reader_highlights_source_id ON reader_highlights(source_id);
CREATE INDEX idx_reader_highlights_session_id ON reader_highlights(session_id);
CREATE INDEX idx_reader_highlights_sync_state ON reader_highlights(sync_state);
CREATE INDEX idx_reader_highlights_created_at ON reader_highlights(created_at DESC);
CREATE INDEX idx_reader_highlights_source_sync ON reader_highlights(source_id, sync_state);

-- ============================================================================
-- 5. Notes
-- ============================================================================
CREATE TABLE IF NOT EXISTS reader_notes (
    id TEXT PRIMARY KEY,  -- UUIDv7
    source_id TEXT NOT NULL,
    highlight_id TEXT,
    session_id TEXT,
    content TEXT NOT NULL,
    created_at INTEGER NOT NULL DEFAULT (unixepoch()),
    updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
    revision INTEGER NOT NULL DEFAULT 1,
    sync_state TEXT NOT NULL DEFAULT 'local_only' CHECK (sync_state IN ('local_only', 'pending', 'synced')),
    FOREIGN KEY (source_id) REFERENCES reader_sources(id) ON DELETE CASCADE,
    FOREIGN KEY (highlight_id) REFERENCES reader_highlights(id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES reader_sessions(id) ON DELETE SET NULL
);

CREATE INDEX idx_reader_notes_source_id ON reader_notes(source_id);
CREATE INDEX idx_reader_notes_highlight_id ON reader_notes(highlight_id);
CREATE INDEX idx_reader_notes_session_id ON reader_notes(session_id);
CREATE INDEX idx_reader_notes_sync_state ON reader_notes(sync_state);
CREATE INDEX idx_reader_notes_updated_at ON reader_notes(updated_at DESC);
CREATE INDEX idx_reader_notes_source_sync ON reader_notes(source_id, sync_state);

-- ============================================================================
-- 6. Conversations
-- ============================================================================
CREATE TABLE IF NOT EXISTS reader_conversations (
    id TEXT PRIMARY KEY,  -- UUIDv7
    source_id TEXT,
    highlight_id TEXT,
    session_id TEXT,
    created_at INTEGER NOT NULL DEFAULT (unixepoch()),
    FOREIGN KEY (source_id) REFERENCES reader_sources(id) ON DELETE CASCADE,
    FOREIGN KEY (highlight_id) REFERENCES reader_highlights(id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES reader_sessions(id) ON DELETE SET NULL
);

CREATE INDEX idx_reader_conversations_source_id ON reader_conversations(source_id);
CREATE INDEX idx_reader_conversations_highlight_id ON reader_conversations(highlight_id);
CREATE INDEX idx_reader_conversations_session_id ON reader_conversations(session_id);
CREATE INDEX idx_reader_conversations_created_at ON reader_conversations(created_at DESC);

-- ============================================================================
-- 7. Messages
-- ============================================================================
CREATE TABLE IF NOT EXISTS reader_messages (
    id TEXT PRIMARY KEY,  -- UUIDv7
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    model TEXT,
    citations TEXT,  -- JSON string
    created_at INTEGER NOT NULL DEFAULT (unixepoch()),
    FOREIGN KEY (conversation_id) REFERENCES reader_conversations(id) ON DELETE CASCADE
);

CREATE INDEX idx_reader_messages_conversation_id ON reader_messages(conversation_id);
CREATE INDEX idx_reader_messages_created_at ON reader_messages(created_at);
CREATE INDEX idx_reader_messages_role ON reader_messages(role);

-- ============================================================================
-- 8. Sync Outbox (LocalBook side)
-- ============================================================================
CREATE TABLE IF NOT EXISTS reader_sync_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,  -- UUIDv7 of the entity
    operation TEXT NOT NULL CHECK (operation IN ('create', 'update', 'delete')),
    payload TEXT NOT NULL,  -- JSON string
    created_at INTEGER NOT NULL DEFAULT (unixepoch()),
    processed_at INTEGER,
    retry_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_reader_sync_outbox_entity ON reader_sync_outbox(entity_type, entity_id);
CREATE INDEX idx_reader_sync_outbox_created_at ON reader_sync_outbox(created_at);
CREATE INDEX idx_reader_sync_outbox_processed_at ON reader_sync_outbox(processed_at);
CREATE INDEX idx_reader_sync_outbox_unprocessed ON reader_sync_outbox(processed_at) WHERE processed_at IS NULL;

-- ============================================================================
-- Schema Version Tracking
-- ============================================================================
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at INTEGER NOT NULL DEFAULT (unixepoch()),
    description TEXT
);

INSERT INTO schema_version (version, description)
VALUES (1, 'Initial reader schema with devices, sources, sessions, highlights, notes, conversations, messages, and sync outbox');
