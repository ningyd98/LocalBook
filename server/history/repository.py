"""Parameterized SQLite CRUD for M7 History (PLAN-M7 §5.5, M7-06).

Every statement is parameterized and executed on the shared
:class:`server.index.db.IndexDatabase` connection inside its transaction
context; no SQL is built from user input.
"""

from __future__ import annotations

import json
from typing import Any

from server.index.db import IndexDatabase

from .schemas import HistoryRecord, JournalEntry

_JSON_COLUMNS: tuple[tuple[str, str, Any], ...] = (
    ("error_json", "error", None),
    ("files_read_json", "files_read", []),
    ("proposed_actions_json", "proposed_actions", []),
    ("executed_actions_json", "executed_actions", []),
    ("diff_json", "diff", []),
    ("before_hash_json", "before_hash", {}),
    ("after_hash_json", "after_hash", {}),
)

_ALL_COLUMNS = (
    "job_id",
    "task_type",
    "model",
    "prompt_version",
    "permission_level",
    "start_time",
    "end_time",
    "status",
    "error_json",
    "files_read_json",
    "proposed_actions_json",
    "executed_actions_json",
    "diff_json",
    "before_hash_json",
    "after_hash_json",
    "created_at",
)


def _dump(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


class HistoryRepository:
    """One repository over one derived ``IndexDatabase``."""

    def __init__(self, db: IndexDatabase) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # ai_jobs
    # ------------------------------------------------------------------

    def save(self, record: HistoryRecord) -> None:
        data = record.model_dump()
        values = [
            data["job_id"],
            data["task_type"],
            data["model"],
            data["prompt_version"],
            data["permission_level"],
            data["start_time"],
            data["end_time"],
            data["status"],
            _dump(data["error"]),
            _dump(data["files_read"]),
            _dump(data["proposed_actions"]),
            _dump(data["executed_actions"]),
            _dump(data["diff"]),
            _dump(data["before_hash"]),
            _dump(data["after_hash"]),
            data["created_at"],
        ]
        # Upsert (never DELETE+INSERT): INSERT OR REPLACE would cascade-delete
        # the ai_job_journal rows (FK ON DELETE CASCADE), destroying Undo data.
        assignments = ", ".join(
            f"{column}=excluded.{column}" for column in _ALL_COLUMNS if column != "job_id"
        )
        with self.db.transaction() as connection:
            connection.execute(
                f"INSERT INTO ai_jobs ({','.join(_ALL_COLUMNS)}) "
                f"VALUES ({','.join('?' for _ in _ALL_COLUMNS)}) "
                f"ON CONFLICT(job_id) DO UPDATE SET {assignments}",
                values,
            )

    def get(self, job_id: str) -> HistoryRecord | None:
        row = self.db.fetchone("SELECT * FROM ai_jobs WHERE job_id=?", (job_id,))
        return self._record_from_row(row) if row is not None else None

    def page(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        status: str | None = None,
    ) -> tuple[list[HistoryRecord], int]:
        """Return (records ordered by start_time DESC, job_id DESC, total)."""
        safe_limit = max(1, min(int(limit), 100))
        safe_offset = max(0, int(offset))
        where = ""
        params: list[Any] = []
        if status:
            where = " WHERE status=?"
            params.append(status)
        total_row = self.db.fetchone(
            f"SELECT COUNT(*) AS n FROM ai_jobs{where}", tuple(params)
        )
        total = int(total_row["n"]) if total_row is not None else 0
        rows = self.db.fetchall(
            f"SELECT * FROM ai_jobs{where} ORDER BY start_time DESC, job_id DESC "
            "LIMIT ? OFFSET ?",
            (*params, safe_limit, safe_offset),
        )
        records = [record for row in rows if (record := self._record_from_row(row)) is not None]
        return records, total

    def delete(self, job_id: str) -> bool:
        with self.db.transaction() as connection:
            cursor = connection.execute("DELETE FROM ai_jobs WHERE job_id=?", (job_id,))
            return cursor.rowcount > 0

    @staticmethod
    def _record_from_row(row: Any) -> HistoryRecord | None:
        data = dict(row)
        for column, field, default in _JSON_COLUMNS:
            raw = data.pop(column)
            data[field] = json.loads(raw) if raw else default
        try:
            return HistoryRecord.model_validate(data)
        except Exception:  # pragma: no cover - defensive against corrupt rows
            return None

    # ------------------------------------------------------------------
    # ai_job_journal
    # ------------------------------------------------------------------

    def add_journal(self, entry: JournalEntry) -> None:
        data = entry.model_dump()
        with self.db.transaction() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO ai_job_journal "
                "(job_id, seq, operation, path, before_exists, before_bytes_base64, "
                "before_hash, after_exists, after_hash, inverse_json, state) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    data["job_id"],
                    data["seq"],
                    data["operation"],
                    data["path"],
                    int(data["before_exists"]),
                    data["before_bytes_base64"],
                    data["before_hash"],
                    int(data["after_exists"]),
                    data["after_hash"],
                    _dump(data["inverse"]),
                    data["state"],
                ),
            )

    def journal(self, job_id: str) -> list[JournalEntry]:
        rows = self.db.fetchall(
            "SELECT * FROM ai_job_journal WHERE job_id=? ORDER BY seq",
            (job_id,),
        )
        entries: list[JournalEntry] = []
        for row in rows:
            data = dict(row)
            raw_inverse = data.pop("inverse_json", None)
            data["before_exists"] = bool(data["before_exists"])
            data["after_exists"] = bool(data["after_exists"])
            data["inverse"] = json.loads(raw_inverse) if raw_inverse else {}
            entries.append(JournalEntry.model_validate(data))
        return entries

    def set_journal_state(self, job_id: str, state: str) -> None:
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE ai_job_journal SET state=? WHERE job_id=?", (state, job_id)
            )

    # ------------------------------------------------------------------
    # scheduler_runs (M8; derived rows inside index.db)
    # ------------------------------------------------------------------

    def save_run(self, row: dict[str, object]) -> None:
        columns = (
            "run_id",
            "task",
            "trigger",
            "status",
            "idempotency_key",
            "agent_job_id",
            "scheduled_for",
            "started_at",
            "finished_at",
            "error_code",
            "message",
            "created_at",
        )
        assignments = ", ".join(
            f"{column}=excluded.{column}" for column in columns if column != "run_id"
        )
        values = [row.get(column) for column in columns]
        with self.db.transaction() as connection:
            connection.execute(
                f"INSERT INTO scheduler_runs ({','.join(columns)}) "
                f"VALUES ({','.join('?' for _ in columns)}) "
                f"ON CONFLICT(run_id) DO UPDATE SET {assignments}",
                values,
            )

    def get_run(self, run_id: str) -> dict[str, object] | None:
        row = self.db.fetchone(
            "SELECT * FROM scheduler_runs WHERE run_id=?", (run_id,)
        )
        return dict(row) if row is not None else None

    def page_runs(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        task: str | None = None,
    ) -> tuple[list[dict[str, object]], int]:
        safe_limit = max(1, min(int(limit), 100))
        safe_offset = max(0, int(offset))
        where = ""
        params: list[object] = []
        if task:
            where = " WHERE task=?"
            params.append(task)
        total_row = self.db.fetchone(
            f"SELECT COUNT(*) AS n FROM scheduler_runs{where}", tuple(params)
        )
        total = int(total_row["n"]) if total_row is not None else 0
        rows = self.db.fetchall(
            f"SELECT * FROM scheduler_runs{where} "
            "ORDER BY started_at DESC, run_id DESC LIMIT ? OFFSET ?",
            (*params, safe_limit, safe_offset),
        )
        return [dict(row) for row in rows], total

    def runs_with_status(self, statuses: tuple[str, ...]) -> list[dict[str, object]]:
        if not statuses:
            return []
        placeholders = ",".join("?" for _ in statuses)
        rows = self.db.fetchall(
            f"SELECT * FROM scheduler_runs WHERE status IN ({placeholders}) "
            "ORDER BY started_at",
            statuses,
        )
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # M8 retention + recovery diagnostics (derived data only)
    # ------------------------------------------------------------------

    def job_ids_with_statuses(self, statuses: tuple[str, ...]) -> list[dict[str, object]]:
        if not statuses:
            return []
        placeholders = ",".join("?" for _ in statuses)
        rows = self.db.fetchall(
            f"SELECT job_id, status, end_time FROM ai_jobs WHERE status IN ({placeholders}) "
            "ORDER BY start_time",
            statuses,
        )
        return [dict(row) for row in rows]

    def delete_jobs(self, job_ids: list[str]) -> int:
        """Delete ai_jobs rows; journal rows cascade via FK ON DELETE CASCADE."""
        if not job_ids:
            return 0
        deleted = 0
        with self.db.transaction() as connection:
            for job_id in job_ids:
                cursor = connection.execute("DELETE FROM ai_jobs WHERE job_id=?", (job_id,))
                deleted += cursor.rowcount
        return deleted

    def delete_runs(self, run_ids: list[str]) -> int:
        if not run_ids:
            return 0
        deleted = 0
        with self.db.transaction() as connection:
            for run_id in run_ids:
                cursor = connection.execute(
                    "DELETE FROM scheduler_runs WHERE run_id=?", (run_id,)
                )
                deleted += cursor.rowcount
        return deleted

    def run_count(self) -> int:
        row = self.db.fetchone("SELECT COUNT(*) AS n FROM scheduler_runs")
        return int(row["n"]) if row is not None else 0

    def terminal_job_rows(
        self, statuses: tuple[str, ...], cutoff_iso: str
    ) -> list[dict[str, object]]:
        """Terminal ai_jobs whose (end_time, start_time) bound is older."""
        placeholders = ",".join("?" for _ in statuses)
        rows = self.db.fetchall(
            f"SELECT job_id, status, start_time, end_time FROM ai_jobs "
            f"WHERE status IN ({placeholders}) "
            "AND COALESCE(end_time, start_time) < ? ORDER BY start_time",
            (*statuses, cutoff_iso),
        )
        return [dict(row) for row in rows]

    def terminal_run_rows(
        self, statuses: tuple[str, ...], cutoff_iso: str
    ) -> list[dict[str, object]]:
        placeholders = ",".join("?" for _ in statuses)
        rows = self.db.fetchall(
            f"SELECT run_id, task, status, started_at, finished_at FROM scheduler_runs "
            f"WHERE status IN ({placeholders}) "
            "AND COALESCE(finished_at, started_at) < ? ORDER BY started_at",
            (*statuses, cutoff_iso),
        )
        return [dict(row) for row in rows]

    def oldest_terminal_runs(
        self, statuses: tuple[str, ...], count: int
    ) -> list[dict[str, object]]:
        placeholders = ",".join("?" for _ in statuses)
        rows = self.db.fetchall(
            f"SELECT run_id, task, status, started_at, finished_at FROM scheduler_runs "
            f"WHERE status IN ({placeholders}) ORDER BY started_at ASC LIMIT ?",
            (*statuses, int(count)),
        )
        return [dict(row) for row in rows]

    def all_runs_sorted(self) -> list[dict[str, object]]:
        rows = self.db.fetchall(
            "SELECT run_id, task, status, started_at, finished_at "
            "FROM scheduler_runs ORDER BY started_at DESC"
        )
        return [dict(row) for row in rows]


__all__ = ["HistoryRepository"]
