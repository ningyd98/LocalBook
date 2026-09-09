"""M4 SQLite-backed, rebuildable, thread-safe derived index (PLAN-M4 §5.3–§5.6).

The index is a pure *reading* derivation over the Vault: it consumes
``VaultService.list_tree``/``read_bytes`` exclusively (never ``open``/
``pathlib`` directly) and writes only derived rows into
``.localnote/index.db`` (SQLite: ``notes``/``tags``/``properties``/``links``/
``backlinks``/``notes_fts``).  The authoritative note bytes always stay in the
Vault files; deleting ``.localnote`` and rebuilding reproduces the same rows
and never touches a note's bytes.

Public behaviour is identical to the M3 in-memory index (PLAN-M3 §5.3):
- ``rebuild()`` clears and rescans the whole Vault inside one transaction
  (atomic: a failure rolls back and leaves no half state);
- ``handle_event()`` applies watcher create/modify/delete/move events as
  single-note SQLite upserts/deletes, serialised with ``rebuild()`` and every
  query under one ``threading.RLock`` (PLAN-M4 §5.6 — rebuild blocks queries
  rather than serving half-built rows; acceptable for a local single-user
  Vault, documented trade-off);
- a single note that cannot be decoded/read/parsed never fails the index; it
  becomes a per-note diagnostic row counted in ``failed``/``skipped``;
- a whole-Vault scan failure marks the index ``unavailable`` so
  metadata/links/search return 503 while Vault I/O, health, AI and the M2
  editor keep working;
- ``entry``/``entries``/``outgoing_for``/``backlink_sources``/``tags_for`` are
  now SQLite reads; their signatures and return types are unchanged so the
  Links/Metadata/Search services needed no contract change.

M4 additions for SearchService: ``fts_search`` (FTS5 MATCH) and
``substring_search`` (M3 degraded path) return :class:`SearchCandidate` rows.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import sqlite3
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..markdown.frontmatter import (
    basename_no_extension,
    derive_title,
    parse_frontmatter,
    strip_frontmatter,
)
from ..markdown.wikilinks import WikilinkRef, parse_wikilinks
from ..vault.derived import derived_db_path
from ..vault.errors import PathNotFound, VaultError
from ..vault.events import VaultEvent
from ..vault.service import VaultService
from .db import DatabaseUnavailable, IndexDatabase, IndexDatabaseError
from .errors import IndexUnavailable
from .schemas import (
    GraphLinkRow,
    GraphNoteRow,
    GraphSnapshot,
    GraphTagRow,
    GraphTotals,
    IndexRebuildResponse,
    NoteIndexEntry,
    SearchCandidate,
)

logger = logging.getLogger("localnote.index")

_MARKDOWN_SUFFIXES = (".md", ".markdown")
_CONTEXT_CAP = 200

# Column weights for FTS5 bm25 (columns: path UNINDEXED, title, basename,
# tags, body).  Higher weight = more important column; title/basename/tags
# outrank plain body text (PLAN-M4 §5.4).
_FTS_WEIGHTS = "bm25(notes_fts, 0.0, 5.0, 4.0, 3.0, 1.0)"

_INSERT_NOTE_SQL = (
    "INSERT INTO notes (path, sha256, title, basename, text, text_truncated, "
    "frontmatter_status, diagnostic, parse_error_json, search_folded) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_SELECT_SHA_SQL = "SELECT sha256 FROM notes WHERE path = ?"
_SELECT_ROWID_SQL = "SELECT rowid FROM notes WHERE path = ?"

_FTS_INSERT_SQL = (
    "INSERT INTO notes_fts (rowid, path, title, basename, tags, body) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)
_FTS_DELETE_SQL = "DELETE FROM notes_fts WHERE rowid = ?"

_BACKLINK_MATERIALIZE_SQL = (
    "INSERT OR IGNORE INTO backlinks (target_path, source_path, context) "
    "SELECT l.resolved_path, l.source_path, l.context FROM links l "
    "WHERE l.resolved_path IS NOT NULL AND l.resolved_path <> l.source_path"
)


def _strip_extension(name: str) -> str:
    if "." in name:
        stem, _, extension = name.rpartition(".")
        if stem and extension:
            return stem
    return name


def _line_context(text: str, position: int) -> str | None:
    line_start = text.rfind("\n", 0, position) + 1
    line_end = text.find("\n", position)
    if line_end < 0:
        line_end = len(text)
    line = text[line_start:line_end].strip()
    if len(line) > _CONTEXT_CAP:
        line = line[:_CONTEXT_CAP].rstrip() + "…"
    return line or None


def _markdown_file(path: str) -> bool:
    return path.casefold().endswith(_MARKDOWN_SUFFIXES)


def _dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class DerivedIndexService:
    """SQLite-backed note/tag/link/backlink index over one Vault."""

    def __init__(
        self,
        vault: VaultService,
        *,
        note_text_cap: int = 1_000_000,
        db_filename: str = "index.db",
        fts_tokenizer: str = "unicode61",
        journal_mode: str = "WAL",
        synchronous: str = "NORMAL",
        busy_timeout_ms: int = 5000,
    ) -> None:
        self._vault = vault
        self._note_text_cap = max(1, int(note_text_cap))
        self._lock = threading.RLock()
        self._db: IndexDatabase | None = None
        self._fts_available = False
        try:
            root = Path(vault.root)
        except Exception:  # pragma: no cover - VaultService always has root
            root = None  # type: ignore[assignment]
        self._db_path = (
            derived_db_path(root, db_filename) if root is not None else None
        )
        self._db_kwargs = {
            "journal_mode": journal_mode,
            "synchronous": synchronous,
            "busy_timeout_ms": busy_timeout_ms,
            "fts_tokenizer": fts_tokenizer,
        }
        # basename maps are index-time resolution data (all files, incl.
        # attachments): they are rebuilt from list_tree and updated by events,
        # mirroring M3.  They are never the store of note rows.
        self._basenames_full: dict[str, list[str]] = {}
        self._basenames_noext: dict[str, list[str]] = {}
        self._build_state = "idle"  # "idle" | "ready" | "unavailable"
        self._build_error: str | None = None
        self._last_skipped = 0
        self._last_failed = 0
        self._last_built_at: datetime | None = None
        # P1-3: Event buffer to merge consecutive events on the same path
        self._pending_events: dict[str, tuple[VaultEvent, float]] = {}
        self._event_timers: dict[str, threading.Timer] = {}
        self._open_database()

    # ------------------------------------------------------------------
    # Database lifecycle
    # ------------------------------------------------------------------

    def _open_database(self) -> bool:
        """Open (create + migrate) ``.localnote/index.db``.

        A failure only marks the index unavailable; it never raises into the
        caller (lifecycle/startup must not crash on a missing derived dir).
        """
        if self._db is not None:
            return True
        if self._db_path is None:
            self._set_unavailable("Derived index database path is unavailable")
            return False
        db = IndexDatabase(self._db_path, **self._db_kwargs)
        try:
            db.open()
        except (DatabaseUnavailable, IndexDatabaseError, sqlite3.Error) as exc:
            logger.warning("derived index db open failed: %s", exc)
            db.close()
            self._set_unavailable("Derived index database is unavailable")
            return False
        self._db = db
        self._fts_available = db.fts_available
        if self._build_state == "unavailable" and self._build_error:
            # Recovered from an earlier open failure; reset for a retry.
            self._build_state = "idle"
            self._build_error = None
        return True

    def _set_unavailable(self, message: str) -> None:
        self._build_state = "unavailable"
        self._build_error = message
        self._last_built_at = None

    @property
    def fts_available(self) -> bool:
        with self._lock:
            return self._fts_available and self._db is not None

    @property
    def db_path(self) -> Path | None:
        return self._db_path

    def close(self) -> None:
        """Close the SQLite connection (lifecycle shutdown)."""
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None
            self._fts_available = False

    # ------------------------------------------------------------------
    # State accessors (used by domain services under the same lock)
    # ------------------------------------------------------------------

    @property
    def build_state(self) -> str:
        with self._lock:
            return self._build_state

    @property
    def build_error(self) -> str | None:
        with self._lock:
            return self._build_error

    @property
    def last_built_at(self) -> datetime | None:
        with self._lock:
            return self._last_built_at

    @property
    def skipped_count(self) -> int:
        with self._lock:
            return self._last_skipped

    @property
    def failed_count(self) -> int:
        with self._lock:
            return self._last_failed

    def assert_ready(self) -> None:
        """Raise ``IndexUnavailable`` unless the index is queryable."""
        with self._lock:
            if self._build_state != "ready" or self._db is None:
                raise IndexUnavailable()

    # ------------------------------------------------------------------
    # Entry reads (SQLite)
    # ------------------------------------------------------------------

    def entry(self, path: str) -> NoteIndexEntry | None:
        with self._lock:
            if self._db is None:
                return None
            try:
                row = self._db.fetchone(
                    "SELECT path, sha256, title, basename, text, text_truncated, "
                    "frontmatter_status, diagnostic, parse_error_json, search_folded "
                    "FROM notes WHERE path = ?",
                    (path,),
                )
            except sqlite3.Error as exc:
                logger.warning("derived index entry read failed: %s", exc)
                return None
            if row is None:
                return None
            return self._entry_from_row(row)

    def entries(self) -> list[NoteIndexEntry]:
        with self._lock:
            if self._db is None:
                return []
            try:
                rows = self._db.fetchall(
                    "SELECT path, sha256, title, basename, text, text_truncated, "
                    "frontmatter_status, diagnostic, parse_error_json, search_folded "
                    "FROM notes ORDER BY path"
                )
            except sqlite3.Error as exc:
                logger.warning("derived index entries read failed: %s", exc)
                return []
            return [self._entry_from_row(row) for row in rows]

    def note_count(self) -> int:
        with self._lock:
            if self._db is None:
                return 0
            try:
                row = self._db.fetchone("SELECT COUNT(*) AS n FROM notes")
                return int(row["n"]) if row else 0
            except sqlite3.Error:
                return 0

    def _entry_from_row(self, row: sqlite3.Row) -> NoteIndexEntry:
        path = str(row["path"])
        tags, tags_folded = self._tags_of(path)
        outgoing = self._outgoing_refs(path)
        return NoteIndexEntry(
            path=path,
            sha256=str(row["sha256"]),
            title=str(row["title"]),
            basename=str(row["basename"]),
            text=str(row["text"]),
            text_truncated=bool(row["text_truncated"]),
            tags=tags,
            tags_folded=tags_folded,
            properties=self._properties_of(path),
            frontmatter_status=str(row["frontmatter_status"]),  # type: ignore[arg-type]
            parse_error=(
                json.loads(row["parse_error_json"])
                if row["parse_error_json"] is not None
                else None
            ),
            outgoing=outgoing,
            resolved_targets={
                ref.resolved_path
                for ref in outgoing
                if ref.resolved_path is not None
            },
            search_folded=str(row["search_folded"] or ""),
            diagnostic=row["diagnostic"],
        )

    def _tags_of(self, path: str) -> tuple[list[str], list[str]]:
        rows = self._db.fetchall(  # type: ignore[union-attr]
            "SELECT tag, tag_folded FROM tags WHERE note_path = ? ORDER BY seq",
            (path,),
        )
        return [str(r["tag"]) for r in rows], [str(r["tag_folded"]) for r in rows]

    def _properties_of(self, path: str) -> dict[str, Any]:
        rows = self._db.fetchall(  # type: ignore[union-attr]
            "SELECT key, value_json FROM properties WHERE note_path = ? ORDER BY key",
            (path,),
        )
        result: dict[str, Any] = {}
        for row in rows:
            try:
                result[str(row["key"])] = json.loads(row["value_json"])
            except (TypeError, ValueError):
                result[str(row["key"])] = None
        return result

    def _outgoing_refs(self, path: str) -> list[WikilinkRef]:
        rows = self._db.fetchall(  # type: ignore[union-attr]
            "SELECT target, raw, kind, display, section, block, resolved_path, "
            "broken, ambiguous, context, candidates_json "
            "FROM links WHERE source_path = ? ORDER BY seq",
            (path,),
        )
        refs: list[WikilinkRef] = []
        for row in rows:
            try:
                candidates = json.loads(row["candidates_json"]) if row["candidates_json"] else []
            except (TypeError, ValueError):
                candidates = []
            refs.append(
                WikilinkRef(
                    target=str(row["target"]),
                    raw=str(row["raw"]),
                    kind=str(row["kind"]),  # type: ignore[arg-type]
                    display=row["display"],
                    section=row["section"],
                    block=row["block"],
                    context=row["context"],
                    resolved_path=row["resolved_path"],
                    broken=bool(row["broken"]),
                    ambiguous=bool(row["ambiguous"]),
                    candidates=[str(item) for item in candidates],
                )
            )
        return refs

    def basenames_noext(self, key: str) -> list[str]:
        with self._lock:
            return list(self._basenames_noext.get(key.casefold(), []))

    def basenames_full(self, key: str) -> list[str]:
        with self._lock:
            return list(self._basenames_full.get(key.casefold(), []))

    # ------------------------------------------------------------------
    # Full rebuild
    # ------------------------------------------------------------------

    def rebuild(self) -> IndexRebuildResponse:
        started = time.perf_counter()
        with self._lock:
            if not self._open_database():
                self._set_unavailable("Derived index database is unavailable")
                raise IndexUnavailable()

            try:
                tree = self._vault.list_tree("", recursive=True)
            except VaultError as exc:
                self._set_unavailable(exc.code.value)
                logger.warning("index rebuild failed code=%s", exc.code.value)
                raise IndexUnavailable() from exc

            files = sorted(
                (
                    item.path
                    for item in tree
                    if item.kind == "file" and item.path != "."
                ),
                key=str.casefold,
            )
            full_map: dict[str, list[str]] = {}
            noext_map: dict[str, list[str]] = {}
            for path in files:
                full_map.setdefault(path.rsplit("/", 1)[-1].casefold(), []).append(path)
                if _markdown_file(path):
                    name = path.rsplit("/", 1)[-1]
                    noext_map.setdefault(_strip_extension(name).casefold(), []).append(path)
            self._basenames_full = full_map
            self._basenames_noext = noext_map

            skipped = 0
            failed = 0
            written = 0
            assert self._db is not None
            try:
                with self._db.transaction() as conn:
                    conn.execute("DELETE FROM notes")
                    if self._fts_available:
                        conn.execute("DELETE FROM notes_fts")
                    for path in files:
                        if not _markdown_file(path):
                            continue
                        entry = self._read_and_build_entry(path, full_map, noext_map)
                        if entry is None:
                            skipped += 1
                            continue
                        if entry.frontmatter_status == "unreadable" or entry.diagnostic is not None:
                            failed += 1
                        self._insert_note_row(conn, path, entry)
                        written += 1
                    conn.execute(_BACKLINK_MATERIALIZE_SQL)
            except Exception:
                logger.exception("index rebuild failed (transaction rolled back)")
                raise

            self._last_skipped = skipped
            self._last_failed = failed
            self._last_built_at = datetime.now(UTC)
            self._build_state = "ready"
            self._build_error = None
            duration_ms = (time.perf_counter() - started) * 1000.0
            logger.info(
                "index rebuilt notes=%d skipped=%d failed=%d duration_ms=%.1f",
                written,
                skipped,
                failed,
                duration_ms,
            )
            return IndexRebuildResponse(
                indexed=max(0, written - failed),
                skipped=skipped,
                failed=failed,
                duration_ms=round(duration_ms, 3),
                ready=True,
                generated_at=self._last_built_at,
            )

    # ------------------------------------------------------------------
    # Per-note construction (unchanged M3 parsing, rows written to SQLite)
    # ------------------------------------------------------------------

    def _read_and_build_entry(
        self,
        path: str,
        full_map: dict[str, list[str]],
        noext_map: dict[str, list[str]],
    ) -> NoteIndexEntry | None:
        """Read one note and produce its entry; ``None`` ⇒ skipped note."""
        try:
            data, digest = self._vault.read_bytes(path)
        except PathNotFound:
            return None
        except VaultError:
            return None  # transient/unreadable → skipped, isolated
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            entry = self._empty_entry(path, digest, "non_utf8")
            entry.frontmatter_status = "unreadable"
            entry.diagnostic = "File is not valid UTF-8"
            return entry
        return self._entry_from_text(path, text, digest, full_map, noext_map)

    def _empty_entry(
        self,
        path: str,
        digest: str,
        diagnostic: str,
    ) -> NoteIndexEntry:
        name = path.rsplit("/", 1)[-1]
        return NoteIndexEntry(
            path=path,
            sha256=digest,
            title=basename_no_extension(path),
            basename=_strip_extension(name),
            text="",
            text_truncated=False,
            diagnostic=diagnostic,
        )

    def _entry_from_text(
        self,
        path: str,
        text: str,
        digest: str,
        full_map: dict[str, list[str]],
        noext_map: dict[str, list[str]],
    ) -> NoteIndexEntry:
        name = path.rsplit("/", 1)[-1]
        stem = _strip_extension(name)
        frontmatter = parse_frontmatter(text)
        body = strip_frontmatter(text)
        truncated = False
        if len(body) > self._note_text_cap:
            body = body[: self._note_text_cap]
            truncated = True
        title = derive_title(body, stem)
        outgoing = parse_wikilinks(text)
        resolved_outgoing: list[WikilinkRef] = []
        resolved_targets: set[str] = set()
        for ref in outgoing:
            resolved = self._resolve_ref(path, ref, full_map, noext_map)
            resolved_outgoing.append(resolved)
            if resolved.resolved_path:
                resolved_targets.add(resolved.resolved_path)
        searchable = " ".join(
            (stem, title, " ".join(frontmatter.tags_folded), body)
        )
        return NoteIndexEntry(
            path=path,
            sha256=digest,
            title=title,
            basename=stem,
            text=body,
            text_truncated=truncated,
            tags=frontmatter.tags,
            tags_folded=frontmatter.tags_folded,
            properties=frontmatter.properties,
            frontmatter_status=frontmatter.status,  # type: ignore[arg-type]
            parse_error=frontmatter.parse_error,
            outgoing=resolved_outgoing,
            resolved_targets=resolved_targets,
            search_folded=searchable.casefold(),
        )

    def _resolve_ref(
        self,
        source_path: str,
        ref: WikilinkRef,
        full_map: dict[str, list[str]],
        noext_map: dict[str, list[str]],
    ) -> WikilinkRef:
        """Resolve a parsed ref to a path using basename maps (PLAN §5.3/10.1)."""
        if ref.kind == "web":
            return ref
        target = ref.target.strip()
        if not target:
            # [[#Heading]] / [[^block]] — anchor inside the same note.
            if ref.section is not None or ref.block is not None:
                return dataclasses.replace(
                    ref, resolved_path=source_path, broken=False, ambiguous=False
                )
            return dataclasses.replace(ref, broken=True)

        candidates: set[str] = set()
        if "/" in target:
            # Optional relative-path form (PLAN-M3 §10.2 open item): accept an
            # exact whole-path match with an optional markdown suffix.
            variants = {
                target.casefold(),
                target.casefold() + ".md",
                target.casefold() + ".markdown",
            }
            leaf = target.rsplit("/", 1)[-1]
            leaf_variants = {
                leaf.casefold(),
                leaf.casefold() + ".md",
                leaf.casefold() + ".markdown",
            }
            for variant in leaf_variants:
                for path in full_map.get(variant, []):
                    if path.casefold() in variants:
                        candidates.add(path)
        else:
            # Whole-file basename (with extension) — embeds/attachments.
            candidates.update(full_map.get(target.casefold(), []))
            # Extensionless markdown basename — Obsidian [[Note]] convention.
            noext_target = _strip_extension(target).casefold()
            candidates.update(noext_map.get(noext_target, []))

        if not candidates:
            return dataclasses.replace(ref, broken=True, candidates=[])
        ordered = sorted(candidates, key=str.casefold)
        ambiguous = len(ordered) > 1
        return dataclasses.replace(
            ref,
            resolved_path=ordered[0],
            broken=False,
            ambiguous=ambiguous,
            candidates=ordered if ambiguous else [],
        )

    # ------------------------------------------------------------------
    # SQLite row writers
    # ------------------------------------------------------------------

    def _insert_note_row(
        self,
        conn: sqlite3.Connection,
        path: str,
        entry: NoteIndexEntry,
    ) -> None:
        cursor = conn.execute(
            _INSERT_NOTE_SQL,
            (
                path,
                entry.sha256,
                entry.title,
                entry.basename,
                entry.text,
                1 if entry.text_truncated else 0,
                entry.frontmatter_status,
                entry.diagnostic,
                _dumps(entry.parse_error),
                entry.search_folded,
            ),
        )
        rowid = int(cursor.lastrowid)
        tags_folded = entry.tags_folded or [tag.casefold() for tag in entry.tags]
        for seq, (tag, folded) in enumerate(zip(entry.tags, tags_folded, strict=False)):
            conn.execute(
                "INSERT INTO tags (note_path, tag, tag_folded, seq) "
                "VALUES (?, ?, ?, ?)",
                (path, tag, folded, seq),
            )
        for key, value in sorted(entry.properties.items()):
            conn.execute(
                "INSERT INTO properties (note_path, key, value_json) "
                "VALUES (?, ?, ?)",
                (path, str(key), _dumps(value) or "null"),
            )
        for seq, ref in enumerate(entry.outgoing):
            conn.execute(
                "INSERT INTO links (source_path, seq, target, raw, kind, "
                "display, section, block, resolved_path, broken, ambiguous, "
                "context, candidates_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    path,
                    seq,
                    ref.target,
                    ref.raw,
                    ref.kind,
                    ref.display,
                    ref.section,
                    ref.block,
                    ref.resolved_path,
                    1 if ref.broken else 0,
                    1 if ref.ambiguous else 0,
                    ref.context,
                    _dumps(list(ref.candidates)),
                ),
            )
        if self._fts_available:
            conn.execute(
                _FTS_INSERT_SQL,
                (
                    rowid,
                    path,
                    entry.title,
                    entry.basename,
                    " ".join(tags_folded),
                    entry.text,
                ),
            )

    def _delete_note_rows(self, conn: sqlite3.Connection, path: str) -> None:
        if self._fts_available:
            row = conn.execute(_SELECT_ROWID_SQL, (path,)).fetchone()
            if row is not None:
                conn.execute(_FTS_DELETE_SQL, (int(row["rowid"]),))
        # CASCADE removes tags/properties/links and this note's backlink
        # *source* rows.  Backlink rows targeting ``path`` from other notes
        # are left untouched: M3 never rewrites other notes when one note is
        # deleted/upserted (their stale outgoing refs persist until the next
        # full rebuild, exactly as the in-memory index behaved).
        conn.execute("DELETE FROM notes WHERE path = ?", (path,))

    def _sync_outgoing_backlinks(self, conn: sqlite3.Connection, path: str) -> None:
        """Add the backlink rows contributed by ``path``'s outgoing links.

        M3 semantics: a note's backlink contribution is the set of its
        resolved outgoing targets (self-references excluded).  Called inside
        the upsert transaction right after the note row was rewritten (which
        cascaded this note's old backlink source rows away).
        """
        conn.execute(
            "INSERT OR IGNORE INTO backlinks (target_path, source_path, context) "
            "SELECT l.resolved_path, l.source_path, l.context FROM links l "
            "WHERE l.source_path = ? AND l.resolved_path IS NOT NULL "
            "AND l.resolved_path <> l.source_path",
            (path,),
        )

    # ------------------------------------------------------------------
    # Watcher-driven incremental updates
    # ------------------------------------------------------------------

    def handle_event(self, event: VaultEvent) -> None:
        """Consume one normalized watcher event (create/modify/delete/move).
        
        P1-3 fix: Buffer events for 150ms to merge consecutive operations on the
        same path (e.g., atomic write generates delete+create). Final processing
        checks the actual disk state rather than blindly applying the event.
        """
        try:
            with self._lock:
                if self._db is None:
                    return  # index unavailable; events are dropped until rebuild
                
                # Determine the target path(s)
                if event.kind == "move":
                    # Move is handled immediately without buffering
                    self._apply_delete(event.old_path or "")
                    self._apply_create(event.new_path or "")
                    return
                
                path = event.path or ""
                if not path:
                    return
                
                # Cancel any existing timer for this path
                old_timer = self._event_timers.pop(path, None)
                if old_timer is not None:
                    old_timer.cancel()
                
                # Buffer this event
                now = time.time()
                self._pending_events[path] = (event, now)
                
                # Schedule delayed processing
                timer = threading.Timer(0.15, self._flush_event, args=[path, now])
                self._event_timers[path] = timer
                timer.start()
        except Exception:  # pragma: no cover - defensive
            logger.exception("index event handling failed kind=%s", event.kind)

    def _flush_event(self, path: str, timestamp: float) -> None:
        """Process buffered event after delay, checking actual disk state.
        
        P1-3: This runs after the debounce window. If the event was superseded by
        a newer one, we do nothing. Otherwise, we check if the file actually
        exists on disk and index/delete accordingly, ignoring the event type.
        """
        try:
            with self._lock:
                if self._db is None:
                    return
                
                # Check if this event is still current
                entry = self._pending_events.get(path)
                if entry is None or entry[1] != timestamp:
                    return  # Superseded by a newer event
                
                # Remove from pending
                del self._pending_events[path]
                self._event_timers.pop(path, None)
                
                # Check actual disk state
                exists = self._file_exists(path)
                
                if exists:
                    # File exists: index it (create or modify)
                    self._apply_create(path)
                else:
                    # File doesn't exist: remove from index
                    self._apply_delete(path)
        except Exception:  # pragma: no cover - defensive
            logger.exception("flush event failed path=%s", path)

    def _file_exists(self, path: str) -> bool:
        """Check if a file exists in the vault."""
        try:
            self._vault.read_bytes(path)
            return True
        except VaultError:
            return False

    def flush_events(self) -> None:
        """Immediately process all buffered events (test helper).
        
        P1-3: In production, events are debounced and processed after 150ms.
        Tests that want immediate consistency can call this method to force
        synchronous processing of all pending events.
        """
        with self._lock:
            # Cancel all timers and process events now
            for timer in self._event_timers.values():
                timer.cancel()
            self._event_timers.clear()
            
            # Process all pending events
            for path, (event, _timestamp) in list(self._pending_events.items()):
                del self._pending_events[path]
                exists = self._file_exists(path)
                if exists:
                    self._apply_create(path)
                else:
                    self._apply_delete(path)

    def _apply_create(self, path: str) -> None:
        """Index (or re-index) one file event; directories are ignored."""
        if not path or not _markdown_file(path):
            return
        try:
            data, digest = self._vault.read_bytes(path)
        except VaultError:
            return  # deleted between event and read; event will follow
        assert self._db is not None
        try:
            row = self._db.fetchone(_SELECT_SHA_SQL, (path,))
        except sqlite3.Error:
            return
        if row is not None and str(row["sha256"]) == digest:
            return
        # Basename maps may be stale if the index was built earlier.
        self._refresh_basenames(path)
        full_map = self._basenames_full
        noext_map = self._basenames_noext
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            entry = self._empty_entry(path, digest, "non_utf8")
            entry.frontmatter_status = "unreadable"
            entry.diagnostic = "File is not valid UTF-8"
        else:
            entry = self._entry_from_text(path, text, digest, full_map, noext_map)
        try:
            with self._db.transaction() as conn:
                self._delete_note_rows(conn, path)
                self._insert_note_row(conn, path, entry)
                self._sync_outgoing_backlinks(conn, path)
        except sqlite3.Error:
            logger.exception("index upsert failed path=%s", path)

    def _refresh_basenames(self, path: str) -> None:
        name = path.rsplit("/", 1)[-1]
        key_full = name.casefold()
        existing_full = [
            item for item in self._basenames_full.get(key_full, []) if item != path
        ]
        existing_full.append(path)
        self._basenames_full[key_full] = sorted(existing_full, key=str.casefold)
        if _markdown_file(path):
            key_noext = _strip_extension(name).casefold()
            existing_noext = [
                item
                for item in self._basenames_noext.get(key_noext, [])
                if item != path
            ]
            existing_noext.append(path)
            self._basenames_noext[key_noext] = sorted(existing_noext, key=str.casefold)

    def _apply_delete(self, path: str) -> None:
        if not path:
            return
        assert self._db is not None
        try:
            row = self._db.fetchone(_SELECT_SHA_SQL, (path,))
        except sqlite3.Error:
            row = None
        if row is not None:
            try:
                with self._db.transaction() as conn:
                    self._delete_note_rows(conn, path)
            except sqlite3.Error:
                logger.exception("index delete failed path=%s", path)
        name = path.rsplit("/", 1)[-1]
        key_full = name.casefold()
        if key_full in self._basenames_full:
            remaining = [
                item for item in self._basenames_full[key_full] if item != path
            ]
            if remaining:
                self._basenames_full[key_full] = remaining
            else:
                self._basenames_full.pop(key_full, None)
        if _markdown_file(path):
            key_noext = _strip_extension(name).casefold()
            if key_noext in self._basenames_noext:
                remaining = [
                    item for item in self._basenames_noext[key_noext] if item != path
                ]
                if remaining:
                    self._basenames_noext[key_noext] = remaining
                else:
                    self._basenames_noext.pop(key_noext, None)

    # ------------------------------------------------------------------
    # Queries used by Links/Search services
    # ------------------------------------------------------------------

    def outgoing_for(self, path: str) -> list[WikilinkRef]:
        with self._lock:
            if self._db is None:
                return []
            try:
                rows = self._db.fetchall(
                    "SELECT 1 FROM notes WHERE path = ?", (path,)
                )
            except sqlite3.Error:
                return []
            if not rows:
                return []
            return self._outgoing_refs(path)

    def backlink_sources(self, path: str) -> list[tuple[str, WikilinkRef]]:
        """(source path, ref-in-source) pairs whose resolved target == path."""
        result: list[tuple[str, WikilinkRef]] = []
        with self._lock:
            if self._db is None:
                return []
            try:
                source_rows = self._db.fetchall(
                    "SELECT source_path FROM backlinks WHERE target_path = ? "
                    "ORDER BY source_path",
                    (path,),
                )
            except sqlite3.Error:
                return []
            for row in source_rows:
                source_path = str(row["source_path"])
                if source_path == path:
                    continue
                refs = self._db.fetchall(  # type: ignore[union-attr]
                    "SELECT target, raw, kind, display, section, block, "
                    "resolved_path, broken, ambiguous, context, candidates_json "
                    "FROM links WHERE source_path = ? AND resolved_path = ? "
                    "ORDER BY seq",
                    (source_path, path),
                )
                for ref_row in refs:
                    result.append((source_path, self._ref_from_row(ref_row)))
        result.sort(key=lambda item: (item[0].casefold(), item[1].raw))
        return result

    def _ref_from_row(self, row: sqlite3.Row) -> WikilinkRef:
        try:
            candidates = json.loads(row["candidates_json"]) if row["candidates_json"] else []
        except (TypeError, ValueError):
            candidates = []
        return WikilinkRef(
            target=str(row["target"]),
            raw=str(row["raw"]),
            kind=str(row["kind"]),  # type: ignore[arg-type]
            display=row["display"],
            section=row["section"],
            block=row["block"],
            context=row["context"],
            resolved_path=row["resolved_path"],
            broken=bool(row["broken"]),
            ambiguous=bool(row["ambiguous"]),
            candidates=[str(item) for item in candidates],
        )

    def tags_for(self, tag_folded: str) -> list[str]:
        with self._lock:
            if self._db is None:
                return []
            try:
                rows = self._db.fetchall(
                    "SELECT note_path FROM tags WHERE tag_folded = ?",
                    (tag_folded.casefold(),),
                )
            except sqlite3.Error:
                return []
            return sorted((str(r["note_path"]) for r in rows), key=str.casefold)

    # ------------------------------------------------------------------
    # M5 read-only graph queries (PLAN-M5 §5.3/§5.4) — additive, no writes.
    # ------------------------------------------------------------------

    def note_exists(self, path: str) -> bool:
        """True when ``path`` is a note row of the derived index.

        Conservative on DB errors (False) like the sibling ``entry()`` read
        helpers: graph gating follows the M3/M4 convention where a note that
        cannot be read surfaces as ``not_found`` rather than a 5xx.
        """
        with self._lock:
            if self._db is None:
                return False
            try:
                row = self._db.fetchone(
                    "SELECT 1 FROM notes WHERE path = ?", (path,)
                )
            except sqlite3.Error:
                logger.warning("derived index note_exists read failed")
                return False
            return row is not None

    def note_title(self, path: str) -> str | None:
        """Return the derived title of one note row, or None when absent."""
        with self._lock:
            if self._db is None:
                return None
            try:
                row = self._db.fetchone(
                    "SELECT title FROM notes WHERE path = ?", (path,)
                )
            except sqlite3.Error:
                logger.warning("derived index note_title read failed")
                return None
            return str(row["title"]) if row is not None else None

    def note_tags(self, path: str) -> list[tuple[str, str]]:
        """Return ``(display, folded)`` tag pairs of one note in ``seq`` order.

        ``tags_for`` answers the inverse direction (folded tag → note paths);
        this helper answers note → tags for graph/UI composition.  Both stay
        read-only and take the index RLock per call.
        """
        with self._lock:
            if self._db is None:
                return []
            try:
                rows = self._db.fetchall(
                    "SELECT tag, tag_folded FROM tags WHERE note_path = ? "
                    "ORDER BY seq",
                    (path,),
                )
            except sqlite3.Error:
                logger.warning("derived index note_tags read failed")
                return []
            return [(str(r["tag"]), str(r["tag_folded"])) for r in rows]

    def graph_snapshot(self) -> GraphSnapshot:
        """Return one coherent read of every M4 row the graph projects.

        The three SELECTs run under a single RLock hold, so a concurrent
        ``rebuild()``/watcher upsert can never be observed half-applied: the
        request waits or sees a fully-before/fully-after state (PLAN-M5
        §5.3).  All lists come back sorted deterministically
        (notes/tags/links by path/seq) so repeated snapshots of an unchanged
        index are byte-identical.  Rows are copied into frozen dataclasses
        before the lock is released.

        A DB-level read failure raises :class:`IndexUnavailable` (HTTP 503
        ``index_unavailable``) instead of silently serving an empty graph.
        """
        with self._lock:
            if self._db is None:
                raise IndexUnavailable()
            try:
                note_rows = self._db.fetchall(
                    "SELECT path, title, basename FROM notes ORDER BY path"
                )
                tag_rows = self._db.fetchall(
                    "SELECT note_path, tag, tag_folded, seq FROM tags "
                    "ORDER BY note_path, seq"
                )
                link_rows = self._db.fetchall(
                    "SELECT source_path, seq, target, raw, kind, display, "
                    "section, block, context, resolved_path, broken, "
                    "ambiguous, candidates_json "
                    "FROM links ORDER BY source_path, seq"
                )
            except sqlite3.Error as exc:
                logger.warning("derived index graph snapshot read failed: %s", exc)
                raise IndexUnavailable() from exc
            notes = tuple(
                GraphNoteRow(
                    path=str(r["path"]),
                    title=str(r["title"]),
                    basename=str(r["basename"]),
                )
                for r in note_rows
            )
            tags = tuple(
                GraphTagRow(
                    note_path=str(r["note_path"]),
                    tag=str(r["tag"]),
                    tag_folded=str(r["tag_folded"]),
                    seq=int(r["seq"]),
                )
                for r in tag_rows
            )
            links = tuple(self._graph_link_row(r) for r in link_rows)
            return GraphSnapshot(notes=notes, tags=tags, links=links)

    def graph_totals(self) -> GraphTotals:
        """Return note / distinct-folded-tag / link-row counts (no writes).

        Useful for cheap graph sizing before projection and as perf evidence
        in the M5 limits suite.  Raises :class:`IndexUnavailable` when the
        database cannot be read.
        """
        with self._lock:
            if self._db is None:
                raise IndexUnavailable()
            try:
                note_row = self._db.fetchone("SELECT COUNT(*) AS n FROM notes")
                tag_row = self._db.fetchone(
                    "SELECT COUNT(DISTINCT tag_folded) AS n FROM tags"
                )
                link_row = self._db.fetchone(
                    "SELECT COUNT(*) AS n FROM links"
                )
            except sqlite3.Error as exc:
                logger.warning("derived index graph totals read failed: %s", exc)
                raise IndexUnavailable() from exc
            return GraphTotals(
                note_count=int(note_row["n"]) if note_row else 0,
                tag_count=int(tag_row["n"]) if tag_row else 0,
                link_count=int(link_row["n"]) if link_row else 0,
            )

    @staticmethod
    def _graph_link_row(row: sqlite3.Row) -> GraphLinkRow:
        try:
            candidates = (
                json.loads(row["candidates_json"]) if row["candidates_json"] else []
            )
        except (TypeError, ValueError):
            candidates = []
        return GraphLinkRow(
            source_path=str(row["source_path"]),
            seq=int(row["seq"]),
            target=str(row["target"]),
            raw=str(row["raw"]),
            kind=str(row["kind"]),
            display=row["display"],
            section=row["section"],
            block=row["block"],
            context=row["context"],
            resolved_path=row["resolved_path"],
            broken=bool(row["broken"]),
            ambiguous=bool(row["ambiguous"]),
            candidates=tuple(str(item) for item in candidates),
        )

    # ------------------------------------------------------------------
    # Search primitives (M4; SearchService owns scoring/DTO)
    # ------------------------------------------------------------------

    def fts_search(self, match_expr: str) -> list[SearchCandidate]:
        """Run one FTS5 ``MATCH`` and return ranked note rows.

        ``match_expr`` is a sanitised FTS5 query expression built by
        ``SearchService`` (quoted phrases joined by spaces ⇒ implicit AND).
        Rows come back in ``bm25`` order (best first) with the raw bm25 value
        in ``SearchCandidate.score``; SearchService maps it to a positive
        ``hit.score``.
        """
        with self._lock:
            if self._db is None or not self._fts_available or not match_expr:
                return []
            try:
                rows = self._db.fetchall(
                    "SELECT n.path, n.title, n.basename, n.text, "
                    + _FTS_WEIGHTS
                    + " AS score "
                    "FROM notes_fts JOIN notes n ON n.rowid = notes_fts.rowid "
                    "WHERE n.frontmatter_status != 'unreadable' "
                    "AND n.diagnostic IS NULL AND notes_fts MATCH ? "
                    "ORDER BY score ASC",
                    (match_expr,),
                )
            except sqlite3.OperationalError:
                logger.warning("fts query failed expr=%r", match_expr, exc_info=True)
                return []
            return [
                SearchCandidate(
                    path=str(r["path"]),
                    title=str(r["title"]),
                    basename=str(r["basename"]),
                    text=str(r["text"]),
                    tags_folded="",
                    score=float(r["score"]),
                )
                for r in rows
            ]

    def substring_search(self, terms: list[str]) -> list[SearchCandidate]:
        """M3 degraded path: AND over substring hits inside ``search_folded``.

        Every whitespace term must appear (as a casefolded substring) in the
        note corpus (basename + title + folded tags + body) — the same
        semantics as the M3 in-memory scan, now executed in SQL.
        """
        with self._lock:
            if self._db is None or not terms:
                return []
            clauses = " AND ".join(
                "instr(n.search_folded, ?) > 0" for _ in terms
            )
            sql = (
                "SELECT n.path, n.title, n.basename, n.text, "
                "(SELECT group_concat(t.tag_folded, ' ') FROM tags t "
                " WHERE t.note_path = n.path) AS tags_folded "
                "FROM notes n "
                "WHERE n.frontmatter_status != 'unreadable' "
                "AND n.diagnostic IS NULL AND (" + clauses + ") "
                "ORDER BY n.path"
            )
            try:
                rows = self._db.fetchall(sql, list(terms))
            except sqlite3.Error:
                logger.exception("substring search failed")
                return []
            return [
                SearchCandidate(
                    path=str(r["path"]),
                    title=str(r["title"]),
                    basename=str(r["basename"]),
                    text=str(r["text"]),
                    tags_folded=str(r["tags_folded"] or ""),
                )
                for r in rows
            ]

    def clear(self) -> None:
        """Reset readiness so read endpoints 503 until the next rebuild.

        Keeps the open database handle (rows are disposable and are wiped by
        the next ``rebuild()`` transaction); the M3 semantics — after a
        ``clear()`` the index must not serve reads — are preserved.
        """
        with self._lock:
            self._build_state = "idle"
            self._build_error = None
            self._last_built_at = None
            self._last_skipped = 0
            self._last_failed = 0


__all__ = ["DerivedIndexService"]
