"""M8 REST API tests (PLAN-M8 §8.1): status/run/runs/recovery endpoints,
stable error codes, LAN warning + CORS, no-leak error bodies."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from server.api.main import create_app
from server.config import AISettings as RealAISettings
from server.config import SchedulerSettings, ServerSettings, Settings, VaultSettings


def _client(
    vault_root: Path,
    *,
    scheduler: SchedulerSettings,
    host: str = "127.0.0.1",
    cors_origins: list[str] | None = None,
):
    server_kwargs = {"host": host, "port": 3780}
    if cors_origins is not None:
        server_kwargs["cors_origins"] = cors_origins
    settings = Settings(
        server=ServerSettings(**server_kwargs),
        vault=VaultSettings(root=vault_root, watcher_enabled=False),
        ai=RealAISettings(enabled=False, base_url=None),
        scheduler=scheduler,
    )
    app = create_app(settings=settings)
    return TestClient(app, raise_server_exceptions=False)


def _status_body(client: TestClient):
    response = client.get("/api/v1/scheduler/status")
    assert response.status_code == 200
    return response.json()


def test_scheduler_disabled_status_and_run_409(vault_fixture_copy: Path) -> None:
    settings_scheduler = SchedulerSettings(enabled=False)
    with _client(vault_fixture_copy, scheduler=settings_scheduler) as client:
        body = _status_body(client)
        assert body["enabled"] is False
        assert body["running"] is False
        assert body["network_exposure_warning"] is False
        run = client.post(
            "/api/v1/scheduler/run/daily_organizer", json={"confirm": True}
        )
        assert run.status_code == 409
        payload = run.json()
        assert payload["error"]["code"] == "scheduler_disabled"
        assert str(vault_fixture_copy.resolve()) not in run.text


def test_scheduler_status_enabled_with_jobs(vault_fixture_copy: Path) -> None:
    with _client(vault_fixture_copy, scheduler=SchedulerSettings()) as client:
        body = _status_body(client)
        assert body["enabled"] is True
        assert body["running"] is True
        assert body["backend"] in ("apscheduler", "asyncio")
        assert body["recovery_required"] == 0
        by_id = {job["id"]: job for job in body["jobs"]}
        assert by_id["daily_organizer"]["enabled"] is True
        assert by_id["daily_organizer"]["next_run_at"] is not None
        assert by_id["index_consistency"]["enabled"] is False
        assert by_id["index_consistency"]["next_run_at"] is None
        # core endpoints are unaffected by a running scheduler
        assert client.get("/api/v1/health").json() == {"status": "ok"}


def test_unknown_task_is_400_without_leaks(vault_fixture_copy: Path) -> None:
    with _client(vault_fixture_copy, scheduler=SchedulerSettings()) as client:
        response = client.post("/api/v1/scheduler/run/not_a_task", json={"confirm": True})
        assert response.status_code == 400
        payload = response.json()
        assert payload["error"]["code"] == "unknown_task"
        assert payload["meta"] == {"task": "not_a_task"}
        assert "Traceback" not in response.text


def test_manual_daily_run_fails_safely_when_ai_off(vault_fixture_copy: Path) -> None:
    with _client(vault_fixture_copy, scheduler=SchedulerSettings()) as client:
        run = client.post(
            "/api/v1/scheduler/run/daily_organizer",
            json={"confirm": True, "auto_level2": True},
        )
        assert run.status_code == 200
        payload = run.json()
        assert payload["task"] == "daily_organizer"
        assert payload["trigger"] == "manual"
        # AI is disabled in this test profile -> the run must stop at a safe
        # failed state, never write, never touch a remote model.
        assert payload["status"] == "failed"
        assert payload["error_code"] in ("ai_unavailable", "scheduler_unavailable")
        assert "run_id" in payload
        page = client.get("/api/v1/scheduler/runs?task=daily_organizer").json()
        assert page["total"] >= 1
        assert page["items"][0]["run_id"] == payload["run_id"]
        all_page = client.get("/api/v1/scheduler/runs?limit=50").json()
        assert all_page["total"] >= 1


def test_index_consistency_manual_run_commits(vault_fixture_copy: Path) -> None:
    with _client(vault_fixture_copy, scheduler=SchedulerSettings()) as client:
        run = client.post(
            "/api/v1/scheduler/run/index_consistency", json={"confirm": False}
        )
        assert run.status_code == 200
        payload = run.json()
        assert payload["task"] == "index_consistency"
        assert payload["status"] == "committed"
        assert payload["message"]
        # Vault untouched by the check
        files = client.get("/api/v1/vault/files?recursive=true").json()
        assert files["root"] == "."


def test_recovery_missing_run_is_404(vault_fixture_copy: Path) -> None:
    with _client(vault_fixture_copy, scheduler=SchedulerSettings()) as client:
        response = client.post(
            "/api/v1/scheduler/recovery/no-such-run",
            json={"action": "diagnose"},
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "job_not_found"


def test_lan_warning_flag_and_cors(vault_fixture_copy: Path) -> None:
    with _client(
        vault_fixture_copy,
        scheduler=SchedulerSettings(),
        host="0.0.0.0",
    ) as client:
        body = _status_body(client)
        assert body["network_exposure_warning"] is True
        # explicit CORS: trusted localhost origin preflight passes
        allowed = client.options(
            "/api/v1/scheduler/status",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert allowed.headers.get("access-control-allow-origin") == "http://127.0.0.1:5173"
        # unknown origin is not reflected
        denied = client.options(
            "/api/v1/scheduler/status",
            headers={
                "Origin": "http://evil.example",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert denied.headers.get("access-control-allow-origin") is None


def test_error_bodies_never_leak_roots_or_stacks(vault_fixture_copy: Path) -> None:
    with _client(vault_fixture_copy, scheduler=SchedulerSettings()) as client:
        response = client.get("/api/v1/scheduler/runs?task=")  # empty task filter OK
        assert response.status_code == 200
        unknown = client.post("/api/v1/scheduler/run/bogus", json={"confirm": True})
        assert "vault_fixture_copy" not in unknown.text
        assert str(vault_fixture_copy.resolve()) not in unknown.text
        assert "File \"" not in unknown.text


def test_lan_warning_and_cors_advice_for_non_loopback_hosts(
    vault_fixture_copy: Path,
) -> None:
    """I2: any non-loopback host warns; when the CORS allow-list cannot serve
    a LAN client the status carries the fixed advice text."""
    # LAN IP with loopback-only CORS -> warning + fixed advice
    with _client(
        vault_fixture_copy, scheduler=SchedulerSettings(), host="192.168.1.5"
    ) as client:
        body = _status_body(client)
        assert body["network_exposure_warning"] is True
        assert "LOCALNOTE_SERVER__CORS_ORIGINS" in (body["network_exposure_advice"] or "")
    # explicit LAN CORS whitelist -> warning stays, advice disappears
    with _client(
        vault_fixture_copy,
        scheduler=SchedulerSettings(),
        host="0.0.0.0",
        cors_origins=["http://192.168.1.9:5173"],
    ) as client:
        body = _status_body(client)
        assert body["network_exposure_warning"] is True
        assert body["network_exposure_advice"] is None
        allowed = client.options(
            "/api/v1/scheduler/status",
            headers={
                "Origin": "http://192.168.1.9:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert (
            allowed.headers.get("access-control-allow-origin")
            == "http://192.168.1.9:5173"
        )
    # loopback default stays quiet
    with _client(vault_fixture_copy, scheduler=SchedulerSettings()) as client:
        body = _status_body(client)
        assert body["network_exposure_warning"] is False
        assert body.get("network_exposure_advice") is None
