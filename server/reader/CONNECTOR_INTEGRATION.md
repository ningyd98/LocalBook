# Reader Connector Integration

This document describes the Reader Connector integration that converts Reader sessions into LocalBook Markdown documents.

## Overview

The Reader Connector (`ReaderImporter`) transforms reading sessions captured by ReadFlow clients (macOS, iOS, Android) into structured Markdown documents within the LocalBook vault, making them searchable and discoverable alongside other notes.

## Architecture

### Components

1. **ReaderImporter** (`server/reader/importer.py`)
   - Core conversion logic
   - Generates Markdown from session data
   - Integrates with VaultService for document storage

2. **ReaderService** (`server/reader/service.py`)
   - Business logic layer
   - Exposes `import_session()` method
   - Coordinates between database and importer

3. **Reader API** (`server/reader/router.py`)
   - REST endpoint: `POST /api/v1/reader/sessions/{session_id}/import`
   - Requires JWT authentication
   - Returns document metadata

### Data Flow

```
Reader Client (macOS/iOS)
    ↓ sync
Reader Database (SQLite)
    ↓ import_session()
ReaderImporter
    ↓ write_document()
VaultService
    ↓ auto-index
FTS5 + Vector Index
```

## Document Format

### File Organization

Documents are organized by reading time:

```
LocalBook/
  Reading/
    2024/
      01/
        Article Title.md
        Another Article.md
      02/
        Research Paper.md
      03/
        Blog Post.md
```

### Markdown Structure

```markdown
---
session_id: uuid
connector_type: reader
device: Device Name (macos)
device_type: macos
application: Safari
url: https://example.com/article
author: Author Name
reading_time: 45 minutes
scroll_depth: 85
created_at: 2024-03-15T10:30:00Z
---

# Article Title

> Source: [https://example.com/article](https://example.com/article)
> Author: Author Name
> Read on: 2024-03-15 10:30 (45 minutes, 85% depth)

## Highlights

### Highlight 1

Important insight here

**Context:** Some context ... more text

---

### Highlight 2

Another key point

---

## Notes

### Note 1

This is my thought about the highlight

---

### Note 2

Additional observations

---

## AI Conversations

### Conversation 1

**Q:** Can you explain this concept?

**A:** Here's the explanation... *(gpt-4)*

---

## Session Info

- **Session ID:** uuid
- **Device:** Device Name (macos)
- **Application:** Safari
- **Duration:** 45 minutes
- **Scroll Depth:** 85%
```

## Metadata

Each imported document includes metadata for:

1. **Connector tracking**: `connector_type: reader`
2. **Source attribution**: device, application, URL, author
3. **Reading context**: session_id, reading_time, scroll_depth
4. **Timestamps**: created_at (reading start time)

## Indexing

Imported documents are automatically indexed:

1. **FTS5 Full-Text Search**: Title, highlights, notes, conversations
2. **Vector Search**: Semantic search across content
3. **Metadata Search**: Filter by device, application, URL, date

## API Usage

### Import a Session

```bash
curl -X POST "http://localhost:3780/api/v1/reader/sessions/{session_id}/import" \
  -H "Authorization: Bearer reader_token_{device_id}_{random}"
```

**Response:**

```json
{
  "path": "Reading/2024/03/Article Title.md",
  "sha256": "abc123...",
  "byte_length": 2048,
  "operation": "created"
}
```

### Error Responses

- **401 Unauthorized**: Invalid or missing JWT token
- **404 Not Found**: Session does not exist
- **503 Service Unavailable**: Vault service not configured

## Implementation Details

### Filename Sanitization

Unsafe characters in titles are removed or replaced:

- Path separators (`/`, `\`) → removed
- Special chars (`:`, `*`, `?`, `"`, `<`, `>`, `|`) → removed
- Multiple spaces → single space
- Max length: 200 characters
- Fallback: "Untitled" if empty after sanitization

### Time Formatting

Reading durations are formatted human-readably:

- < 60s: "X seconds"
- < 3600s: "X.X minutes"
- ≥ 3600s: "X.X hours"

### Scroll Depth

Displayed as percentage (0-100%) indicating how much of the document was scrolled.

### Directory Creation

Parent directories are created automatically:

```python
importer._ensure_directory("Reading/2024/03/article.md")
# Creates: Reading/ → Reading/2024/ → Reading/2024/03/
```

## Testing

Comprehensive test suite in `server/reader/tests/test_importer.py`:

1. ✅ Import session with highlights, notes, conversations
2. ✅ Import minimal session (no highlights/notes)
3. ✅ Filename sanitization
4. ✅ Path generation with year/month
5. ✅ Directory creation
6. ✅ Markdown special character preservation
7. ✅ Error handling (session not found)
8. ✅ Vault service requirement

Run tests:

```bash
cd /path/to/LocalBook
pytest server/reader/tests/test_importer.py -v
```

## Future Enhancements

1. **Incremental Updates**: Re-import updated sessions without duplication
2. **Batch Import**: Import multiple sessions in one operation
3. **Export Options**: PDF, EPUB, or other formats
4. **Tag Extraction**: Auto-tag based on content or highlights
5. **Link Detection**: Cross-reference with existing notes
6. **Reading Statistics**: Aggregate reading analytics

## Dependencies

- `server.vault.service.VaultService`: Document storage
- `server.reader.database.ReaderDatabase`: Session data access
- `server.reader.errors.ReaderError`: Error handling

## Configuration

No additional configuration required. The importer uses:

- Vault root from `LOCALNOTE_VAULT__ROOT`
- Document stored under `Reading/` subdirectory
- UTF-8 encoding for all documents

## References

- Task: t5 P1.4 Reader Connector 集成
- Plan: `docs/readflow-development-plan.md` lines 240-294
- Schema: `server/reader/migrations/001_init_reader_schema.sql`
- API Docs: `docs/api-reference.md` (Reader API section)
