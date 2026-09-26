# ReadFlow Reader Database Migrations

This directory contains SQLite schema migrations for the ReadFlow LocalBook reader infrastructure.

## Migration Files

### 001_init_reader_schema.sql
Initial schema creation for the reader system, including:

- **devices**: Device management and pairing
- **reader_sources**: Reading material sources (web, PDF, documents, markdown)
- **reader_sessions**: Reading sessions with duration and activity metrics
- **reader_highlights**: Text highlights with context and location
- **reader_notes**: User notes linked to highlights or sources
- **reader_conversations**: AI conversation contexts
- **reader_messages**: Conversation messages with role and citations
- **reader_sync_outbox**: Outbound sync queue for LocalBook → server sync

### 004_reader_access_tokens.sql
Stores a SHA-256 hash for every issued Reader bearer token. The raw token is
returned once during registration and is never written to SQLite; subsequent
requests are accepted only when the presented token hash matches the registered
device row.

### 005_reader_items.sql
Adds the generic `reader_items` table used by the macOS client for question,
answer, and other Reader records that do not fit the source/highlight/note
normalized tables.

### 006_reader_pairing_approval.sql
Requires approval from the trusted LocalBook settings page before a new Reader
device can exchange a pairing code for an access token.

### 007_reader_conversation_owner.sql
Records the owning device for AI conversations. Legacy conversations attached
to a session inherit that session's device; source-only legacy conversations
remain stored but cannot be reopened without an owner.

## Schema Design Decisions

### Primary Keys
- All tables use **UUIDv7** as primary keys (stored as TEXT in SQLite)
- Exception: `reader_sync_outbox` uses INTEGER AUTOINCREMENT for queue ordering

### Timestamps
- All timestamps use **Unix epoch** (INTEGER type) for efficiency and simplicity
- Default to `unixepoch()` for automatic timestamp generation

### JSON Fields
- JSON data is stored as TEXT (SQLite's native JSON support is available via JSON functions)
- Fields: `metadata`, `location`, `citations`, `payload`

### Indexes
Covering common query paths:
- `device_id` - device-scoped queries
- `source_id` - source-scoped queries
- `session_id` - session-scoped queries
- `sync_state` - sync status filtering
- Composite indexes for common join patterns

### Foreign Keys
- `PRAGMA foreign_keys = ON` enforced
- Cascade deletes for child records
- `SET NULL` for optional references (sessions)

### Sync State
Three states for sync-enabled entities:
- `local_only` - not yet queued for sync
- `pending` - queued in outbox
- `synced` - confirmed by server

## Usage

### Apply Migration
```bash
sqlite3 localbook.db < server/reader/migrations/001_init_reader_schema.sql
```

### Verify Schema
```bash
sqlite3 localbook.db ".schema devices"
sqlite3 localbook.db "SELECT * FROM schema_version;"
```

### Generate UUIDv7 (Python)
```python
import uuid
from datetime import datetime


def generate_uuidv7():
    """Generate UUIDv7 with timestamp prefix"""
    timestamp_ms = int(datetime.utcnow().timestamp() * 1000)
    # UUIDv7 format: timestamp(48) + version(4) + random(12) + variant(2) + random(62)
    return str(uuid.uuid7())  # Python 3.11+ or use external library
```

## Migration Strategy

1. **Version Tracking**: `schema_version` table tracks applied migrations
2. **Idempotent**: `CREATE TABLE IF NOT EXISTS` allows safe re-runs
3. **Forward-Only**: No down migrations (backup database before applying)
4. **Testing**: Test migrations on copy of production database first

## Next Steps

- Implement Python ORM models (`server/reader/models.py`)
- Create migration runner script
- Add indexes for full-text search if needed
- Consider WAL mode for better concurrency
