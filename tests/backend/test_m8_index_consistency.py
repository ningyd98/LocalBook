"""M8 index consistency tests (PLAN-M8 §8.1): consistent / mismatch / degraded,
optional rebuild (derived only) and run outcome mapping."""

from __future__ import annotations

import asyncio
from pathlib import Path

import m7support as m7

from server.index.service import DerivedIndexService
from server.scheduler.index_job import IndexConsistencyChecker, to_message
from server.scheduler.models import SchedulerRunStatus
from server.scheduler.runner import JobRunner

_NOTES = {"notes/a.md": b"# A\n\nbody\n", "notes/b.md": b"# B\n"}


def _index(tmp_path: Path):
    vault = m7.make_vault(tmp_path, dict(_NOTES))
    index = DerivedIndexService(vault)
    assert index.rebuild().ready is True
    return vault, index


def test_consistent_index_reports_ready(tmp_path: Path) -> None:
    vault, index = _index(tmp_path)
    checker = IndexConsistencyChecker(vault, index, auto_rebuild=False)
    result = checker.check()
    assert result.degraded is False
    assert result.ready is True
    assert result.checked == 2
    assert result.mismatches == []
    assert "consistent" in to_message(result)


def test_hash_mismatch_detected(tmp_path: Path) -> None:
    vault, index = _index(tmp_path)
    _data, digest = vault.read_bytes("notes/a.md")
    # Modify the note behind the index's back (no watcher event).
    vault.write_bytes("notes/a.md", b"# A\n\nchanged\n", expected_sha256=digest)
    checker = IndexConsistencyChecker(vault, index, auto_rebuild=False)
    result = checker.check()
    assert result.degraded is False
    assert result.ready is False
    assert any(item.startswith("hash:notes/a.md") for item in result.mismatches)


def test_missing_and_stale_entries_detected(tmp_path: Path) -> None:
    vault, index = _index(tmp_path)
    # a new file exists on disk but never indexed
    vault.create_bytes("notes/new.md", b"# New\n")
    # a note is deleted on disk but still present in the derived index
    data, digest = vault.read_bytes("notes/b.md")
    vault.delete_file("notes/b.md", expected_sha256=digest)
    checker = IndexConsistencyChecker(vault, index, auto_rebuild=False)
    result = checker.check()
    assert result.ready is False
    kinds = {item.split(":", 1)[0] for item in result.mismatches}
    assert kinds == {"missing", "stale"}


def test_unavailable_index_is_degraded_not_crashing(tmp_path: Path) -> None:
    vault, index = _index(tmp_path)
    index.close()
    # emulate the M4 "startup scan failed" state (index stays queryable=False)
    index._set_unavailable("simulated scan failure")  # type: ignore[attr-defined]
    checker = IndexConsistencyChecker(vault, index, auto_rebuild=False)
    result = checker.check()
    assert result.degraded is True
    assert result.error is not None
    # the checker never raises even when pieces are missing
    empty = IndexConsistencyChecker(None, None)
    assert empty.check().degraded is True


def test_auto_rebuild_fixes_mismatch_derived_only(tmp_path: Path) -> None:
    vault, index = _index(tmp_path)
    _data, digest = vault.read_bytes("notes/a.md")
    vault.write_bytes("notes/a.md", b"# A\n\nchanged\n", expected_sha256=digest)
    changed_content = vault.read_bytes("notes/a.md")[0]
    checker = IndexConsistencyChecker(vault, index, auto_rebuild=True)
    result = checker.check()
    assert result.rebuilt is True
    # derived index now matches the vault again
    assert index.note_count() == 2
    after = checker.check()
    assert after.ready is True and after.mismatches == []
    # note bytes were never touched by the rebuild
    assert vault.read_bytes("notes/a.md")[0] == changed_content
    assert vault.read_bytes("notes/b.md")[0] == b"# B\n"


def test_runner_index_outcome_mapping(tmp_path: Path) -> None:
    vault, index = _index(tmp_path)
    runner = JobRunner(None, index_checker=IndexConsistencyChecker(vault, index))
    outcome = asyncio.run(runner.arun("index_consistency"))
    assert outcome.status == SchedulerRunStatus.COMMITTED
    assert outcome.detail is not None and outcome.detail["checked"] == 2
