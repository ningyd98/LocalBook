"""REST /api/v1/ai/* integration tests (PLAN-M6 §9.1.11): status codes + isolation."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.backend.client import TestClient

from server.ai.adapters.base import ChatResult
from server.ai.errors import AIAdapterError
from server.ai.registry import PromptRegistry
from server.ai.schemas import AICapabilities, DiscoveredModel
from server.ai.workflows import AIWorkflowService
from server.api import dependencies
from server.api.main import create_app
from server.config import AISettings, Settings


class FakeAdapter:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    async def list_models(self) -> list[DiscoveredModel]:
        return [
            DiscoveredModel(
                id="Qwen3.5-4B-Instruct-4bit", capabilities=AICapabilities(chat=True)
            )
        ]

    async def chat(self, **kwargs) -> ChatResult:
        self.calls.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return ChatResult(content=item, model="Qwen3.5-4B-Instruct-4bit")

    async def embed(self, **kwargs):
        raise AIAdapterError("capability_unavailable")

    async def rerank(self, **kwargs):
        raise AIAdapterError("capability_unavailable")


@pytest.fixture
def registry() -> PromptRegistry:
    return PromptRegistry()


@pytest.fixture
def api_vault(vault_service_factory, index_service_factory, tmp_path: Path):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "alpha.md").write_text(
        "# Alpha\nrocket science quantum widgets\n[[beta]]", encoding="utf-8"
    )
    (root / "beta.md").write_text("# Beta\nquantum widgets notes", encoding="utf-8")
    vault = vault_service_factory(root)
    index = index_service_factory(vault)
    return vault, index


def _client_for(service) -> TestClient:
    app = create_app()
    app.dependency_overrides[dependencies.get_ai_workflow_service] = lambda: service
    return TestClient(app, raise_server_exceptions=False)


def test_openapi_declares_all_six_read_only_ai_routes() -> None:
    client = TestClient(create_app())
    paths = client.get("/openapi.json").json()["paths"]
    for route in ("chat", "summarize", "tags", "related", "extract_todos", "classify"):
        assert f"/api/v1/ai/{route}" in paths
    for route in ("chat", "summarize", "tags", "related", "extract_todos", "classify"):
        assert paths[f"/api/v1/ai/{route}"]["post"] is not None


def test_six_routes_schema_gate_422_without_vault_or_ai_call() -> None:
    client = TestClient(create_app())  # vault unconfigured, no AI override
    for route in ("chat", "summarize", "tags", "related", "extract_todos", "classify"):
        response = client.post(f"/api/v1/ai/{route}", json={})
        assert response.status_code == 422, route
        body = response.json()
        assert body["error"]["code"] == "invalid_request"
        assert "meta" not in body  # 422 shape has no AI meta


def test_ai_disabled_is_503_ai_disabled() -> None:
    settings = Settings(ai=AISettings(enabled=False, base_url=None))
    client = TestClient(create_app(settings=settings), raise_server_exceptions=False)
    response = client.post("/api/v1/ai/chat", json={"question": "hi"})
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "ai_disabled"
    assert body["meta"] == {}


def test_chat_success_via_route(api_vault, registry: PromptRegistry) -> None:
    vault, index = api_vault
    adapter = FakeAdapter(
        [
            '{"answer": "42", "citations": [{"path": "alpha.md", "heading": null, "quote": "q"}]}'
        ]
    )
    service = AIWorkflowService(AISettings(), adapter, vault=vault, index=index)
    client = _client_for(service)
    response = client.post(
        "/api/v1/ai/chat",
        json={"note_path": "alpha.md", "question": "meaning?"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "42"
    assert body["prompt_version"] == registry.get("chat").prompt_version
    assert body["model"] == "Qwen3.5-4B-Instruct-4bit"
    assert body["degraded"] is False
    assert body["citations"][0]["path"] == "alpha.md"


def test_chat_without_note_path_succeeds_when_vault_missing() -> None:
    # S4: an unconfigured Vault must not break an empty-context chat.
    adapter = FakeAdapter(['{"answer": "no vault needed", "citations": []}'])
    service = AIWorkflowService(AISettings(), adapter, vault=None, index=None)
    client = _client_for(service)
    response = client.post("/api/v1/ai/chat", json={"question": "hi there"})
    assert response.status_code == 200
    assert response.json()["answer"] == "no vault needed"


def test_traversal_path_is_400_not_404(api_vault) -> None:
    vault, index = api_vault
    service = AIWorkflowService(AISettings(), FakeAdapter([]), vault=vault, index=index)
    client = _client_for(service)
    for bad in ("/etc/passwd", "../escape.md", ".localnote/state.json"):
        response = client.post("/api/v1/ai/summarize", json={"note_path": bad})
        assert response.status_code == 400, bad
        body = response.json()
        assert body["error"]["code"] == "ai_invalid_request"
        assert body["error"]["path"] is None


def test_missing_note_is_404_not_found(api_vault) -> None:
    vault, index = api_vault
    service = AIWorkflowService(AISettings(), FakeAdapter([]), vault=vault, index=index)
    client = _client_for(service)
    response = client.post("/api/v1/ai/summarize", json={"note_path": "ghost.md"})
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"
    assert body["meta"] == {}


def test_invalid_output_is_502_with_meta(api_vault, registry: PromptRegistry) -> None:
    vault, index = api_vault
    service = AIWorkflowService(
        AISettings(), FakeAdapter(["definitely not json"]), vault=vault, index=index
    )
    client = _client_for(service)
    response = client.post("/api/v1/ai/summarize", json={"note_path": "alpha.md"})
    assert response.status_code == 502
    body = response.json()
    assert body["error"]["code"] == "ai_invalid_output"
    assert body["meta"] == {
        "prompt_version": registry.get("summarize_note").prompt_version,
        "model": "Qwen3.5-4B-Instruct-4bit",
    }
    assert "traceback" not in response.text.lower()


def test_offline_is_503_ai_unavailable_with_meta(api_vault, registry: PromptRegistry) -> None:
    vault, index = api_vault
    service = AIWorkflowService(
        AISettings(), FakeAdapter([AIAdapterError("offline")]), vault=vault, index=index
    )
    client = _client_for(service)
    response = client.post("/api/v1/ai/tags", json={"note_path": "alpha.md"})
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "ai_unavailable"
    assert body["meta"]["prompt_version"] == registry.get("generate_tags").prompt_version
    assert body["meta"]["model"] == "Qwen3.5-4B-Instruct-4bit"


def test_timeout_is_503_ai_timeout(api_vault, registry: PromptRegistry) -> None:
    vault, index = api_vault
    service = AIWorkflowService(
        AISettings(), FakeAdapter([AIAdapterError("timeout")]), vault=vault, index=index
    )
    client = _client_for(service)
    response = client.post("/api/v1/ai/tags", json={"note_path": "alpha.md"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ai_timeout"


def test_requested_model_missing_is_400_model_not_found(api_vault) -> None:
    vault, index = api_vault
    adapter = FakeAdapter([])
    service = AIWorkflowService(
        AISettings(chat_model="llama-not-discovered"), adapter, vault=vault, index=index
    )
    client = _client_for(service)
    response = client.post("/api/v1/ai/summarize", json={"note_path": "alpha.md"})
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "ai_model_not_found"
    assert body["meta"] == {}


def test_workflow_ai_not_configured_is_503() -> None:
    settings = Settings(ai=AISettings(enabled=True, base_url=None))
    client = TestClient(create_app(settings=settings), raise_server_exceptions=False)
    response = client.post("/api/v1/ai/chat", json={"question": "hi"})
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "ai_not_configured"


def test_related_without_index_is_503_index_unavailable(api_vault) -> None:
    vault, _index = api_vault
    adapter = FakeAdapter(['{"related": []}'])
    service = AIWorkflowService(AISettings(), adapter, vault=vault, index=None)
    client = _client_for(service)
    response = client.post("/api/v1/ai/related", json={"note_path": "alpha.md", "limit": 5})
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "index_unavailable"
    assert adapter.calls == []


def test_related_route_returns_only_candidate_paths(api_vault, registry: PromptRegistry) -> None:
    vault, index = api_vault
    canned = (
        '{"related": [{"path": "beta.md", "title": "Beta", "reason": "term", "score": 1.0},'
        ' {"path": "evil.md", "title": "Evil", "reason": "fake", "score": 9.0}]}'
    )
    service = AIWorkflowService(AISettings(), FakeAdapter([canned]), vault=vault, index=index)
    client = _client_for(service)
    response = client.post("/api/v1/ai/related", json={"note_path": "alpha.md", "limit": 5})
    assert response.status_code == 200
    body = response.json()
    assert [item["path"] for item in body["related"]] == ["beta.md"]
    assert body["candidates_considered"] == 1
    assert body["model"] == "Qwen3.5-4B-Instruct-4bit"
    assert body["prompt_version"] == registry.get("suggest_links").prompt_version


def test_six_routes_never_touch_vault_write_paths(api_vault) -> None:
    vault, index = api_vault
    alpha_before = vault.read_bytes("alpha.md")[0]
    beta_before = vault.read_bytes("beta.md")[0]
    write_attempts: list[str] = []
    for name in (
        "create_bytes",
        "write_bytes",
        "update_bytes",
        "create_file",
        "write_file",
        "delete_file",
        "delete",
        "move_file",
        "move",
    ):
        original = getattr(vault, name)

        def _spy(*args, _name=name, _original=original, **kwargs):  # type: ignore[no-untyped-def]
            write_attempts.append(_name)
            raise AssertionError(f"AI must not call vault write {_name}")

        setattr(vault, name, _spy)
    responses = [
        '{"items": [{"text": "x"}]}',
        '{"tags": [{"name": "t", "reason": "r"}]}',
        '{"label": "l", "confidence": 0.5, "alternatives": []}',
        '{"summary": "s", "key_points": []}',
        '{"answer": "a", "citations": []}',
    ]
    adapter = FakeAdapter(responses)
    service = AIWorkflowService(AISettings(), adapter, vault=vault, index=index)
    client = _client_for(service)
    requests = [
        ("extract_todos", {"note_path": "alpha.md"}),
        ("tags", {"note_path": "alpha.md"}),
        ("classify", {"note_path": "alpha.md", "labels": ["l"]}),
        ("summarize", {"note_path": "alpha.md"}),
        ("chat", {"note_path": "alpha.md", "question": "q"}),
    ]
    for route, payload in requests:
        response = client.post(f"/api/v1/ai/{route}", json=payload)
        assert response.status_code == 200, route
    assert write_attempts == []
    assert vault.read_bytes("alpha.md")[0] == alpha_before
    assert vault.read_bytes("beta.md")[0] == beta_before
