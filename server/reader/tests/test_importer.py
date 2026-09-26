"""Tests for Reader Connector importer."""

import json
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from server.reader.database import ReaderDatabase
from server.reader.importer import ReaderImporter
from server.vault.service import VaultService


@pytest.fixture
def temp_vault():
    """Create a temporary vault for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault_path = Path(tmpdir) / "vault"
        vault_path.mkdir()

        vault = VaultService(
            root=vault_path,
            max_file_bytes=10 * 1024 * 1024,  # 10MB
            watcher_enabled=False,
        )
        vault.initialize(start_watcher=False)

        yield vault


@pytest.fixture
def test_db():
    """Create a temporary test database."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    # ReaderDatabase.__init__ automatically applies migrations
    db = ReaderDatabase(db_path)

    yield db

    # Cleanup
    Path(db_path).unlink(missing_ok=True)


@pytest.fixture
def importer(test_db, temp_vault):
    """Create ReaderImporter instance."""
    return ReaderImporter(test_db, temp_vault)


@pytest.fixture
def sample_session(test_db):
    """Create a sample session with highlights, notes, and conversations."""
    # Create device
    device_id = "device_test_001"
    conn = test_db._get_connection()
    conn.execute(
        """
        INSERT INTO devices (id, device_name, device_type)
        VALUES (?, ?, ?)
        """,
        (device_id, "Test iPhone", "ios"),
    )
    conn.commit()

    # Create source
    source_id = "source_test_001"
    conn.execute(
        """
        INSERT INTO reader_sources (id, type, title, url, canonical_url, metadata)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            source_id,
            "web",
            "The Art of Readable Code",
            "https://example.com/readable-code",
            "https://example.com/readable-code",
            json.dumps({"author": "Dustin Boswell"}),
        ),
    )
    conn.commit()

    # Create session
    session_id = "session_test_001"
    started_at = int(datetime(2024, 3, 15, 10, 30).timestamp())
    ended_at = int(datetime(2024, 3, 15, 11, 45).timestamp())
    duration = ended_at - started_at  # Duration in seconds

    conn.execute(
        """
        INSERT INTO reader_sessions (
            id, device_id, source_id, started_at, ended_at, duration,
            highlight_count, query_count, note_count
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            device_id,
            source_id,
            started_at,
            ended_at,
            duration,
            2,  # Will add 2 highlights
            0,  # No queries
            2,  # Will add 2 notes
        ),
    )

    # Create highlights
    conn.execute(
        """
        INSERT INTO reader_highlights (
            id, source_id, session_id, selected_text, context_before, context_after
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "highlight_001",
            source_id,
            session_id,
            "Code should be written to minimize the time it would take for "
            "someone else to understand it.",
            "The most important principle is:",
            "This is the guiding principle throughout the book.",
        ),
    )

    conn.execute(
        """
        INSERT INTO reader_highlights (id, source_id, session_id, selected_text)
        VALUES (?, ?, ?, ?)
        """,
        (
            "highlight_002",
            source_id,
            session_id,
            "Shorter code is NOT always better.",
        ),
    )

    # Create notes
    conn.execute(
        """
        INSERT INTO reader_notes (id, source_id, session_id, content)
        VALUES (?, ?, ?, ?)
        """,
        (
            "note_001",
            source_id,
            session_id,
            "Remember to apply this principle in code reviews",
        ),
    )

    conn.execute(
        """
        INSERT INTO reader_notes (id, source_id, session_id, content)
        VALUES (?, ?, ?, ?)
        """,
        (
            "note_002",
            source_id,
            session_id,
            "Check out the examples in Chapter 3",
        ),
    )

    # Create conversation
    conv_id = "conv_001"
    conn.execute(
        """
        INSERT INTO reader_conversations (id, source_id, session_id, device_id)
        VALUES (?, ?, ?, ?)
        """,
        (conv_id, source_id, session_id, device_id),
    )

    # Add messages
    conn.execute(
        """
        INSERT INTO reader_messages (id, conversation_id, role, content)
        VALUES (?, ?, ?, ?)
        """,
        (
            "msg_001",
            conv_id,
            "user",
            "What are the key principles from this book?",
        ),
    )

    conn.execute(
        """
        INSERT INTO reader_messages (id, conversation_id, role, content, model)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            "msg_002",
            conv_id,
            "assistant",
            "The key principle is writing code that minimizes understanding time. "
            "This includes using clear names, simplifying control flow, and "
            "making code obvious.",
            "gpt-4",
        ),
    )

    conn.commit()
    conn.close()

    return session_id


def test_import_session_creates_markdown(importer, sample_session, temp_vault):
    """Test that import_session creates a valid Markdown document."""
    result = importer.import_session(sample_session)

    # Check result structure
    assert "path" in result
    assert "sha256" in result
    assert "byte_length" in result
    assert result["operation"] == "created"

    # Check path format: Reading/YYYY/MM/title.md
    assert result["path"].startswith("Reading/2024/03/")
    assert result["path"].endswith(".md")

    # Read the created file
    content_bytes = temp_vault.read_file(result["path"])["content"]
    content = content_bytes.decode("utf-8")

    # Check frontmatter
    assert "---" in content
    assert "session_id: session_test_001" in content
    assert "connector_type: reader" in content
    assert "device: Test iPhone" in content
    assert "device_type: ios" in content
    assert "url: https://example.com/readable-code" in content
    assert "started_at:" in content
    assert "ended_at:" in content

    # Check title
    assert "# The Art of Readable Code" in content

    # Check highlights
    assert "## Highlights" in content
    assert "Code should be written to minimize the time" in content
    assert "Shorter code is NOT always better" in content
    assert "**Context:**" in content

    # Check notes
    assert "## Notes" in content
    assert "Remember to apply this principle in code reviews" in content
    assert "Check out the examples in Chapter 3" in content

    # Check conversations
    assert "## AI Conversations" in content
    assert "**Q:** What are the key principles from this book?" in content
    assert "**A:** The key principle is writing code" in content
    assert "*(gpt-4)*" in content

    # Check session info
    assert "## Session Info" in content
    assert "Session ID:" in content


