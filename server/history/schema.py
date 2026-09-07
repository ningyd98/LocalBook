"""History table declarations for M7 (PLAN-M7 §5.5, M7-06) — single source.

Storage decision: History is derived data persisted as additive tables in the
M4 ``.localnote/index.db`` (``ai_jobs`` + ``ai_job_journal``).

This module is the *single declaration of record* for that DDL.  The runtime
open path wires through it explicitly:
:meth:`server.index.db.IndexDatabase._prepare` calls
``IndexDatabase._ensure_history_tables``, which delegates to
:func:`ensure_history_tables` here — so both fresh databases and migrated
M1–M6 databases carry the tables without changing any M4 table semantics or
the M4 schema version.  No other module carries a second copy of this DDL.

Deleting ``.localnote/`` wipes History with it — the UI and docs must warn
that Undo is unavailable once History is deleted.
"""

from __future__ import annotations

import sqlite3

AI_JOBS_TABLE = "ai_jobs"
AI_JOB_JOURNAL_TABLE = "ai_job_journal"
SCHEDULER_RUNS_TABLE = "scheduler_runs"

AI_JOBS_DDL = """
CREATE TABLE IF NOT EXISTS ai_jobs (
  job_id TEXT PRIMARY KEY,
  task_type TEXT NOT NULL,
  model TEXT,
  prompt_version TEXT,
  permission_level INTEGER NOT NULL DEFAULT 1,
  start_time TEXT NOT NULL,
  end_time TEXT,
  status TEXT NOT NULL,
  error_json TEXT,
  files_read_json TEXT NOT NULL DEFAULT '[]',
  proposed_actions_json TEXT NOT NULL DEFAULT '[]',
  executed_actions_json TEXT NOT NULL DEFAULT '[]',
  diff_json TEXT NOT NULL DEFAULT '[]',
  before_hash_json TEXT NOT NULL DEFAULT '{}',
  after_hash_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
)
"""

AI_JOBS_TIME_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_ai_jobs_time "
    "ON ai_jobs(start_time DESC, job_id DESC)"
)

AI_JOB_JOURNAL_DDL = """
CREATE TABLE IF NOT EXISTS ai_job_journal (
  job_id TEXT NOT NULL REFERENCES ai_jobs(job_id) ON DELETE CASCADE,
  seq INTEGER NOT NULL,
  operation TEXT NOT NULL,
  path TEXT NOT NULL,
  before_exists INTEGER NOT NULL,
  before_bytes_base64 TEXT,
  before_hash TEXT,
  after_exists INTEGER NOT NULL,
  after_hash TEXT,
  inverse_json TEXT NOT NULL,
  state TEXT NOT NULL,
  PRIMARY KEY (job_id, seq)
)
"""

# M8 scheduler audit runs (PLAN-M8 §5.4/§5.6). One row per trigger attempt:
# trigger source, run state, linked M7 agent job, scheduled slot and bounded
# error text.  Also derived data inside ``index.db``: deleting ``.localnote``
# wipes run history together with ai_jobs; Vault note bytes never live here.
SCHEDULER_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS scheduler_runs (
  run_id TEXT PRIMARY KEY,
  task TEXT NOT NULL,
  trigger TEXT NOT NULL,
  status TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  agent_job_id TEXT,
  scheduled_for TEXT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  error_code TEXT,
  message TEXT,
  created_at TEXT NOT NULL
)
"""

SCHEDULER_RUNS_TIME_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_scheduler_runs_time "
    "ON scheduler_runs(started_at DESC)"
)

SCHEDULER_RUNS_TASK_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_scheduler_runs_task "
    "ON scheduler_runs(task, started_at DESC)"
)

STATEMENTS: tuple[str, ...] = (
    AI_JOBS_DDL,
    AI_JOBS_TIME_INDEX,
    AI_JOB_JOURNAL_DDL,
    SCHEDULER_RUNS_DDL,
    SCHEDULER_RUNS_TIME_INDEX,
    SCHEDULER_RUNS_TASK_INDEX,
)


def ensure_history_tables(connection: sqlite3.Connection) -> None:
    """Create the additive M7 tables on an arbitrary SQLite connection.

    Idempotent; used by tests that open a raw connection without the M4
    derived-index lifecycle.
    """
    for statement in STATEMENTS:
        connection.execute(statement)


__all__ = [
    "AI_JOBS_TABLE",
    "AI_JOB_JOURNAL_TABLE",
    "SCHEDULER_RUNS_TABLE",
    "STATEMENTS",
    "ensure_history_tables",
]
