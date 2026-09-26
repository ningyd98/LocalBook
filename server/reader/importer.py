"""Reader Connector: Import Reader sessions into LocalBook as Markdown documents.

Converts Reader sessions (highlights, notes, AI conversations) into structured
Markdown documents and integrates them with the LocalBook vault, triggering FTS5
and vector indexing.

Architecture:
- ReaderImporter: Main class handling session → Markdown conversion
- Path organization: LocalBook/Reading/YYYY/MM/title.md
- Metadata: frontmatter with device, application, url, session_id, reading_time
- Integration: Uses VaultService for atomic writes with conflict detection
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from server.reader.database import ReaderDatabase
from server.vault.service import VaultService


class ReaderImporter:
    """Import Reader sessions as Markdown documents into LocalBook vault."""

    def __init__(self, database: ReaderDatabase, vault: VaultService):
        """Initialize importer with database and vault service.

        Args:
            database: ReaderDatabase instance for querying session data
            vault: VaultService instance for writing documents
        """
        self.database = database
        self.vault = vault

    def import_session(self, session_id: str, device_id: str | None = None) -> dict[str, Any]:
        """Import a single session as a Markdown document.

        Args:
            session_id: UUID of the session to import

        Returns:
            dict with keys:
                - path: relative path of created document
                - sha256: content hash
                - byte_length: document size
                - operation: "created"

        Raises:
            ReaderError: if session not found
            VaultError: if document creation fails
        """
        # Fetch session with all related data
        data = self.database.get_session_with_data(session_id, device_id=device_id)
        if not data:
            from server.reader.errors import ReaderError, ReaderErrorCode

            raise ReaderError(
                ReaderErrorCode.SESSION_NOT_FOUND,
                f"Session {session_id} not found",
            )

        session = data["session"]
        highlights = data["highlights"]
        notes = data["notes"]
        conversations = data["conversations"]

        # Generate Markdown content
        content = self._generate_markdown(session, highlights, notes, conversations)

        # Determine target path: Reading/YYYY/MM/title.md
        target_path = self._generate_path(session)

        # Ensure parent directory exists
        self._ensure_directory(target_path)

        # Write document atomically via VaultService
        result = self.vault.create_bytes(target_path, content.encode("utf-8"))

        return result

    def _generate_path(self, session: dict[str, Any]) -> str:
        """Generate the target path for a session document.

        Format: Reading/YYYY/MM/title.md

        Args:
            session: session data dict

        Returns:
            relative path string
        """
        # Parse session start time
        started_at = session.get("started_at")
        if started_at:
            # Unix timestamp in seconds (epoch)
            dt = datetime.fromtimestamp(started_at)
            year = dt.strftime("%Y")
            month = dt.strftime("%m")
        else:
            # Fallback to current time
            now = datetime.now()
            year = now.strftime("%Y")
            month = now.strftime("%m")

        # Generate safe filename from source title
        title = session.get("source_title", "Untitled")
        safe_title = self._sanitize_filename(title)

        return f"Reading/{year}/{month}/{safe_title}.md"

    def _sanitize_filename(self, title: str) -> str:
        """Convert title to a safe filename.

        Args:
            title: raw title string

        Returns:
            sanitized filename (without extension)
        """
        # Remove or replace unsafe characters
        safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", title)
        # Collapse whitespace
        safe = re.sub(r"\s+", " ", safe).strip()
        # Limit length (leave room for extension)
        if len(safe) > 200:
            safe = safe[:200].rstrip()
        # Fallback if empty
        if not safe:
            safe = "Untitled"
        return safe

    def _ensure_directory(self, file_path: str) -> None:
        """Ensure parent directories exist for the target file.

        Args:
            file_path: relative file path
        """
        parent = str(Path(file_path).parent)
        if parent and parent != ".":
            # Create intermediate directories if needed
            parts = parent.split("/")
            current = ""
            for part in parts:
                if current:
                    current = f"{current}/{part}"
                else:
                    current = part

                try:
                    self.vault.create_directory(current)
                except Exception:
                    # Directory may already exist - ignore
                    pass

    def _generate_markdown(
        self,
        session: dict[str, Any],
        highlights: list[dict[str, Any]],
        notes: list[dict[str, Any]],
        conversations: list[dict[str, Any]],
    ) -> str:
        """Generate Markdown content from session data.

        Args:
            session: session metadata
            highlights: list of highlight dicts
            notes: list of note dicts
            conversations: list of conversation dicts with messages

        Returns:
            complete Markdown document as string
        """
        lines = []

        # === Frontmatter ===
        lines.append("---")

        # Required metadata
        lines.append(f"session_id: {session['id']}")
        lines.append("connector_type: reader")

        # Device info
        if session.get("device_name"):
            lines.append(f"device: {session['device_name']}")
        if session.get("device_type"):
            lines.append(f"device_type: {session['device_type']}")
        if session.get("device_application"):
            lines.append(f"application: {session['device_application']}")

        # Source info
        if session.get("source_url"):
            lines.append(f"url: {session['source_url']}")
        if session.get("source_canonical_url"):
            lines.append(f"canonical_url: {session['source_canonical_url']}")
        if session.get("source_type"):
            lines.append(f"source_type: {session['source_type']}")

        # Timing
        if session.get("started_at"):
            dt = datetime.fromtimestamp(session["started_at"])
            lines.append(f"started_at: {dt.isoformat()}")
        if session.get("ended_at"):
            dt = datetime.fromtimestamp(session["ended_at"])
            lines.append(f"ended_at: {dt.isoformat()}")
        if session.get("duration"):
            minutes = session["duration"] / 60
            lines.append(f"reading_time: {minutes:.1f} min")

        # Scroll depth
        if session.get("scroll_depth") is not None:
            lines.append(f"scroll_depth: {session['scroll_depth']:.1%}")

        lines.append("---")
        lines.append("")

        # === Title ===
        title = session.get("source_title", "Untitled")
        lines.append(f"# {title}")
        lines.append("")

        # === Source link ===
        if session.get("source_url"):
            lines.append(f"**Source:** {session['source_url']}")
            lines.append("")

        # === Highlights ===
        if highlights:
            lines.append("## Highlights")
            lines.append("")
            for i, highlight in enumerate(highlights, 1):
                text = highlight.get("selected_text", "").strip()
                if text:
                    lines.append(f"### {i}. {text}")
                    lines.append("")

                    # Context
                    context_before = (highlight.get("context_before") or "").strip()
                    context_after = (highlight.get("context_after") or "").strip()
                    if context_before or context_after:
                        lines.append("**Context:**")
                        if context_before:
                            lines.append(f"> ...{context_before}")
                        lines.append(f"> **{text}**")
                        if context_after:
                            lines.append(f"> {context_after}...")
                        lines.append("")

                    # Position metadata
                    if highlight.get("page") is not None:
                        lines.append(f"*Page: {highlight['page']}*")
                        lines.append("")
            lines.append("")

        # === Notes ===
        if notes:
            lines.append("## Notes")
            lines.append("")
            for note in notes:
                content = note.get("content", "").strip()
                if content:
                    lines.append(f"- {content}")
            lines.append("")

        # === AI Conversations ===
        if conversations:
            lines.append("## AI Conversations")
            lines.append("")

            for i, conv in enumerate(conversations, 1):
                messages = conv.get("messages", [])
                if messages:
                    lines.append(f"### Conversation {i}")
                    lines.append("")

                    for msg in messages:
                        role = msg.get("role", "user")
                        content = msg.get("content", "").strip()
                        model = msg.get("model")

                        if role == "user":
                            lines.append(f"**Q:** {content}")
                        elif role == "assistant":
                            lines.append(f"**A:** {content}")
                            if model:
                                lines.append(f"*({model})*")
                        lines.append("")

                    lines.append("---")
                    lines.append("")

        # === Session Metadata ===
        lines.append("## Session Info")
        lines.append("")
        lines.append(f"- **Session ID:** `{session['id']}`")
        if session.get("device_name"):
            lines.append(
                f"- **Device:** {session['device_name']} ({session.get('device_type', 'unknown')})"
            )
        if session.get("device_application"):
            lines.append(f"- **Application:** {session['device_application']}")
        if session.get("duration"):
            duration_min = session["duration"] / 60
            lines.append(f"- **Reading Time:** {duration_min:.1f} minutes")
        if session.get("scroll_depth") is not None:
            lines.append(f"- **Scroll Depth:** {session['scroll_depth']:.1%}")
        lines.append("")

        return "\n".join(lines)