def test_import_session_not_found(importer):
    """Test that importing non-existent session raises error."""
    from server.reader.errors import ReaderError, ReaderErrorCode

    with pytest.raises(ReaderError) as exc_info:
        importer.import_session("nonexistent_session")

    assert exc_info.value.code == ReaderErrorCode.SESSION_NOT_FOUND


def test_sanitize_filename(importer):
    """Test filename sanitization."""
    # Normal title
    assert importer._sanitize_filename("Hello World") == "Hello World"

    # Unsafe characters
    assert importer._sanitize_filename("Hello/World:Test") == "HelloWorldTest"

    # Multiple spaces
    assert importer._sanitize_filename("Hello    World") == "Hello World"

    # Empty after sanitization
    assert importer._sanitize_filename("///:::") == "Untitled"

    # Long title
    long_title = "A" * 300
    result = importer._sanitize_filename(long_title)
    assert len(result) == 200


def test_generate_path(importer):
    """Test path generation."""
    session = {
        "started_at": int(datetime(2024, 3, 15, 10, 30).timestamp()),
        "source_title": "Test Article",
    }

    path = importer._generate_path(session)
    assert path == "Reading/2024/03/Test Article.md"


def test_generate_path_fallback_to_current_time(importer):
    """Test path generation falls back to current time if started_at is missing."""
    session = {
        "source_title": "Test Article",
    }

    path = importer._generate_path(session)
    now = datetime.now()
    expected_prefix = f"Reading/{now.year:04d}/{now.month:02d}/"

    assert path.startswith(expected_prefix)
    assert path.endswith(".md")


def test_ensure_directory_creates_nested_paths(importer, temp_vault):
    """Test that ensure_directory creates nested directories."""
    file_path = "Reading/2024/03/test.md"
    importer._ensure_directory(file_path)

    # Verify directories exist
    entries = temp_vault.list_tree(".", recursive=False)
    assert any(e.path == "Reading" and e.kind == "directory" for e in entries)


def test_import_session_without_highlights_notes_conversations(test_db, temp_vault):
    """Test importing a minimal session with no highlights, notes, or conversations."""
    importer = ReaderImporter(test_db, temp_vault)

    # Create minimal session
    device_id = "device_min_001"
    conn = test_db._get_connection()
    conn.execute(
        """
        INSERT INTO devices (id, device_name, device_type)
        VALUES (?, ?, ?)
        """,
        (device_id, "Minimal Device", "macos"),
    )

    source_id = "source_min_001"
    conn.execute(
        """
        INSERT INTO reader_sources (id, type, title, url)
        VALUES (?, ?, ?, ?)
        """,
        (source_id, "web", "Minimal Article", "https://example.com/minimal"),
    )

    session_id = "session_min_001"
    started_at = int(datetime.now().timestamp())
    conn.execute(
        """
        INSERT INTO reader_sessions (id, device_id, source_id, started_at, duration)
        VALUES (?, ?, ?, ?, ?)
        """,
        (session_id, device_id, source_id, started_at, 60),
    )
    conn.commit()
    conn.close()

    # Import
    result = importer.import_session(session_id)

    # Verify document was created
    assert result["operation"] == "created"

    # Read content
    content = temp_vault.read_file(result["path"])["content"].decode("utf-8")

    # Check basic structure
    assert "# Minimal Article" in content
    assert "connector_type: reader" in content
    assert "## Session Info" in content

    # Should not have empty sections
    assert "## Highlights" not in content
    assert "## Notes" not in content
    assert "## AI Conversations" not in content


def test_markdown_escaping(importer, test_db, temp_vault):
    """Test that special Markdown characters in content are preserved."""
    # Create session with special characters
    device_id = "device_escape_001"
    conn = test_db._get_connection()
    conn.execute(
        """
        INSERT INTO devices (id, device_name, device_type)
        VALUES (?, ?, ?)
        """,
        (device_id, "Test Device", "ios"),
    )

    source_id = "source_escape_001"
    conn.execute(
        """
        INSERT INTO reader_sources (id, type, title, url)
        VALUES (?, ?, ?, ?)
        """,
        (source_id, "web", "Article with # and * chars", "https://example.com/test"),
    )

    session_id = "session_escape_001"
    started_at = int(datetime.now().timestamp())
    conn.execute(
        """
        INSERT INTO reader_sessions (id, device_id, source_id, started_at, duration)
        VALUES (?, ?, ?, ?, ?)
        """,
        (session_id, device_id, source_id, started_at, 60),
    )

    # Add highlight with special chars
    conn.execute(
        """
        INSERT INTO reader_highlights (id, source_id, session_id, selected_text)
        VALUES (?, ?, ?, ?)
        """,
        (
            "highlight_escape_001",
            source_id,
            session_id,
            "Use * for emphasis and # for headers",
        ),
    )

    conn.commit()
    conn.close()

    # Import
    result = importer.import_session(session_id)
    content = temp_vault.read_file(result["path"])["content"].decode("utf-8")

    # Characters should be preserved in content
    assert "Use * for emphasis and # for headers" in content
