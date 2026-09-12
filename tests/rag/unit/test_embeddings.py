"""Phase 2 — embedding providers (M14 §四)."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from server.rag.embeddings.base import (
    BatchingEmbeddingProvider,
    EmbeddingDimensionMismatch,
    EmbeddingProvider,
    EmbeddingRequestFailed,
    EmbeddingUnavailable,
    HashEmbeddingProvider,
    MockEmbeddingProvider,
    cosine_similarity,
    normalize,
)
from server.rag.embeddings.openai_compatible import OpenAICompatibleEmbeddingProvider


def test_hash_provider_is_deterministic_and_offline() -> None:
    provider = HashEmbeddingProvider(dimension=64)
    assert isinstance(provider, EmbeddingProvider)
    assert provider.is_degraded is True
    first = asyncio.run(provider.embed_query("云边协同"))
    second = asyncio.run(provider.embed_query("云边协同"))
    assert first == second
    assert len(first) == 64
    assert abs(sum(value * value for value in first) - 1.0) < 1e-9


def test_hash_provider_scores_similar_text_higher() -> None:
    provider = HashEmbeddingProvider(dimension=256)
    base = asyncio.run(provider.embed_query("云边协同机械臂重规划"))
    near = asyncio.run(provider.embed_query("云边协同机械臂重规划机制"))
    far = asyncio.run(provider.embed_query("完全无关的园艺笔记"))
    assert cosine_similarity(base, near) > cosine_similarity(base, far)


def test_mock_provider_records_calls_and_is_not_degraded() -> None:
    provider = MockEmbeddingProvider(dimension=8)
    assert provider.is_degraded is False
    vectors = asyncio.run(provider.embed_documents(["a", "b"]))
    assert len(vectors) == 2 and len(vectors[0]) == 8
    assert provider.document_calls == [["a", "b"]]
    assert asyncio.run(provider.health_check()) is True
    assert provider.health_calls == 1


def test_mock_provider_can_fail_on_demand() -> None:
    provider = MockEmbeddingProvider(dimension=4, fail_on_call=1)
    with pytest.raises(EmbeddingRequestFailed):
        asyncio.run(provider.embed_documents(["a"]))


def test_batching_provider_splits_into_batches() -> None:
    inner = MockEmbeddingProvider(dimension=4)
    provider = BatchingEmbeddingProvider(inner, batch_size=2)
    vectors = asyncio.run(provider.embed_documents(["a", "b", "c", "d", "e"]))
    assert len(vectors) == 5
    assert [len(batch) for batch in inner.document_calls] == [2, 2, 1]
    assert provider.model == inner.model
    assert provider.dimension == 4


def test_normalize_handles_zero_vector() -> None:
    assert normalize([0.0, 0.0]) == [0.0, 0.0]
    unit = normalize([3.0, 4.0])
    assert abs(unit[0] - 0.6) < 1e-9 and abs(unit[1] - 0.8) < 1e-9


def test_openai_compatible_provider_discovers_dimension() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path.endswith("/embeddings")
        assert body["model"] == "bge-m3"
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"embedding": [3.0, 4.0, 0.0]},
                    {"embedding": [0.0, 1.0, 0.0]},
                ]
            },
        )

    provider = OpenAICompatibleEmbeddingProvider(
        base_url="http://127.0.0.1:1234/v1",
        model="bge-m3",
        api_key="secret",
        transport=httpx.MockTransport(handler),
    )
    assert provider.dimension == 0
    vectors = asyncio.run(provider.embed_documents(["a", "b"]))
    assert provider.dimension == 3
    assert vectors[0] == [0.6, 0.8, 0.0]  # normalised on ingest


def test_openai_compatible_provider_reports_missing_config() -> None:
    provider = OpenAICompatibleEmbeddingProvider(base_url="", model="bge-m3")
    assert provider.configured is False
    with pytest.raises(EmbeddingUnavailable):
        asyncio.run(provider.embed_query("x"))


def test_openai_compatible_provider_maps_auth_and_http_errors() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(401, json={"error": "nope"})
    )
    provider = OpenAICompatibleEmbeddingProvider(
        base_url="http://127.0.0.1:1234/v1", model="m", transport=transport
    )
    with pytest.raises(EmbeddingRequestFailed) as excinfo:
        asyncio.run(provider.embed_query("x"))
    assert excinfo.value.code == "embedding_auth_error"

    transport = httpx.MockTransport(
        lambda request: httpx.Response(500, json={"error": "boom"})
    )
    provider = OpenAICompatibleEmbeddingProvider(
        base_url="http://127.0.0.1:1234/v1", model="m", transport=transport
    )
    with pytest.raises(EmbeddingRequestFailed) as excinfo:
        asyncio.run(provider.embed_query("x"))
    assert excinfo.value.code == "embedding_http_error"


def test_openai_compatible_provider_rejects_invalid_body() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"data": []})
    )
    provider = OpenAICompatibleEmbeddingProvider(
        base_url="http://127.0.0.1:1234/v1", model="m", transport=transport
    )
    with pytest.raises(EmbeddingRequestFailed):
        asyncio.run(provider.embed_query("x"))


def test_openai_compatible_provider_detects_dimension_change() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"data": [{"embedding": [1.0, 0.0]}]})
    )
    provider = OpenAICompatibleEmbeddingProvider(
        base_url="http://127.0.0.1:1234/v1",
        model="m",
        dimension=3,
        transport=transport,
    )
    with pytest.raises(EmbeddingDimensionMismatch):
        asyncio.run(provider.embed_query("x"))


def test_openai_compatible_health_check_is_false_on_failure() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(503, json={"error": "down"})
    )
    provider = OpenAICompatibleEmbeddingProvider(
        base_url="http://127.0.0.1:1234/v1", model="m", transport=transport
    )
    assert asyncio.run(provider.health_check()) is False


def test_chat_and_embedding_models_are_independent() -> None:
    """The embedding provider never inherits the chat model."""
    provider = OpenAICompatibleEmbeddingProvider(
        base_url="http://127.0.0.1:1234/v1", model="bge-m3"
    )
    assert provider.model == "bge-m3"
    assert provider.model != "Qwen3.5-4B"
