"""M8 isolation tests (PLAN-M8 §8.1): stopping the scheduler never affects
health/Vault/editor/search/graph/AI/manual jobs; no real oMLX/user Vault."""

from __future__ import annotations

from pathlib import Path

from tests.backend.client import TestClient

from server.api.main import create_app
from server.config import AISettings as RealAISettings
from server.config import SchedulerSettings, Settings, VaultSettings


def _client(vault_root: Path) -> TestClient:
    settings = Settings(
        vault=VaultSettings(root=vault_root, watcher_enabled=False),
        ai=RealAISettings(enabled=False, base_url=None),
        scheduler=SchedulerSettings(enabled=True),
    )
    return TestClient(create_app(settings=settings), raise_server_exceptions=False)


def _seeded_manual_job_body() -> dict:
    return {
        "task_type": "manual",
        "permission_level": 1,
        "scope": {"paths": ["notes/Alpha.md"]},
        "actions": [
            {
                "action": "add_tags",
                "file": "notes/Alpha.md",
                "tags": ["manual-test"],
                "reason": "isolation test",
            }
        ],
        "execute": False,
    }


def test_stopping_scheduler_keeps_core_apis_alive(vault_fixture_copy: Path) -> None:
    with _client(vault_fixture_copy) as client:
        status = client.get("/api/v1/scheduler/status").json()
        assert status["running"] is True

        scheduler = client.app.state.scheduler_service
        scheduler.stop(wait=True, timeout=2.0)
        assert scheduler.started is False

        # scheduler endpoints report a safe, non-crashing state
        after = client.get("/api/v1/scheduler/status").json()
        assert after["running"] is False
        run = client.post(
            "/api/v1/scheduler/run/daily_organizer", json={"confirm": True}
        )
        assert run.status_code == 503
        assert run.json()["error"]["code"] == "scheduler_unavailable"

        # core capabilities keep working after the stop
        assert client.get("/api/v1/health").json() == {"status": "ok"}
        files = client.get("/api/v1/vault/files?recursive=true").json()
        assert any(entry["path"] == "notes/Alpha.md" for entry in files["entries"])
        read = client.get(
            "/api/v1/vault/file", params={"path": "notes/Alpha.md"}
        )
        assert read.status_code == 200
        # manual M7 job still previews (seeded actions -> no AI needed, no write)
        preview = client.post("/api/v1/jobs", json=_seeded_manual_job_body())
        assert preview.status_code == 200
        assert preview.json()["status"] == "awaiting_confirmation"
        assert preview.json()["policy"]["decision"] == "confirm"
        # index-backed reads keep working
        assert client.get("/api/v1/search", params={"q": "Alpha"}).status_code == 200
        metadata = client.get("/api/v1/metadata/notes/Alpha.md")
        assert metadata.status_code == 200
        ai = client.get("/api/v1/ai/status")
        assert ai.status_code == 200
        # scheduler stop is idempotent and does not poison lifespan shutdown
        scheduler.stop(wait=True, timeout=1.0)


def test_scheduler_stop_leaves_vault_bytes_untouched(vault_fixture_copy: Path) -> None:
    target = vault_fixture_copy / "notes" / "Alpha.md"
    before = target.read_bytes()
    with _client(vault_fixture_copy) as client:
        client.app.state.scheduler_service.stop(wait=True, timeout=2.0)
        assert client.get("/api/v1/health").status_code == 200
    assert target.read_bytes() == before


def test_manual_jobs_do_not_enter_scheduler_records(vault_fixture_copy: Path) -> None:
    with _client(vault_fixture_copy) as client:
        preview = client.post("/api/v1/jobs", json=_seeded_manual_job_body())
        assert preview.status_code == 200
        scheduler_runs = client.get("/api/v1/scheduler/runs").json()
        # plain /jobs flow is orthogonal to scheduler run records
        assert scheduler_runs["total"] == 0
