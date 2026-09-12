"""Phase 8/9 — RAG REST API and Vault-lifecycle incremental indexing (M14 §十/§十三)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI

from server.api import dependencies
from server.api.main import create_app
from server.config import Settings
from server.rag.api_schemas import RagIndexStatusResponse
from server.rag.embeddings.base import MockEmbeddingProvider
from server.rag.factory import build_embedding_provider, create_rag_stack
from server.rag.index_service import RagIndexService
from server.rag.retrieval.hybrid import HybridRetriever
from server.rag.retrieval.keyword import KeywordRetriever
from server.rag.retrieval.vector import VectorRetriever
from server.rag.service import RagService
from server.rag.vector.sqlite import SqliteVectorStore
from server.vault.events import VaultEvent
from server.vault.lifecycle import VaultLifecycle
from tests.backend.client import TestClient  # session-aware client (as the UI is)
from tests.rag.conftest import make_index_service, write_note

# ----------------------------------------------------------------------
# Factory
# ----------------------------------------------------------------------


def test_factory_defaults_to_local_embedder_and_no_reranker() -> None:
    settings = Settings().rag
    provider = build_embedding_provider(settings)
    assert provider is not None
    assert provider.is_degraded is True  # documented fallback, reported in status
    assert settings.reranker_enabled is False


def test_factory_builds_openai_compatible_provider() -> None:
    settings = Settings().rag.model_copy(
        update={
            "embedding_provider": "openai_compatible",
            "embedding_base_url": "http://127.0.0.1:1234/v1",
            "embedding_model": "bge-m3",
            "embedding_dimension": 1024,
        }
    )
    provider = build_embedding_provider(settings)
    assert provider is not None
    assert provider.model == "bge-m3"
    assert provider.is_degraded is False


def test_factory_falls_back_when_endpoint_missing() -> None:
    settings = Settings().rag.model_copy(
        update={"embedding_provider": "openai_compatible", "embedding_base_url": ""}
    )
    provider = build_embedding_provider(settings)
    assert provider is not None and provider.is_degraded is True


def test_factory_can_disable_embeddings_entirely() -> None:
    settings = Settings().rag.model_copy(update={"embedding_provider": "none"})
    assert build_embedding_provider(settings) is None


def test_create_rag_stack_assembles_everything(rag_vault, index_db) -> None:
    settings = Settings().rag.model_copy(
        update={"embedding_provider": "hash", "embedding_dimension": 32}
    )
    stack = create_rag_stack(rag_vault, index_db, settings)
    assert isinstance(stack.store, SqliteVectorStore)
    assert isinstance(stack.index, RagIndexService)
    assert isinstance(stack.service, RagService)
    assert stack.service.index_state().chunk_count == 0
    stack.close()


# ----------------------------------------------------------------------
# API
# ----------------------------------------------------------------------


@pytest.fixture
def rag_app(rag_vault, store, tmp_path: Path, monkeypatch) -> FastAPI:
    """App whose RAG dependency is overridden with a deterministic stack."""
    provider = MockEmbeddingProvider(dimension=64)
    index = make_index_service(rag_vault, store, provider=provider)
    index.rebuild()
    vector = VectorRetriever(store, provider=provider)
    retriever = HybridRetriever(keyword=KeywordRetriever(store), vector=vector)

    async def generate(system: str, user: str) -> tuple[str, str]:
        # A deterministic "model": cite the first source it was given.
        assert "[S1]" in user
        return (
            json.dumps({"answer": "根据检索到的证据 [S1]。", "used_sources": ["S1"]}),
            "fake-chat-model",
        )

    service = RagService(index=index, retriever=retriever, generate=generate)

    from server.rag.factory import RagStack

    stack = RagStack(store=store, index=index, retriever=retriever, service=service)

    app = create_app(Settings())
    app.dependency_overrides[dependencies.get_rag_stack] = lambda: stack
    app.dependency_overrides[dependencies.get_rag_service] = lambda: service
    return app


def test_api_status_endpoint(rag_app: FastAPI, rag_vault) -> None:
    write_note(rag_vault, "a.md", "# A\n\n内容。\n")
    with TestClient(rag_app) as client:
        client.post("/api/v1/rag/index/rebuild")
        response = client.get("/api/v1/rag/index/status")
    assert response.status_code == 200
    payload = RagIndexStatusResponse.model_validate(response.json())
    assert payload.enabled is True
    assert payload.chunks >= 1
    assert payload.vector_store == "sqlite"
    assert payload.vector_kernel in {"numpy", "python"}
    assert payload.embedding_model == "mock-embed"


def test_api_search_endpoint_returns_ranks(rag_app: FastAPI, rag_vault) -> None:
    write_note(rag_vault, "a.md", "# 云边协同\n\n机械臂重规划任务。\n")
    with TestClient(rag_app) as client:
        client.post("/api/v1/rag/index/rebuild")
        response = client.post(
            "/api/v1/rag/search", json={"query": "云边协同", "top_k": 5}
        )
    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "云边协同"
    assert body["results"]
    first = body["results"][0]
    assert first["path"] == "a.md"
    assert first["keyword_rank"] is not None
    assert first["start_line"] >= 1 and first["end_line"] >= first["start_line"]
    assert body["stats"]["fts_candidates"] >= 1


def test_api_query_endpoint_returns_grounded_answer_with_sources(
    rag_app: FastAPI, rag_vault
) -> None:
    write_note(
        rag_vault,
        "研究/云边协同.md",
        "# 云边协同\n\n## 重规划机制\n\n边缘节点在时延超限时重新分配任务。\n",
    )
    with TestClient(rag_app) as client:
        client.post("/api/v1/rag/index/rebuild")
        response = client.post(
            "/api/v1/rag/query", json={"query": "云边协同重规划机制"}
        )
    assert response.status_code == 200
    body = response.json()
    assert body["answer"]
    assert body["sources"], body
    source = body["sources"][0]
    assert source["id"] == "S1"
    assert source["path"] == "研究/云边协同.md"
    assert source["start_line"] >= 1
    assert source["excerpt"]
    assert body["model"] == "fake-chat-model"
    assert body["retrieval_stats"]["context_chunks"] >= 1
    assert set(body) >= {"answer", "sources", "retrieval_stats", "model"}


def test_api_query_rejects_empty_query(rag_app: FastAPI) -> None:
    with TestClient(rag_app) as client:
        response = client.post("/api/v1/rag/query", json={"query": "   "})
    assert response.status_code in (400, 422)
    assert response.json()["error"]["code"] == "invalid_request"


def test_api_query_rejects_unknown_fields(rag_app: FastAPI) -> None:
    with TestClient(rag_app) as client:
        response = client.post(
            "/api/v1/rag/query", json={"query": "x", "evil": True}
        )
    assert response.status_code == 422


def test_api_rebuild_endpoint_reports_counts(rag_app: FastAPI, rag_vault) -> None:
    write_note(rag_vault, "a.md", "# A\n\n内容一。\n")
    write_note(rag_vault, "notes/b.md", "# B\n\n内容二。\n")
    with TestClient(rag_app) as client:
        response = client.post("/api/v1/rag/index/rebuild")
    assert response.status_code == 200
    body = response.json()
    assert body["indexed_documents"] == 2
    assert body["indexed_chunks"] >= 2
    assert body["ready"] is True
    assert body["status"]["indexed_notes"] == 2


def test_api_rag_unavailable_is_503_not_500(tmp_path: Path, monkeypatch) -> None:
    app = create_app(Settings())

    def unavailable():
        from server.rag.errors import RagUnavailable

        raise RagUnavailable()

    app.dependency_overrides[dependencies.get_rag_stack] = unavailable
    app.dependency_overrides[dependencies.get_rag_service] = unavailable
    with TestClient(app) as client:
        response = client.get("/api/v1/rag/index/status")
        health = client.get("/api/v1/health")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "rag_unavailable"
    assert health.status_code == 200  # the rest of the API is unaffected


def test_api_debug_flag_returns_retrieval_debug(rag_app: FastAPI, rag_vault) -> None:
    write_note(rag_vault, "a.md", "# 云边协同\n\n机械臂。\n")
    with TestClient(rag_app) as client:
        client.post("/api/v1/rag/index/rebuild")
        response = client.post(
            "/api/v1/rag/query", json={"query": "云边协同", "debug": True}
        )
    assert response.status_code == 200
    assert response.json()["retrieval_stats"]["retrieval_debug"] is not None


# ----------------------------------------------------------------------
# Lifecycle integration
# ----------------------------------------------------------------------


def test_lifecycle_builds_rag_stack_and_shared_watcher_callback(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    settings = Settings(
        vault={"root": root},
        index={"note_text_cap": 1_000_000},
        rag={
            "enabled": True,
            "embedding_provider": "hash",
            "embedding_dimension": 32,
            "index_on_startup": True,
        },
    )
    lifecycle = VaultLifecycle(
        settings.vault, index=settings.index, rag=settings.rag
    )
    try:
        service = lifecycle.startup(start_watcher=False)
        assert service is not None
        assert lifecycle.rag_stack is not None
        # Startup indexing ran, so the empty Vault reports an empty index.
        assert lifecycle.rag_stack.service.status().chunks == 0

        (root / "a.md").write_text("# 云边协同\n\n机械臂重规划。\n", encoding="utf-8")
        # The lifecycle installed one callback that feeds the M4 index *and*
        # the M14 RAG index; invoking it is what the watcher does.
        service._event_callback(VaultEvent(kind="create", path="a.md"))  # noqa: SLF001
        assert lifecycle.rag_stack.index.pending_paths == ["a.md"]
        lifecycle.rag_stack.index.flush()
        assert lifecycle.rag_stack.service.status().chunks >= 1
    finally:
        lifecycle.shutdown()


def test_lifecycle_skips_rag_when_disabled(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    settings = Settings(vault={"root": root}, rag={"enabled": False})
    lifecycle = VaultLifecycle(settings.vault, rag=settings.rag)
    try:
        assert lifecycle.startup(start_watcher=False) is not None
        assert lifecycle.rag_stack is None
    finally:
        lifecycle.shutdown()


def test_lifecycle_rag_failure_does_not_break_vault(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    settings = Settings(vault={"root": root}, rag={"enabled": True})
    lifecycle = VaultLifecycle(settings.vault, rag=settings.rag)

    import server.rag.factory as factory

    def broken(*args, **kwargs):
        raise RuntimeError("rag exploded")

    monkeypatch.setattr(factory, "create_rag_stack", broken)
    try:
        service = lifecycle.startup(start_watcher=False)
        assert service is not None  # the Vault still started
        assert lifecycle.rag_stack is None
        assert lifecycle.index_service is not None
        assert lifecycle.index_service.build_state == "ready"
    finally:
        lifecycle.shutdown()


def test_incremental_indexing_through_lifecycle(
    tmp_path: Path, monkeypatch
) -> None:
    """A modified note flows watcher → debounce → chunk → embed → vector store."""
    root = tmp_path / "vault"
    root.mkdir()
    settings = Settings(
        vault={"root": root},
        rag={
            "enabled": True,
            "embedding_provider": "hash",
            "embedding_dimension": 32,
        },
    )
    lifecycle = VaultLifecycle(settings.vault, rag=settings.rag)
    try:
        service = lifecycle.startup(start_watcher=False)
        assert service is not None and lifecycle.rag_stack is not None
        stack = lifecycle.rag_stack
        service.create_bytes("a.md", "# 云边协同\n\n机械臂重规划任务。\n".encode())
        service._event_callback(VaultEvent(kind="create", path="a.md"))  # noqa: SLF001
        stack.index.flush()
        assert stack.service.status().chunks >= 1

        # A no-op modify must not re-embed anything.
        state_before = stack.store.state()
        service._event_callback(VaultEvent(kind="modify", path="a.md"))  # noqa: SLF001
        result = stack.index.flush()
        assert result.embedded_chunks == 0
        assert stack.store.state().chunk_count == state_before.chunk_count
    finally:
        lifecycle.shutdown()
