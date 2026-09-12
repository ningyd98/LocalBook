"""Shell proxy environment must never break local model calls.

Regression context: ``httpx.AsyncClient`` defaults to ``trust_env=True`` and
*raises* while parsing a malformed proxy variable (a ``NO_PROXY`` containing an
IPv6 literal such as ``[::1]`` is enough). The exception is raised before the
request is made, escapes the adapter's error mapping, and surfaces as an HTTP 500
for a user who never asked LocalNote to use a proxy.

Every HTTP client in LocalBook therefore treats shell proxies as opt-in, and
these tests pin that policy for the chat adapter, the discovery client, the
embedding provider and the reranker.
"""

from __future__ import annotations

import httpx
import pytest

from server.ai.adapters.omlx_client import OMLXDiscoveryConfig, OMLXModelDiscoveryClient
from server.ai.adapters.openai_compatible import OpenAICompatibleAdapter
from server.ai.service import AIStatusService
from server.api.main import create_app
from server.config import AISettings, RagSettings, Settings
from server.rag.embeddings.openai_compatible import OpenAICompatibleEmbeddingProvider
from server.rag.rerank.openai_compatible import OpenAICompatibleReranker
from server.runtime import SESSION_HEADER
from tests.backend.client import TestClient

# A NO_PROXY value httpx cannot parse; combined with a set proxy var this is what
# made every local call raise `InvalidURL: Invalid port: ':1]'`.
BROKEN_PROXY_ENV = {
    "HTTP_PROXY": "http://127.0.0.1:7890",
    "HTTPS_PROXY": "http://127.0.0.1:7890",
    "NO_PROXY": "localhost,127.0.0.1,::1,[::1]",
}


@pytest.fixture(autouse=True)
def _broken_proxy_environment(monkeypatch: pytest.MonkeyPatch):
    for key, value in BROKEN_PROXY_ENV.items():
        monkeypatch.setenv(key, value)


def _models_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"data": [{"id": "Qwen3.5-4B-Instruct"}]})


def test_httpx_default_would_break_in_this_environment() -> None:
    """Documents the failure mode the opt-in protects against."""
    with pytest.raises(httpx.InvalidURL):
        httpx.AsyncClient(timeout=1)


def test_chat_adapter_ignores_the_shell_proxy_by_default() -> None:
    adapter = OpenAICompatibleAdapter(
        "http://127.0.0.1:1234/v1",
        transport=httpx.MockTransport(_models_handler),
    )
    assert adapter.trust_env is False
    models = __import__("asyncio").run(adapter.list_models())
    assert [model.id for model in models] == ["Qwen3.5-4B-Instruct"]


def test_chat_adapter_can_opt_into_the_shell_proxy() -> None:
    adapter = OpenAICompatibleAdapter(
        "http://127.0.0.1:1234/v1",
        transport=httpx.MockTransport(_models_handler),
        trust_env=True,
    )
    assert adapter.trust_env is True


def test_discovery_client_ignores_the_shell_proxy_by_default() -> None:
    client = OMLXModelDiscoveryClient(
        OMLXDiscoveryConfig(base_url="http://127.0.0.1:1234/v1"),
        transport=httpx.MockTransport(_models_handler),
    )
    models = __import__("asyncio").run(client.list_models())
    assert len(models) == 1


def test_ai_status_service_forwards_the_opt_in() -> None:
    from_plain = AIStatusService.from_ai_settings(AISettings())
    assert from_plain._trust_env is False  # noqa: SLF001
    from_opted_in = AIStatusService.from_ai_settings(AISettings(use_env_proxy=True))
    assert from_opted_in._trust_env is True  # noqa: SLF001


def test_embedding_provider_ignores_the_shell_proxy_by_default() -> None:
    provider = OpenAICompatibleEmbeddingProvider(
        base_url="http://127.0.0.1:1234/v1",
        model="bge-m3",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"data": [{"embedding": [1.0, 0.0]}]})
        ),
    )
    assert provider._trust_env is False  # noqa: SLF001
    vectors = __import__("asyncio").run(provider.embed_query("hello"))
    assert vectors == [1.0, 0.0]

    from server.rag.factory import build_embedding_provider

    opted_in = build_embedding_provider(
        RagSettings(
            embedding_provider="openai_compatible",
            embedding_base_url="http://127.0.0.1:1234/v1",
            embedding_model="bge-m3",
            use_env_proxy=True,
        )
    )
    assert opted_in is not None and opted_in._trust_env is True  # noqa: SLF001


def test_reranker_ignores_the_shell_proxy_by_default() -> None:
    from server.rag.factory import build_reranker

    settings = RagSettings(
        reranker_enabled=True,
        reranker_provider="openai_compatible",
        reranker_base_url="http://127.0.0.1:9997/v1",
        reranker_model="bge-reranker-v2",
    )
    reranker = build_reranker(settings)
    assert isinstance(reranker, OpenAICompatibleReranker)
    assert reranker._trust_env is False  # noqa: SLF001
    opted_in = build_reranker(settings.model_copy(update={"use_env_proxy": True}))
    assert opted_in is not None and opted_in._trust_env is True  # noqa: SLF001


def test_ai_status_endpoint_reports_offline_instead_of_500(tmp_path) -> None:
    """End-to-end consequence: a broken proxy env must degrade, not 500."""
    settings = Settings(
        vault={"root": tmp_path},
        ai={"enabled": True, "base_url": "http://127.0.0.1:9/v1"},
    )
    app = create_app(settings)
    with TestClient(app) as client:
        client.headers[SESSION_HEADER] = app.state.runtime.session_id
        response = client.get("/api/v1/ai/status")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] in {"offline", "connected"}
    assert payload["error_code"] in {None, "connection_refused", "timeout", "http_error"}
