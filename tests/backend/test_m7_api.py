"""M7 REST API tests (PLAN-M7 §9.1-11): error contract and full flows."""

from __future__ import annotations

from fastapi.testclient import TestClient

from server.actions.schemas import ActionType
from server.agents.schemas import JobRequest
from server.api import dependencies
from server.api.main import create_app
from server.ai.adapters.base import ChatResult
from server.ai.schemas import AICapabilities, DiscoveredModel

import m7support as m7

GOOD = (
    '{"actions":[{"action":"add_tags","file":"notes/a.md","tags":["inbox"],'
    '"reason":"tidy"}],"task_type":"daily_organizer"}'
)


class FakeAdapter:
    def __init__(self, content: str = GOOD) -> None:
        self.content = content
        self.chat_calls = 0

    async def list_models(self):
        return [DiscoveredModel(id="mock-qwen", capabilities=AICapabilities(chat=True))]

    async def chat(self, **kwargs) -> ChatResult:
        self.chat_calls += 1
        return ChatResult(content=self.content, model="mock-qwen")


def _client(tmp_path, *, adapter=None, history=None, policy=None, max_journal_bytes=10_000_000):
    root = tmp_path / "vault"
    vault = m7.make_vault(
        root, {"notes/a.md": b"---\ntags:\n  - work\n---\n# A\n\nbody\n"}
    )
    history = history or m7.make_history(tmp_path)
    service = m7.make_service(
        vault,
        history=history,
        adapter=adapter,
        policy=policy,
        default_model="mock-qwen",
        max_journal_bytes=max_journal_bytes,
    )
    app = create_app()
    app.dependency_overrides[dependencies.get_agent_job_service] = lambda: service
    return TestClient(app, raise_server_exceptions=False), service


def _tag_body() -> dict:
    return {
        "task_type": "manual",
        "permission_level": 1,
        "actions": [
            {
                "action": "add_tags",
                "file": "notes/a.md",
                "tags": ["inbox"],
                "reason": "api test",
            }
        ],
        "execute": False,
    }


def test_post_jobs_preview_returns_awaiting_and_error_shape(
    tmp_path,
) -> None:
    client, service = _client(tmp_path)
    response = client.post("/api/v1/jobs", json=_tag_body())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "awaiting_confirmation"
    assert body["policy"]["decision"] == "confirm"
    assert body["diff"][0]["status"] == "proposed"
    # preview never wrote
    assert b"inbox" not in service.vault.read_bytes("notes/a.md")[0]


def test_accept_without_confirmation_is_409(tmp_path) -> None:
    client, service = _client(tmp_path)
    job_id = client.post("/api/v1/jobs", json=_tag_body()).json()["job_id"]
    response = client.post(f"/api/v1/jobs/{job_id}/accept", json={"confirm": False})
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "confirmation_required"
    assert body["meta"] == {}


