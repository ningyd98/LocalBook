"""Reader database operations layer."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any

from .errors import ReaderError, ReaderErrorCode
from .schemas import DeviceType, SyncEntityType, SyncOperation


class ReaderDatabase:
    """Database operations for Reader API."""

    def __init__(self, db_path: str) -> None:
        """Initialize database connection.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path

        # Ensure parent directory exists
        parent = Path(db_path).parent
        parent.mkdir(parents=True, exist_ok=True)

        self._ensure_schema()

    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection with foreign keys enabled."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _ensure_schema(self) -> None:
        """Ensure the reader schema is applied."""
        import logging
        from pathlib import Path

        logger = logging.getLogger("localnote.reader")

        conn = self._get_connection()
        try:
            # Create migrations tracking table if it doesn't exist
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    filename TEXT PRIMARY KEY,
                    applied_at INTEGER NOT NULL
                )
            """)
            conn.commit()
            logger.info("schema_migrations table ensured")

            # Backward compatibility: if old schema_version table exists with version 1,
            # automatically record 001 migration as applied
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
            )
            if cursor.fetchone():
                cursor = conn.execute("SELECT version FROM schema_version WHERE version = 1")
                if cursor.fetchone():
                    # Version 1 exists in old tracking table
                    cursor = conn.execute(
                        "SELECT 1 FROM schema_migrations "
                        "WHERE filename = '001_init_reader_schema.sql'"
                    )
                    if not cursor.fetchone():
                        # Record 001 as applied
                        conn.execute(
                            "INSERT INTO schema_migrations (filename, applied_at) VALUES (?, ?)",
                            ("001_init_reader_schema.sql", int(time.time())),
                        )
                        conn.commit()
                        logger.info(
                            "Recorded 001_init_reader_schema.sql as applied "
                            "(from old schema_version)"
                        )

            # Apply all migrations in order
            migrations_dir = Path(__file__).parent / "migrations"
            migration_files = sorted(migrations_dir.glob("*.sql"))
            logger.info(
                f"Found {len(migration_files)} migration files: {[f.name for f in migration_files]}"
            )

            for migration_path in migration_files:
                filename = migration_path.name
                logger.info(f"Checking migration: {filename}")

                # Check if migration already applied
                cursor = conn.execute(
                    "SELECT 1 FROM schema_migrations WHERE filename = ?", (filename,)
                )
                result = cursor.fetchone()
                logger.info(f"Migration check result for {filename}: {result}")

                if result:
                    logger.info(f"Skipping already applied migration: {filename}")
                    continue  # Already applied

                logger.info(f"Applying migration: {filename}")

                # Special handling for schema transformations that SQLite
                # cannot express with a portable conditional ALTER TABLE.
                if filename == "002_reader_pairing_and_outbox.sql":
                    self._apply_migration_002(conn, migration_path)
                elif filename == "003_reader_conversation_source_nullable.sql":
                    self._apply_migration_003(conn, migration_path)
                elif filename == "007_reader_conversation_owner.sql":
                    self._apply_migration_007(conn)
                else:
                    # Apply migration normally (executescript commits automatically)
                    with open(migration_path, encoding="utf-8") as f:
                        migration_sql = f.read()
                    conn.executescript(migration_sql)

                # Record migration (in a new transaction after executescript)
                conn.execute(
                    "INSERT INTO schema_migrations (filename, applied_at) VALUES (?, ?)",
                    (filename, int(time.time())),
                )
                conn.commit()
                logger.info(f"Migration applied and recorded: {filename}")

            # Health check: a recorded migration is NOT proof that the schema
            # converged. Databases upgraded by an earlier build could record 002
            # while skipping its body (that build mis-detected pairing_tokens and
            # took its "already correct schema" branch), which left
            # reader_sync_outbox on the old payload schema and broke sync
            # permanently. Re-run the idempotent 002 transform whenever 002 is
            # recorded, so convergence is decided by the actual schema rather than
            # by the ledger.
            cursor = conn.execute(
                "SELECT 1 FROM schema_migrations "
                "WHERE filename = '002_reader_pairing_and_outbox.sql'"
            )
            if cursor.fetchone():
                migration_002 = migrations_dir / "002_reader_pairing_and_outbox.sql"
                if migration_002.exists():
                    self._apply_migration_002(conn, migration_002)
                    logger.info("Health check: 002 schema convergence verified")
                else:
                    logger.error(
                        "Health check: migration file 002 is missing; "
                        "cannot verify schema convergence"
                    )
        finally:
            conn.close()

    def _apply_migration_007(self, conn: sqlite3.Connection) -> None:
        """Add a device owner to conversations without discarding legacy data."""
        columns = {row[1] for row in conn.execute("PRAGMA table_info(reader_conversations)")}
        if "device_id" not in columns:
            conn.execute(
                "ALTER TABLE reader_conversations ADD COLUMN device_id TEXT "
                "REFERENCES devices(id) ON DELETE CASCADE"
            )
        # A session has an unambiguous owner. Older source-only conversations
        # remain unowned and cannot be replayed through another device's token.
        conn.execute(
            """
            UPDATE reader_conversations
            SET device_id = (
                SELECT device_id FROM reader_sessions
                WHERE reader_sessions.id = reader_conversations.session_id
            )
            WHERE device_id IS NULL AND session_id IS NOT NULL
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reader_conversations_device_id "
            "ON reader_conversations(device_id)"
        )
        conn.commit()

    def _apply_migration_002(self, conn: sqlite3.Connection, migration_path: Path) -> None:
        """Apply migration 002 with special handling for sync_outbox schema transformation.

        This migration needs conditional logic because:
        1. Fresh databases need the new schema created
        2. Databases with old schema (payload column) need data migration
        3. Databases with new schema (device_id column) need no changes
        4. pairing_tokens table may already exist with incomplete schema
        """
        logger = logging.getLogger("localnote.reader")
        logger.info("Starting migration 002 with sync_outbox schema transformation")

        # Check if pairing_tokens table exists and has correct schema
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pairing_tokens'"
        )
        pairing_tokens_exists = cursor.fetchone() is not None

        if pairing_tokens_exists:
            # Check if the table has the columns code actually uses
            cursor = conn.execute("PRAGMA table_info(pairing_tokens)")
            columns = {row[1] for row in cursor.fetchall()}
            logger.info(f"Existing pairing_tokens columns: {columns}")

            # Check what columns are missing
            needs_used_at = "used_at" not in columns
            needs_used_by_device_id = "used_by_device_id" not in columns

            if needs_used_at or needs_used_by_device_id:
                # Non-destructively add missing columns
                logger.info(
                    "pairing_tokens needs columns: "
                    f"used_at={needs_used_at}, used_by_device_id={needs_used_by_device_id}"
                )

                if needs_used_at:
                    logger.info("Adding used_at column to pairing_tokens")
                    conn.execute("ALTER TABLE pairing_tokens ADD COLUMN used_at INTEGER")

                if needs_used_by_device_id:
                    logger.info("Adding used_by_device_id column to pairing_tokens")
                    conn.execute("ALTER TABLE pairing_tokens ADD COLUMN used_by_device_id TEXT")

                conn.commit()
                logger.info("pairing_tokens schema updated non-destructively")
            else:
                logger.info("pairing_tokens already has all required columns")
        else:
            # Table doesn't exist - create it from migration SQL
            logger.info("pairing_tokens doesn't exist, creating from migration SQL")
            with open(migration_path, encoding="utf-8") as f:
                migration_sql = f.read()
            conn.executescript(migration_sql)
            logger.info("Executed migration 002 SQL (pairing_tokens table created)")
            conn.commit()

        # Now handle sync_outbox transformation
        # Check if reader_sync_outbox table exists
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='reader_sync_outbox'"
        )
        table_exists = cursor.fetchone() is not None

        if not table_exists:
            # Table doesn't exist - create with new schema
            logger.info("reader_sync_outbox table doesn't exist, creating with new schema")
            conn.execute("""
                CREATE TABLE reader_sync_outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    operation TEXT NOT NULL CHECK (operation IN ('create', 'update', 'delete')),
                    data TEXT NOT NULL,
                    created_at INTEGER NOT NULL DEFAULT (unixepoch()),
                    sync_state TEXT NOT NULL DEFAULT 'pending'
                        CHECK (sync_state IN ('pending', 'synced', 'failed')),
                    FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
                )
            """)
            conn.execute(
                "CREATE INDEX idx_reader_sync_outbox_device_id ON reader_sync_outbox(device_id)"
            )
            conn.execute(
                "CREATE INDEX idx_reader_sync_outbox_entity "
                "ON reader_sync_outbox(entity_type, entity_id)"
            )
            conn.execute(
                "CREATE INDEX idx_reader_sync_outbox_created_at ON reader_sync_outbox(created_at)"
            )
            conn.execute(
                "CREATE INDEX idx_reader_sync_outbox_sync_state ON reader_sync_outbox(sync_state)"
            )
            conn.execute(
                "CREATE INDEX idx_reader_sync_outbox_device_pending "
                "ON reader_sync_outbox(device_id, sync_state) "
                "WHERE sync_state = 'pending'"
            )
            conn.commit()
            logger.info("Created reader_sync_outbox with new schema")
            return

        # Table exists - check its schema
        cursor = conn.execute("PRAGMA table_info(reader_sync_outbox)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}  # {column_name: type}
        logger.info(f"reader_sync_outbox current columns: {list(columns.keys())}")

        has_payload = "payload" in columns
        has_device_id = "device_id" in columns

        if has_device_id and not has_payload:
            # Already has new schema - nothing to do
            logger.info(
                "reader_sync_outbox already has new schema (device_id column), skipping migration"
            )
            return

        if not has_payload:
            # Neither old nor new schema - something is wrong
            logger.error(f"reader_sync_outbox has unexpected schema: {list(columns.keys())}")
            raise ValueError("reader_sync_outbox table has unexpected schema")

        # Has old schema (payload column) - need to migrate
        logger.info("reader_sync_outbox has old schema (payload column), migrating to new schema")

        # Step 0: Clean up any leftover temporary table from interrupted migration
        conn.execute("DROP TABLE IF EXISTS reader_sync_outbox_new")
        logger.info("Cleaned up any existing reader_sync_outbox_new table")

        # Step 1: Create new table with new schema
        conn.execute("""
            CREATE TABLE reader_sync_outbox_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                operation TEXT NOT NULL CHECK (operation IN ('create', 'update', 'delete')),
                data TEXT NOT NULL,
                created_at INTEGER NOT NULL DEFAULT (unixepoch()),
                sync_state TEXT NOT NULL DEFAULT 'pending'
                    CHECK (sync_state IN ('pending', 'synced', 'failed')),
                FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
            )
        """)
        logger.info("Created reader_sync_outbox_new table")

        # Step 2: Copy data from old table to new table
        # Use the first device for old records, or create a migration placeholder.
        cursor = conn.execute("SELECT id FROM devices LIMIT 1")
        first_device = cursor.fetchone()

        if first_device:
            default_device_id = first_device[0]
            logger.info(f"Using existing device_id for migration: {default_device_id}")
        else:
            # No devices exist - create a placeholder device for migration
            default_device_id = "migration-device-001"
            conn.execute(
                """
                INSERT INTO devices (id, device_name, device_type, created_at, last_seen)
                VALUES (?, 'Migration Placeholder', 'macos', unixepoch(), unixepoch())
            """,
                (default_device_id,),
            )
            logger.info(f"Created placeholder device for migration: {default_device_id}")

        # Copy data with schema transformation
        conn.execute(
            """
            INSERT INTO reader_sync_outbox_new (
                id, device_id, entity_type, entity_id, operation, data, created_at, sync_state
            )
            SELECT
                id,
                ? as device_id,
                entity_type,
                entity_id,
                operation,
                payload as data,
                created_at,
                CASE
                    WHEN processed_at IS NOT NULL THEN 'synced'
                    ELSE 'pending'
                END as sync_state
            FROM reader_sync_outbox
        """,
            (default_device_id,),
        )

        rows_migrated = conn.total_changes
        logger.info(f"Migrated {rows_migrated} rows from old schema to new schema")

        # Step 3: Drop old table
        conn.execute("DROP TABLE reader_sync_outbox")
        logger.info("Dropped old reader_sync_outbox table")

        # Step 4: Rename new table
        conn.execute("ALTER TABLE reader_sync_outbox_new RENAME TO reader_sync_outbox")
        logger.info("Renamed reader_sync_outbox_new to reader_sync_outbox")

        # Step 5: Create indexes
        conn.execute(
            "CREATE INDEX idx_reader_sync_outbox_device_id ON reader_sync_outbox(device_id)"
        )
        conn.execute(
            "CREATE INDEX idx_reader_sync_outbox_entity "
            "ON reader_sync_outbox(entity_type, entity_id)"
        )
        conn.execute(
            "CREATE INDEX idx_reader_sync_outbox_created_at ON reader_sync_outbox(created_at)"
        )
        conn.execute(
            "CREATE INDEX idx_reader_sync_outbox_sync_state ON reader_sync_outbox(sync_state)"
        )
        conn.execute(
            "CREATE INDEX idx_reader_sync_outbox_device_pending "
            "ON reader_sync_outbox(device_id, sync_state) "
            "WHERE sync_state = 'pending'"
        )
        conn.commit()
        logger.info("Created indexes on migrated reader_sync_outbox table")
        logger.info("Migration 002 sync_outbox transformation completed successfully")

    def _apply_migration_003(self, conn: sqlite3.Connection, migration_path: Path) -> None:
        """Make conversation ``source_id`` nullable without losing data.

        ``AskRequest.source_id`` is optional because a Reader can start a
        conversation before selecting a source.  The original table declared
        the column ``NOT NULL``; rebuild it when upgrading an existing
        database, while fresh databases get the corrected definition from
        migration 001.
        """
        logger = logging.getLogger("localnote.reader")
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='reader_conversations'"
        )
        if cursor.fetchone() is None:
            return

        columns = {
            row[1]: row[3]
            for row in conn.execute("PRAGMA table_info(reader_conversations)").fetchall()
        }
        if columns.get("source_id", 0) == 0:
            logger.info("Migration 003: reader_conversations.source_id is already nullable")
            return

        logger.info("Migration 003: rebuilding reader_conversations with nullable source_id")
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.execute("BEGIN")
            conn.execute("DROP TABLE IF EXISTS reader_conversations_new")
            conn.execute(
                """
                CREATE TABLE reader_conversations_new (
                    id TEXT PRIMARY KEY,
                    source_id TEXT,
                    highlight_id TEXT,
                    session_id TEXT,
                    created_at INTEGER NOT NULL DEFAULT (unixepoch()),
                    FOREIGN KEY (source_id) REFERENCES reader_sources(id) ON DELETE CASCADE,
                    FOREIGN KEY (highlight_id) REFERENCES reader_highlights(id) ON DELETE CASCADE,
                    FOREIGN KEY (session_id) REFERENCES reader_sessions(id) ON DELETE SET NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO reader_conversations_new
                    (id, source_id, highlight_id, session_id, created_at)
                SELECT id, source_id, highlight_id, session_id, created_at
                FROM reader_conversations
                """
            )
            conn.execute("DROP TABLE reader_conversations")
            conn.execute("ALTER TABLE reader_conversations_new RENAME TO reader_conversations")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_reader_conversations_source_id "
                "ON reader_conversations(source_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_reader_conversations_highlight_id "
                "ON reader_conversations(highlight_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_reader_conversations_session_id "
                "ON reader_conversations(session_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_reader_conversations_created_at "
                "ON reader_conversations(created_at DESC)"
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.execute("PRAGMA foreign_keys = ON")

        # Keep the legacy schema_version table in sync for databases created
        # before schema_migrations was introduced.
        with migration_path.open(encoding="utf-8") as migration_file:
            conn.executescript(migration_file.read())
        logger.info("Migration 003 reader_conversations transformation completed")

    # ========================================================================
    # Device & Pairing
    # ========================================================================

    def create_pairing_token(
        self, device_name: str, device_type: DeviceType | str
    ) -> tuple[str, int]:
        """Create a pairing token for device registration.

        Returns:
            tuple: (6-digit token, expiration timestamp)
        """
        # Generate 6-digit token
        token = "".join(str(secrets.randbelow(10)) for _ in range(6))
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        expires_at = int(time.time()) + 300  # 5 minutes

        # Handle both enum and string
        device_type_str = device_type.value if isinstance(device_type, DeviceType) else device_type

        conn = self._get_connection()
        try:
            # Store token in pairing_tokens table
            conn.execute(
                """
                INSERT INTO pairing_tokens (token_hash, device_name, device_type, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (token_hash, device_name, device_type_str, expires_at),
            )
            conn.commit()
            return token, expires_at
        finally:
            conn.close()

    def approve_pairing_token(self, pairing_token: str) -> dict[str, Any]:
        """Approve a live, unused pairing code and return its device metadata."""
        token_hash = hashlib.sha256(pairing_token.encode()).hexdigest()
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT device_name, device_type, expires_at, used_at, approved_at "
                "FROM pairing_tokens WHERE token_hash = ?", (token_hash,)
            ).fetchone()
            if not row:
                raise ReaderError(
                    ReaderErrorCode.INVALID_PAIRING_TOKEN, "Invalid pairing token", 404
                )
            if row["used_at"] is not None or int(time.time()) > row["expires_at"]:
                raise ReaderError(
                    ReaderErrorCode.PAIRING_TOKEN_EXPIRED, "Pairing token has expired", 410
                )
            conn.execute(
                "UPDATE pairing_tokens SET approved_at = unixepoch() "
                "WHERE token_hash = ? AND approved_at IS NULL AND used_at IS NULL",
                (token_hash,),
            )
            conn.commit()
            return {
                "approved": True,
                "device_name": row["device_name"],
                "device_type": row["device_type"],
                "expires_at": row["expires_at"],
            }
        finally:
            conn.close()

    def verify_and_register_device(
        self,
        device_id: str,
        device_name: str,
        device_type: DeviceType | str,
        pairing_token: str,
        public_key: str | None = None,
    ) -> bool:
        """Verify pairing token and complete device registration.

        Returns:
            bool: True if registration successful
        """
        token_hash = hashlib.sha256(pairing_token.encode()).hexdigest()

        # Handle both enum and string
        device_type_str = device_type.value if isinstance(device_type, DeviceType) else device_type

        conn = self._get_connection()
        try:
            # Check pairing token
            cursor = conn.execute(
                """
                SELECT device_name, device_type, expires_at, used_at, approved_at
                FROM pairing_tokens
                WHERE token_hash = ?
                """,
                (token_hash,),
            )
            row = cursor.fetchone()

            if not row:
                raise ReaderError(
                    ReaderErrorCode.INVALID_PAIRING_TOKEN,
                    "Invalid pairing token",
                    401,
                )

            # Check if already used
            if row["used_at"] is not None:
                raise ReaderError(
                    ReaderErrorCode.PAIRING_TOKEN_EXPIRED,
                    "Pairing token already used",
                    401,
                )

            # Check expiration
            if int(time.time()) > row["expires_at"]:
                raise ReaderError(
                    ReaderErrorCode.PAIRING_TOKEN_EXPIRED,
                    "Pairing token has expired",
                    401,
                )

            # Verify device info matches
            if row["device_name"] != device_name or row["device_type"] != device_type_str:
                raise ReaderError(
                    ReaderErrorCode.INVALID_PAIRING_TOKEN,
                    "Device info does not match pairing token",
                    401,
                )

            if row["approved_at"] is None:
                raise ReaderError(
                    ReaderErrorCode.PAIRING_APPROVAL_REQUIRED,
                    "Pairing code requires approval in LocalBook settings",
                    403,
                )

            # Create device
            try:
                conn.execute(
                    """
                    INSERT INTO devices (
                        id, device_name, device_type, public_key, created_at, last_seen
                    )
                    VALUES (?, ?, ?, ?, unixepoch(), unixepoch())
                    """,
                    (device_id, device_name, device_type_str, public_key),
                )
            except sqlite3.IntegrityError as exc:
                raise ReaderError(
                    ReaderErrorCode.DEVICE_ALREADY_REGISTERED,
                    f"Device with id '{device_id}' already exists",
                    400,
                ) from exc

            # Mark token as used
            conn.execute(
                """
                UPDATE pairing_tokens
                SET used_at = unixepoch(), used_by_device_id = ?
                WHERE token_hash = ?
                """,
                (device_id, token_hash),
            )

            conn.commit()
            return True
        finally:
            conn.close()

    def store_access_token(self, device_id: str, token: str) -> None:
        """Persist only a hash of an issued access token.

        The raw bearer token is returned to the client once and is never stored
        in the Reader database. Re-registering a device replaces its previous
        token, which also provides an explicit revocation path.
        """
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        conn = self._get_connection()
        try:
            conn.execute(
                """
                INSERT INTO reader_access_tokens (device_id, token_hash, created_at, last_used_at)
                VALUES (?, ?, unixepoch(), unixepoch())
                ON CONFLICT(device_id) DO UPDATE SET
                    token_hash = excluded.token_hash,
                    created_at = excluded.created_at,
                    last_used_at = excluded.last_used_at
                """,
                (device_id, token_hash),
            )
            conn.commit()
        finally:
            conn.close()

    def authenticate_access_token(self, token: str) -> str | None:
        """Return the registered device for a valid bearer token.

        Authentication is an exact hash lookup; the device id is never trusted
        from the token's spelling. A successful lookup also records token use and
        refreshes the device heartbeat.
        """
        if not token or len(token) > 512:
            return None
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT device_id FROM reader_access_tokens WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            device_id = str(row["device_id"])
            conn.execute(
                "UPDATE reader_access_tokens SET last_used_at = unixepoch() WHERE device_id = ?",
                (device_id,),
            )
            conn.execute(
                "UPDATE devices SET last_seen = unixepoch() WHERE id = ?",
                (device_id,),
            )
            conn.commit()
            return device_id
        finally:
            conn.close()

    def list_devices(self) -> list[dict[str, Any]]:
        """List all registered devices.

        Filters out the internal migration placeholder device which exists
        only to satisfy foreign key constraints for historical sync_outbox rows.

        Returns:
            list[dict]: List of device data
        """
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                """
                SELECT id, device_name, device_type, created_at, last_seen, public_key
                FROM devices
                WHERE id != 'migration-device-001'
                ORDER BY created_at DESC
                """
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_device(self, device_id: str) -> dict[str, Any]:
        """Get device information.

        Filters out the internal migration placeholder device.

        Args:
            device_id: device identifier

        Returns:
            dict: Device data

        Raises:
            ReaderError: If device not found or is internal placeholder
        """
        # Block access to internal migration placeholder
        if device_id == "migration-device-001":
            raise ReaderError(ReaderErrorCode.DEVICE_NOT_FOUND, f"Device not found: {device_id}")

        conn = self._get_connection()
        try:
            cursor = conn.execute(
                """
                SELECT id, device_name, device_type, created_at, last_seen, public_key
                FROM devices
                WHERE id = ?
                """,
                (device_id,),
            )
            row = cursor.fetchone()

            if not row:
                raise ReaderError(
                    ReaderErrorCode.DEVICE_NOT_FOUND,
                    "Device not found",
                    404,
                )

            return dict(row)
        finally:
            conn.close()

    def update_device_last_seen(self, device_id: str) -> None:
        """Update device last_seen timestamp."""
        conn = self._get_connection()
        try:
            conn.execute(
                "UPDATE devices SET last_seen = unixepoch() WHERE id = ?",
                (device_id,),
            )
            conn.commit()
        finally:
            conn.close()

    # ========================================================================
    # Search
    # ========================================================================

    def search_reader_entities(
        self,
        query: str,
        source_types: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search Reader records using bounded, parameterized SQL."""
        terms = [term.casefold() for term in query.split() if term.strip()][:8]
        if not terms:
            return []
        limit = max(1, min(limit, 100))
        normalized_types = [str(value) for value in (source_types or [])]
        conn = self._get_connection()
        try:
            results: list[dict[str, Any]] = []

            def append_rows(
                entity_type: str,
                table: str,
                body_column: str,
                source_id_column: str | None,
            ) -> None:
                conditions: list[str] = []
                params: list[Any] = []
                for term in terms:
                    pattern = f"%{term}%"
                    conditions.append(
                        f"(COALESCE(entity.{body_column}, '') LIKE ? COLLATE NOCASE "
                        "OR COALESCE(src.title, '') LIKE ? COLLATE NOCASE "
                        "OR COALESCE(src.url, '') LIKE ? COLLATE NOCASE)"
                    )
                    params.extend((pattern, pattern, pattern))
                type_clause = ""
                if normalized_types:
                    placeholders = ", ".join("?" for _ in normalized_types)
                    type_clause = f" AND src.type IN ({placeholders})"
                    params.extend(normalized_types)
                source_id_select = f"entity.{source_id_column}" if source_id_column else "NULL"
                params.append(limit)
                rows = conn.execute(
                    f"""
                    SELECT entity.id AS entity_id,
                           src.title AS title,
                           entity.{body_column} AS body,
                           src.url AS url,
                           {source_id_select} AS source_id
                    FROM {table} AS entity
                    LEFT JOIN reader_sources AS src ON src.id = {source_id_select}
                    WHERE {" AND ".join(conditions)}{type_clause}
                    LIMIT ?
                    """,
                    params,
                ).fetchall()
                for row in rows:
                    body = str(row["body"] or "")
                    results.append(
                        {
                            "entity_type": entity_type,
                            "entity_id": str(row["entity_id"]),
                            "title": row["title"],
                            "snippet": body[:240],
                            "score": float(sum(term in body.casefold() for term in terms)),
                            "url": row["url"],
                            "source_id": row["source_id"],
                        }
                    )

            source_conditions: list[str] = []
            source_params: list[Any] = []
            for term in terms:
                pattern = f"%{term}%"
                source_conditions.append(
                    "(COALESCE(entity.title, '') LIKE ? COLLATE NOCASE "
                    "OR COALESCE(entity.url, '') LIKE ? COLLATE NOCASE "
                    "OR COALESCE(entity.metadata, '') LIKE ? COLLATE NOCASE)"
                )
                source_params.extend((pattern, pattern, pattern))
            source_type_clause = ""
            if normalized_types:
                placeholders = ", ".join("?" for _ in normalized_types)
                source_type_clause = f" AND entity.type IN ({placeholders})"
                source_params.extend(normalized_types)
            source_params.append(limit)
            for row in conn.execute(
                f"""
                SELECT entity.id AS entity_id, entity.title, entity.title AS body,
                       entity.url, NULL AS source_id
                FROM reader_sources AS entity
                WHERE {" AND ".join(source_conditions)}{source_type_clause}
                LIMIT ?
                """,
                source_params,
            ).fetchall():
                body = str(row["body"] or "")
                results.append(
                    {
                        "entity_type": "source",
                        "entity_id": str(row["entity_id"]),
                        "title": row["title"],
                        "snippet": body[:240],
                        "score": float(sum(term in body.casefold() for term in terms)),
                        "url": row["url"],
                        "source_id": None,
                    }
                )

            append_rows("highlight", "reader_highlights", "selected_text", "source_id")
            append_rows("note", "reader_notes", "content", "source_id")
            append_rows("reader_item", "reader_items", "content", "source_id")
            results.sort(key=lambda item: (-item["score"], item["entity_id"]))
            return results[:limit]
        finally:
            conn.close()

    # ========================================================================
    # Sync Operations
    # ========================================================================

    def push_sync_operations(
        self, device_id: str, operations: list[dict[str, Any]]
    ) -> tuple[int, int, list[dict[str, Any]], list[str], list[str]]:
        """Process batch sync operations.

        Returns counts, conflict metadata, and the entity IDs in each outcome
        bucket. IDs are part of the client acknowledgement contract: counts alone
        are not enough to safely remove a mixed-success outbox batch.
        """
        conn = self._get_connection()
        accepted = 0
        rejected = 0
        accepted_ids: list[str] = []
        rejected_ids: list[str] = []
        conflicts: list[dict[str, Any]] = []

        try:
            for op in operations:
                entity_id = str(op.get("entity_id", ""))
                try:
                    self._apply_sync_operation(conn, device_id, op)
                    accepted += 1
                    accepted_ids.append(entity_id)
                except ReaderError as e:
                    rejected += 1
                    rejected_ids.append(entity_id)
                    if e.code == ReaderErrorCode.SYNC_CONFLICT:
                        conflicts.append(e.meta)

            conn.commit()
            self.update_device_last_seen(device_id)
            return accepted, rejected, conflicts, accepted_ids, rejected_ids
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _apply_sync_operation(
        self, conn: sqlite3.Connection, device_id: str, operation: dict[str, Any]
    ) -> None:
        """Apply a single sync operation."""
        entity_type = operation["entity_type"]
        entity_id = operation["entity_id"]
        op_type = operation["operation"]
        data = operation.get("data", {})

        # Map entity types to tables
        table_map = {
            SyncEntityType.SOURCE: "reader_sources",
            SyncEntityType.SESSION: "reader_sessions",
            SyncEntityType.HIGHLIGHT: "reader_highlights",
            SyncEntityType.NOTE: "reader_notes",
            SyncEntityType.READER_ITEM: "reader_items",
        }

        table = table_map.get(entity_type)
        if not table:
            raise ReaderError(
                ReaderErrorCode.INVALID_OPERATION,
                f"Invalid entity type: {entity_type}",
            )

        if op_type == SyncOperation.CREATE:
            self._insert_entity(conn, table, entity_id, data, device_id)
        elif op_type == SyncOperation.UPDATE:
            self._update_entity(conn, table, entity_id, data)
        elif op_type == SyncOperation.DELETE:
            self._delete_entity(conn, table, entity_id)
        else:
            raise ReaderError(
                ReaderErrorCode.INVALID_OPERATION,
                f"Invalid operation: {op_type}",
            )

        # Add to sync outbox for distribution
        self._add_to_outbox(conn, device_id, entity_type, entity_id, op_type, data)

    def _insert_entity(
        self,
        conn: sqlite3.Connection,
        table: str,
        entity_id: str,
        data: dict[str, Any],
        device_id: str | None = None,
    ) -> None:
        """Insert an entity into the database."""
        try:
            if table == "reader_sources":
                # Map source_type to type if provided
                source_type = data.get("source_type") or data.get("type")
                if not source_type:
                    raise ReaderError(
                        ReaderErrorCode.INVALID_OPERATION,
                        "Missing required field: type or source_type",
                        400,
                    )
                conn.execute(
                    """
                    INSERT INTO reader_sources (
                        id, type, title, url, canonical_url, file_path, file_hash, metadata
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entity_id,
                        source_type,
                        data.get("title"),
                        data.get("url"),
                        data.get("canonical_url"),
                        data.get("file_path"),
                        data.get("file_hash"),
                        json.dumps(data.get("metadata", {})),
                    ),
                )
            elif table == "reader_sessions":
                # Schema has device/source, timestamps, duration, and count fields.
                # The authenticated device owns the session. Never trust a
                # client-supplied device_id that could point at another device.
                session_device_id = device_id
                if not session_device_id or not data.get("source_id"):
                    raise ReaderError(
                        ReaderErrorCode.INVALID_OPERATION,
                        "Missing required fields: device_id and source_id",
                        400,
                    )
                conn.execute(
                    """
                    INSERT INTO reader_sessions (
                        id, device_id, source_id, started_at, ended_at, duration,
                        highlight_count, query_count, note_count
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entity_id,
                        session_device_id,
                        data.get("source_id"),
                        data.get("started_at") or int(time.time()),
                        data.get("ended_at"),
                        data.get("duration") or data.get("duration_seconds"),
                        data.get("highlight_count", 0),
                        data.get("query_count", 0),
                        data.get("note_count", 0),
                    ),
                )
            elif table == "reader_highlights":
                # Schema has source/session, text/context, page, location, and sync state.
                selected_text = data.get("selected_text") or data.get("text")
                if not selected_text:
                    raise ReaderError(
                        ReaderErrorCode.INVALID_OPERATION,
                        "Missing required field: selected_text or text",
                        400,
                    )
                if not data.get("source_id"):
                    raise ReaderError(
                        ReaderErrorCode.INVALID_OPERATION,
                        "Missing required field: source_id",
                        400,
                    )
                conn.execute(
                    """
                    INSERT INTO reader_highlights (
                        id, source_id, session_id, selected_text, context_before,
                        context_after, page, location, sync_state
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entity_id,
                        data.get("source_id"),
                        data.get("session_id"),
                        selected_text,
                        data.get("context_before"),
                        data.get("context_after"),
                        data.get("page"),
                        json.dumps(data.get("location", {})) if data.get("location") else None,
                        data.get("sync_state", "pending"),
                    ),
                )
            elif table == "reader_notes":
                # Schema has source/highlight/session ids, content, timestamps,
                # revision and sync state.
                if not data.get("content"):
                    raise ReaderError(
                        ReaderErrorCode.INVALID_OPERATION,
                        "Missing required field: content",
                        400,
                    )
                if not data.get("source_id"):
                    raise ReaderError(
                        ReaderErrorCode.INVALID_OPERATION,
                        "Missing required field: source_id",
                        400,
                    )
                conn.execute(
                    """
                    INSERT INTO reader_notes (
                        id, source_id, highlight_id, session_id, content,
                        created_at, updated_at, sync_state
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entity_id,
                        data.get("source_id"),
                        data.get("highlight_id"),
                        data.get("session_id"),
                        data.get("content"),
                        data.get("created_at") or int(time.time()),
                        data.get("updated_at") or int(time.time()),
                        data.get("sync_state", "pending"),
                    ),
                )
            elif table == "reader_items":
                item_type = data.get("type")
                content = data.get("content")
                if not item_type or not content:
                    raise ReaderError(
                        ReaderErrorCode.INVALID_OPERATION,
                        "Missing required fields: type and content",
                        400,
                    )
                conn.execute(
                    """
                    INSERT INTO reader_items (
                        id, device_id, type, source_id, session_id, parent_id,
                        content, metadata, created_at, updated_at, sync_state
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entity_id,
                        device_id,
                        item_type,
                        data.get("source_id"),
                        data.get("session_id"),
                        data.get("parent_id"),
                        content,
                        data.get("metadata"),
                        data.get("created_at") or int(time.time()),
                        data.get("updated_at") or int(time.time()),
                        data.get("sync_state", "pending"),
                    ),
                )
        except sqlite3.IntegrityError as e:
            # Map SQLite integrity errors to Reader domain errors
            error_msg = str(e).lower()
            if "not null" in error_msg or "constraint failed" in error_msg:
                raise ReaderError(
                    ReaderErrorCode.INVALID_OPERATION,
                    f"Missing required field or constraint violation: {e}",
                    400,
                ) from e
            elif "foreign key" in error_msg:
                raise ReaderError(
                    ReaderErrorCode.INVALID_OPERATION,
                    f"Referenced entity does not exist: {e}",
                    400,
                ) from e
            else:
                raise ReaderError(
                    ReaderErrorCode.INVALID_OPERATION,
                    f"Data integrity error: {e}",
                    400,
                ) from e

    def _update_entity(
        self, conn: sqlite3.Connection, table: str, entity_id: str, data: dict[str, Any]
    ) -> None:
        """Update an entity in the database."""
        try:
            # For simplicity, we'll handle the most common updates
            if table == "reader_sources":
                conn.execute(
                    """
                    UPDATE reader_sources
                    SET title = COALESCE(?, title),
                        last_read_at = COALESCE(?, last_read_at),
                        metadata = COALESCE(?, metadata)
                    WHERE id = ?
                    """,
                    (
                        data.get("title"),
                        data.get("last_read_at"),
                        json.dumps(data["metadata"]) if "metadata" in data else None,
                        entity_id,
                    ),
                )
            elif table == "reader_sessions":
                # Schema has: ended_at, duration, highlight_count, query_count, note_count
                conn.execute(
                    """
                    UPDATE reader_sessions
                    SET ended_at = COALESCE(?, ended_at),
                        duration = COALESCE(?, duration),
                        highlight_count = COALESCE(?, highlight_count),
                        query_count = COALESCE(?, query_count),
                        note_count = COALESCE(?, note_count)
                    WHERE id = ?
                    """,
                    (
                        data.get("ended_at"),
                        data.get("duration") or data.get("duration_seconds"),
                        data.get("highlight_count"),
                        data.get("query_count"),
                        data.get("note_count"),
                        entity_id,
                    ),
                )
            elif table == "reader_highlights":
                # Fields: selected text, context, page, location and sync state.
                conn.execute(
                    """
                    UPDATE reader_highlights
                    SET selected_text = COALESCE(?, selected_text),
                        context_before = COALESCE(?, context_before),
                        context_after = COALESCE(?, context_after),
                        page = COALESCE(?, page),
                        location = COALESCE(?, location),
                        sync_state = COALESCE(?, sync_state)
                    WHERE id = ?
                    """,
                    (
                        data.get("selected_text") or data.get("text"),
                        data.get("context_before"),
                        data.get("context_after"),
                        data.get("page"),
                        json.dumps(data["location"]) if "location" in data else None,
                        data.get("sync_state"),
                        entity_id,
                    ),
                )
            elif table == "reader_notes":
                conn.execute(
                    """
                    UPDATE reader_notes
                    SET content = COALESCE(?, content),
                        updated_at = unixepoch(),
                        revision = revision + 1
                    WHERE id = ?
                    """,
                    (data.get("content"), entity_id),
                )
            elif table == "reader_items":
                conn.execute(
                    """
                    UPDATE reader_items
                    SET type = COALESCE(?, type),
                        source_id = COALESCE(?, source_id),
                        session_id = COALESCE(?, session_id),
                        parent_id = COALESCE(?, parent_id),
                        content = COALESCE(?, content),
                        metadata = COALESCE(?, metadata),
                        updated_at = unixepoch(),
                        sync_state = COALESCE(?, sync_state)
                    WHERE id = ?
                    """,
                    (
                        data.get("type"),
                        data.get("source_id"),
                        data.get("session_id"),
                        data.get("parent_id"),
                        data.get("content"),
                        data.get("metadata"),
                        data.get("sync_state"),
                        entity_id,
                    ),
                )
        except sqlite3.IntegrityError as e:
            raise ReaderError(
                ReaderErrorCode.INVALID_OPERATION,
                f"Update failed: {e}",
                400,
            ) from e

    def _delete_entity(self, conn: sqlite3.Connection, table: str, entity_id: str) -> None:
        """Delete an entity from the database."""
        conn.execute(f"DELETE FROM {table} WHERE id = ?", (entity_id,))

    def _add_to_outbox(
        self,
        conn: sqlite3.Connection,
        device_id: str,
        entity_type: str,
        entity_id: str,
        operation: str,
        data: dict[str, Any],
    ) -> None:
        """Add operation to sync outbox."""
        conn.execute(
            """
            INSERT INTO reader_sync_outbox (
                device_id, entity_type, entity_id, operation, data, sync_state
            )
            VALUES (?, ?, ?, ?, ?, 'pending')
            """,
            (device_id, entity_type, entity_id, operation, json.dumps(data)),
        )

    def pull_sync_changes(
        self, device_id: str, cursor: str | None, limit: int
    ) -> tuple[list[dict[str, Any]], str | None, bool]:
        """Pull incremental changes from sync outbox.

        Returns:
            tuple: (entities, next_cursor, has_more)
        """
        conn = self._get_connection()
        try:
            # Parse cursor (format: "timestamp:offset")
            if cursor:
                try:
                    cursor_ts, cursor_offset = cursor.split(":")
                    cursor_ts = int(cursor_ts)
                    cursor_offset = int(cursor_offset)
                except (ValueError, AttributeError) as exc:
                    raise ReaderError(
                        ReaderErrorCode.INVALID_CURSOR,
                        "Invalid sync cursor format",
                    ) from exc
            else:
                # Initial sync - start from beginning
                cursor_ts = 0
                cursor_offset = 0

            # Fetch changes
            cursor_result = conn.execute(
                """
                SELECT id, entity_type, entity_id, operation, data, created_at
                FROM reader_sync_outbox
                WHERE device_id != ? AND created_at >= ? AND id > ?
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (device_id, cursor_ts, cursor_offset, limit + 1),
            )

            rows = cursor_result.fetchall()
            has_more = len(rows) > limit
            rows = rows[:limit]

            entities = []
            for row in rows:
                entities.append(
                    {
                        "entity_type": row["entity_type"],
                        "entity_id": row["entity_id"],
                        "operation": row["operation"],
                        "data": json.loads(row["data"]) if row["data"] else {},
                        "timestamp": row["created_at"],
                    }
                )

            # Generate next cursor
            next_cursor = None
            if rows:
                last_row = rows[-1]
                next_cursor = f"{last_row['created_at']}:{last_row['id']}"

            return entities, next_cursor, has_more
        finally:
            conn.close()

    # ========================================================================
    # Conversations
    # ========================================================================

    def create_conversation(
        self, conversation_id: str, device_id: str, source_id: str | None
    ) -> None:
        """Create a new conversation."""
        conn = self._get_connection()
        try:
            # A fresh selection may not have synced its source yet. Keep the
            # conversation usable and attach the source only if it exists.
            conn.execute(
                """
                INSERT INTO reader_conversations (id, source_id, device_id)
                VALUES (?, (SELECT id FROM reader_sources WHERE id = ?), ?)
                """,
                (conversation_id, source_id, device_id),
            )
            conn.commit()
        finally:
            conn.close()

    def add_message(
        self,
        message_id: str,
        conversation_id: str,
        role: str,
        content: str,
        model: str | None = None,
    ) -> None:
        """Add a message to a conversation."""
        conn = self._get_connection()
        try:
            conn.execute(
                """
                INSERT INTO reader_messages (id, conversation_id, role, content, model)
                VALUES (?, ?, ?, ?, ?)
                """,
                (message_id, conversation_id, role, content, model),
            )
            conn.commit()
        finally:
            conn.close()

    def get_conversation_messages(
        self, conversation_id: str, device_id: str, limit: int = 20
    ) -> list[dict[str, Any]] | None:
        """Get recent owned history; None means a locally assigned new ID."""
        conn = self._get_connection()
        try:
            owner = conn.execute(
                "SELECT device_id FROM reader_conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            if owner is None:
                return None
            if owner["device_id"] != device_id:
                raise ReaderError(
                    ReaderErrorCode.CONVERSATION_NOT_FOUND,
                    "Conversation not found",
                    404,
                )
            cursor = conn.execute(
                """
                SELECT id, role, content, model, created_at
                FROM (
                    SELECT rowid AS sequence, id, role, content, model, created_at
                    FROM reader_messages
                    WHERE conversation_id = ?
                    ORDER BY rowid DESC
                    LIMIT ?
                )
                ORDER BY sequence ASC
                """,
                (conversation_id, limit),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    # ========================================================================
    # Session Import Queries
    # ========================================================================

    def get_session_with_data(
        self, session_id: str, device_id: str | None = None
    ) -> dict[str, Any] | None:
        """Get session data, optionally restricted to its owning device.

        The device filter is required for authenticated Reader imports; the
        optional default keeps the database helper useful for trusted local
        maintenance and existing importer callers.
        """
        conn = self._get_connection()
        try:
            # Get session. Authenticated callers must not be able to import a
            # session belonging to another registered device.
            device_clause = " AND s.device_id = ?" if device_id is not None else ""
            query = f"""
                SELECT s.id, s.device_id, s.source_id, s.started_at, s.ended_at,
                       s.duration, s.highlight_count, s.query_count, s.note_count,
                       src.type as source_type, src.title as source_title,
                       src.url as source_url, src.canonical_url as source_canonical_url,
                       src.metadata as source_metadata,
                       d.device_name as device_name, d.device_type as device_type
                FROM reader_sessions s
                LEFT JOIN reader_sources src ON s.source_id = src.id
                LEFT JOIN devices d ON s.device_id = d.id
                WHERE s.id = ?{device_clause}
                """
            params: tuple[str, ...] = (session_id,)
            if device_id is not None:
                params += (device_id,)
            cursor = conn.execute(query, params)
            session_row = cursor.fetchone()
            if not session_row:
                return None

            session_data = dict(session_row)

            # Parse JSON fields
            if session_data.get("source_metadata"):
                session_data["source_metadata"] = json.loads(session_data["source_metadata"])

            # Get highlights
            cursor = conn.execute(
                """
                SELECT id, source_id, session_id, selected_text, context_before, context_after,
                       page, location, created_at, sync_state
                FROM reader_highlights
                WHERE session_id = ?
                ORDER BY created_at ASC
                """,
                (session_id,),
            )
            highlights = []
            for row in cursor.fetchall():
                highlight = dict(row)
                # Parse location if it's JSON string
                if highlight.get("location"):
                    try:
                        highlight["location"] = json.loads(highlight["location"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                highlights.append(highlight)

            # Get notes
            cursor = conn.execute(
                """
                SELECT id, source_id, highlight_id, session_id, content,
                       created_at, updated_at, revision, sync_state
                FROM reader_notes
                WHERE session_id = ?
                ORDER BY created_at ASC
                """,
                (session_id,),
            )
            notes = []
            for row in cursor.fetchall():
                note = dict(row)
                notes.append(note)

            # Get conversations with messages
            cursor = conn.execute(
                """
                SELECT id, source_id, created_at
                FROM reader_conversations
                WHERE source_id = ?
                  AND device_id = ?
                ORDER BY created_at ASC
                """,
                (session_data["source_id"], session_data["device_id"]),
            )
            conversations = []
            for conv_row in cursor.fetchall():
                conversation = dict(conv_row)

                # Get messages for this conversation
                msg_cursor = conn.execute(
                    """
                    SELECT id, role, content, model, created_at
                    FROM reader_messages
                    WHERE conversation_id = ?
                    ORDER BY created_at ASC
                    """,
                    (conversation["id"],),
                )
                conversation["messages"] = [dict(msg) for msg in msg_cursor.fetchall()]
                conversations.append(conversation)

            return {
                "session": session_data,
                "highlights": highlights,
                "notes": notes,
                "conversations": conversations,
            }
        finally:
            conn.close()
