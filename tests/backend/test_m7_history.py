"""M7 History tests (PLAN-M7 §9.1-8): fields, paging, caps, rebuild safety."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from server.actions.schemas import ActionType
from server.history.schemas import HistoryRecord, JournalEntry, redact_secrets

import m7support as m7


def _record(**overrides: object) -> HistoryRecord:
    payload: dict[str, object] = {
        "job_id": "job-1",
        "task_type": "daily_organizer",
        "status": "awaiting_confirmation",
        "permission_level": 1,
        "model": "mock-model",
        "prompt_version": "daily_organizer@m7.1",
        "files_read": ["notes/a.md"],
        "proposed_actions": [
            {"action": "add_tags", "file": "notes/a.md", "tags": ["x"], "reason": "r"}
        ],
    }
    payload.update(overrides)
    return HistoryRecord(**payload)


def test_record_roundtrip_all_fields(tmp_path: Path) -> None:
    history = m7.make_history(tmp_path)
    record = _record(
        status="committed",
        end_time="2026-01-01T00:00:00+00:00",
        error={"code": "transaction_failed", "message": "boom"},
        before_hash={"notes/a.md": "sha256:" + "a" * 64},
        after_hash={"notes/a.md": "sha256:" + "b" * 64},
    )
    history.save(record)
    loaded = history.get("job-1")
    assert loaded is not None
    assert loaded.job_id == "job-1"
    assert loaded.status == "committed"
    assert loaded.error == {"code": "transaction_failed", "message": "boom"}
    assert loaded.before_hash == record.before_hash
    assert loaded.after_hash == record.after_hash
    assert loaded.end_time == "2026-01-01T00:00:00+00:00"


def test_secret_redaction_in_error_and_journal_fields() -> None:
    cleaned = redact_secrets(
        "Authorization: Bearer sekrit-token-12345 and token: abcdefghijklmnop"
    )
    assert "sekrit-token-12345" not in cleaned
    assert "abcdefghijklmnop" not in cleaned
    record = _record(
        error={
            "code": "policy_denied",
            "message": "Authorization: Bearer sekrit-token-12345 leaked?",
        }
    )
    saved = record.model_dump()
    assert "sekrit-token-12345" not in str(saved["error"])


def test_record_caps_reject_oversized_payloads() -> None:
    with pytest.raises(ValidationError):
        _record(
            proposed_actions=[
                {"action": "add_tags", "file": "f.md", "tags": ["t"], "reason": "r"}
            ]
            * 21
        )
    with pytest.raises(ValidationError):
        _record(
            diff=[{"path": "a.md", "operation": "update", "before_hash": None, "after_hash": None}]
            * 21
        )
    with pytest.raises(ValidationError):
        JournalEntry(
            job_id="j",
            seq=0,
            operation="update",
            path="a.md",
            before_exists=True,
            before_bytes_base64="x" * 14_000_001,  # exceeds journal cap
            before_hash=None,
            after_exists=True,
            after_hash=None,
            inverse={},
        )


def test_page_pagination_and_status_filter(tmp_path: Path) -> None:
    history = m7.make_history(tmp_path)
    for index in range(5):
        history.save(
            _record(
                job_id="job-%d" % index,
                status="committed" if index % 2 else "awaiting_confirmation",
            )
        )
    page = history.page(limit=2, offset=0)
    assert page.total == 5 and len(page.items) == 2
    committed = history.page(limit=20, status="committed")
    assert committed.total == 2
    assert all(item.status == "committed" for item in committed.items)
    # offset beyond the end
    assert history.page(limit=10, offset=100).items == []
    # limits clamped to [1..100]
    assert history.page(limit=500).limit == 100


def test_extra_fields_forbidden_on_history_records() -> None:
    with pytest.raises(ValidationError):
        HistoryRecord(
            job_id="j",
            task_type="manual",
            status="planned",
            prompt_text="the whole prompt must never be stored",
        )


def test_delete_cascades_journal(tmp_path: Path) -> None:
    history = m7.make_history(tmp_path)
    record = _record()
    history.save(record)
    history.append_journal(
        JournalEntry(
            job_id="job-1",
            seq=0,
            operation="update",
            path="notes/a.md",
            before_exists=True,
            before_bytes_base64=None,
            before_hash="sha256:" + "a" * 64,
            after_exists=True,
            after_hash="sha256:" + "b" * 64,
            inverse={"kind": "write_before"},
        )
    )
    assert len(history.journal("job-1")) == 1
    assert history.repository is not None and history.repository.delete("job-1")
    assert history.journal("job-1") == []


def test_index_rebuild_does_not_wipe_history(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    vault = m7.make_vault(root, {"notes/a.md": b"# A\n\nbody\n"})
    history = m7.make_history(root)
    record = _record(job_id="keep-me", status="committed")
    history.save(record)
    history.append_journal(
        JournalEntry(
            job_id="keep-me",
            seq=0,
            operation="update",
            path="notes/a.md",
            before_exists=True,
            before_bytes_base64=None,
            before_hash="sha256:" + "a" * 64,
            after_exists=True,
            after_hash="sha256:" + "b" * 64,
            inverse={"kind": "write_before"},
        )
    )
    from server.index.service import DerivedIndexService

    index = DerivedIndexService(vault)
    result = index.rebuild()
    assert result.ready is True
    loaded = history.get("keep-me")
    assert loaded is not None and loaded.status == "committed"
    assert len(history.journal("keep-me")) == 1


def test_summary_and_detail_dto_shapes(tmp_path: Path) -> None:
    history = m7.make_history(tmp_path)
    record = _record(status="committed", executed_actions=[{"action": "add_tags"}])
    history.save(record)
    summary = history.summary_dto(record)
    assert summary["job_id"] == "job-1"
    assert summary["status"] == "committed"
    assert summary["action_count"] == 1
    detail = history.detail_dto(record)
    assert detail["files_read"] == ["notes/a.md"]
    assert detail["proposed_actions"][0]["action"] == "add_tags"
