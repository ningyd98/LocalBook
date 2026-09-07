# server/scheduler — Local single-process scheduler (M8)

`server/scheduler` only decides **when** a trigger fires and records an
auditable run row.  Business behaviour stays behind the M7 chain
(`AgentJobService.plan`/`accept`, PolicyEngine, History, Recovery,
`VaultService`); the scheduler never writes notes, never holds prompts or
actions of its own and never bypasses Policy.

## Design invariants (PLAN-M8 §3/§5)

1. **Trigger ≠ business**: backends (`apscheduler_backend` preferred,
   `asyncio_backend` degraded fallback) own cron/interval scheduling with
   timezone-aware times, `max_instances=1`, `coalesce` and bounded
   `misfire_grace_time`.  APScheduler 3.11.x is the locked runtime dependency;
   when its import fails the process degrades to the pure-`zoneinfo` asyncio
   loop (no silent second scheduler, no auto-install).
2. **Registration is static**: `daily_organizer` (default `0 23 * * *`),
   `weekly_review` (default `0 20 * * 0`) and optional
   `index_consistency` (interval, off by default).  Cron/interval/timezone are
   validated strictly in `server/config.py`.
3. **Runs are audit rows**: `scheduler_runs` lives in the derived
   `.localnote/index.db` (see `server/history/schema.py`), recording
   trigger/status/error/started/finished/next-slot and the linked M7 job id.
   History retention deletes only terminal rows older than `retention_days`
   and keeps the newest run per task, active runs and recovery rows.
4. **Default is preview**: scheduled/manual Daily/Weekly runs issue a Level-1
   M7 preview (`awaiting_confirmation`, `run.status=previewed`).  Automatic
   execution happens only when all hold: server `level2_auto_enabled` +
   non-empty `level2_auto_actions` (tag-only), Policy engine `allow`, the
   returned set ⊆ the server whitelist — then the existing public `accept`
   entry executes (journal → VaultService → History).  A client `auto_level2`
   flag can never open the server switch.
5. **Crash safety is diagnostic**: startup scanning (`recovery/scanner.py`)
   only marks interrupted ai_jobs/scheduler_runs `recovery_required` with the
   fixed reason `process_interrupted`.  It never auto-writes, never auto-
   rolls back and never calls a model.  Explicit recovery endpoints default to
   `diagnose`; `rollback_if_safe` re-verifies every journal after-hash and
   refuses external changes.
6. **Stopping is safe**: `stop()` is bounded and idempotent, cancels queued
   work, flags leftovers, and never stops health/Vault/editor/AI/manual jobs.

## Module map

| module | purpose |
|---|---|
| `models.py` | `JobDefinition`, `SchedulerRun(Status)` DTOs (extra=forbid) |
| `errors.py` | stable `SchedulerErrorCode`s + typed errors (§6.2 of PLAN-M8) |
| `clock.py` | UTC timestamps + timezone conversion; injectable for tests |
| `cron.py` | pure next-fire math for the degraded fallback (strict subset) |
| `registry.py` | static daily/weekly/index definitions from settings |
| `backend.py` | `SchedulerBackend` protocol + `BaseBackend` |
| `apscheduler_backend.py` | `BackgroundScheduler` adapter (primary) |
| `asyncio_backend.py` | thread + `Event.wait` fallback (degraded) |
| `runner.py` | trigger → controlled M7 job chain adapter (Level1/Level2 rules) |
| `index_job.py` | read-only Vault↔index consistency check + optional derived rebuild |
| `service.py` | facade: lifecycle, gating, run records, watchdog, status/recovery |
| `locks.py` | per-task non-reentrant process locks (no distributed locking) |
| `cleanup.py` | interval-guarded History retention trigger |
| `service_types.py` | wire DTOs for `/scheduler/*` endpoints |

## Explicitly out of scope

No cloud/NAS sync, no Redis/Celery/Postgres/queue, no multi-process or
distributed locking (single uvicorn worker only — see README warnings), no
auth/HTTPS, no WebSocket, no user-defined Python/shell jobs, no automatic
body rewrites/deletes/attachment overwrites, no sleep/wake guarantees and no
automatic recovery of unknown conflicts.  Level 2 remains a tiny tag-only
whitelist that is additionally enforced by `PolicySettings`.

## Tests

`tests/backend/test_m8_*.py` (config, backend, runner, M7 integration,
recovery, retention, index consistency, API, isolation) use fake
clock/backend/adapters/Vault fixtures only — no real oMLX, no user Vault, no
wall-clock gates.