def test_full_accept_history_and_undo_flow(tmp_path) -> None:
    client, service = _client(tmp_path)
    job = client.post("/api/v1/jobs", json=_tag_body()).json()
    job_id = job["job_id"]
    accepted = client.post(
        f"/api/v1/jobs/{job_id}/accept", json={"confirm": True, "action_ids": []}
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "committed"
    page = client.get("/api/v1/history").json()
    assert page["total"] >= 1
    assert any(item["job_id"] == job_id for item in page["items"])
    detail = client.get(f"/api/v1/history/{job_id}")
    assert detail.status_code == 200
    assert detail.json()["diff"][0]["status"] == "accepted"
    undone = client.post(f"/api/v1/history/{job_id}/undo", json={"confirm": True})
    assert undone.status_code == 200
    assert undone.json()["status"] == "undone"
    again = client.post(f"/api/v1/history/{job_id}/undo", json={"confirm": True})
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "undo_unavailable"


def test_unknown_job_is_404(tmp_path) -> None:
    client, _ = _client(tmp_path)
    response = client.get("/api/v1/history/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "job_not_found"
    accept = client.post("/api/v1/jobs/nope/accept", json={"confirm": True})
    assert accept.status_code == 404 and accept.json()["error"]["code"] == "job_not_found"


def test_policy_denied_is_403_and_recorded(tmp_path) -> None:
    client, service = _client(tmp_path)
    body = _tag_body()
    body["actions"] = [
        {
            "action": "create_note",
            "file": "notes/a.md",  # already exists -> create_existing deny
            "content_base64": "IyBvdmVyd3JpdGU=",
            "reason": "api test",
        }
    ]
    response = client.post("/api/v1/jobs", json=body)
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "policy_denied"
    assert "meta" in response.json()
    page = client.get("/api/v1/history").json()
    assert any(item["status"] == "failed" for item in page["items"])


def test_stale_accept_is_409(tmp_path) -> None:
    client, service = _client(tmp_path)
    job_id = client.post("/api/v1/jobs", json=_tag_body()).json()["job_id"]
    # external edit
    data, digest = service.vault.read_bytes("notes/a.md")
    service.vault.write_bytes("notes/a.md", data + b"\nedited\n", digest)
    response = client.post(f"/api/v1/jobs/{job_id}/accept", json={"confirm": True})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "job_state_conflict"


def test_validation_and_invalid_action_errors(tmp_path) -> None:
    client, service = _client(tmp_path)
    # extra field -> 422 invalid_request via FastAPI validation
    bad = _tag_body()
    bad["actions"][0]["unexpected"] = True
    assert client.post("/api/v1/jobs", json=bad).status_code == 422
    # unknown wire action
    bad2 = _tag_body()
    bad2["actions"][0]["action"] = "delete_note"
    assert client.post("/api/v1/jobs", json=bad2).status_code == 422
    # manual job without actions -> 400 invalid_action
    manual = {"task_type": "manual", "permission_level": 1, "actions": []}
    response = client.post("/api/v1/jobs", json=manual)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_action"
    # unknown action id on accept -> 400
    job_id = client.post("/api/v1/jobs", json=_tag_body()).json()["job_id"]
    wrong_id = client.post(
        f"/api/v1/jobs/{job_id}/accept", json={"confirm": True, "action_ids": ["x"]}
    )
    assert wrong_id.status_code == 400
    assert wrong_id.json()["error"]["code"] == "invalid_action"


def test_daily_organizer_single_model_call_and_502_garbage(tmp_path) -> None:
    adapter = FakeAdapter()
    client, service = _client(tmp_path, adapter=adapter)
    request = {
        "task_type": "daily_organizer",
        "permission_level": 1,
        "scope": {"paths": ["notes/a.md"], "max_files": 5},
        "execute": False,
    }
    response = client.post("/api/v1/jobs", json=request)
    assert response.status_code == 200, response.text
    assert adapter.chat_calls == 1
    # garbage output maps to 502 invalid_action_output
    adapter.content = "i will add tags everywhere now"
    response = client.post("/api/v1/jobs", json=request)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "invalid_action_output"


def test_journal_cap_is_413(tmp_path) -> None:
    client, service = _client(tmp_path, max_journal_bytes=4)
    job_id = client.post("/api/v1/jobs", json=_tag_body()).json()["job_id"]
    response = client.post(f"/api/v1/jobs/{job_id}/accept", json={"confirm": True})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "history_limit_exceeded"


def test_level2_default_closed_and_open_with_config(tmp_path) -> None:
    # default engine: level 2 closed -> 403
    client, service = _client(tmp_path)
    body = _tag_body()
    body["permission_level"] = 2
    assert client.post("/api/v1/jobs", json=body).status_code == 403
    # level-2 auto configured + execute -> commits without accept
    from server.actions.schemas import ActionType
    from server.policies.engine import PolicyEngine

    policy = m7.make_policy(level2_auto=(ActionType.ADD_TAGS,))
    client2, service2 = _client(tmp_path, policy=policy)
    body["execute"] = True
    response = client2.post("/api/v1/jobs", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "committed"
    assert b"inbox" in service2.vault.read_bytes("notes/a.md")[0]


def test_errors_never_leak_roots_or_tracebacks(tmp_path) -> None:
    client, _ = _client(tmp_path)
    response = client.post("/api/v1/jobs", json=_tag_body())
    text = response.text
    assert str(tmp_path) not in text
    assert "Traceback" not in text
    for error_response in (
        client.post("/api/v1/jobs/nope/accept", json={"confirm": True}),
        client.get("/api/v1/history/nope"),
    ):
        assert "Traceback" not in error_response.text
        assert str(tmp_path) not in error_response.text
        assert set(error_response.json()) == {"error", "meta"}


def test_error_body_shape_contract(tmp_path) -> None:
    client, _ = _client(tmp_path)
    response = client.post("/api/v1/jobs", json={"task_type": "manual", "permission_level": 1, "actions": []})
    body = response.json()
    assert set(body) == {"error", "meta"}
    assert set(body["error"]) == {"code", "message", "path"}
    assert body["error"]["path"] is None
