"""Phase 12 — optional rerankers: local heuristic and HTTP adapter (M14 §七)."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from server.config import RagSettings
from server.rag.factory import build_reranker
from server.rag.rerank.base import LexicalOverlapReranker, RerankItem
from server.rag.rerank.openai_compatible import (
    OpenAICompatibleReranker,
    RerankUnavailable,
    parse_rerank_response,
)

# ----------------------------------------------------------------------
# Local heuristic reranker
# ----------------------------------------------------------------------


def test_lexical_reranker_prefers_term_coverage() -> None:
    reranker = LexicalOverlapReranker()
    items = reranker.rerank(
        "obsidian plugin",
        [
            "完全不相关的内容",
            "Obsidian plugin runtime compatibility",
            "obsidian",
        ],
    )
    assert [item.index for item in items][:2] == [1, 2]
    assert items[0].score > items[-1].score


def test_lexical_reranker_respects_top_n_and_empty_input() -> None:
    reranker = LexicalOverlapReranker()
    assert reranker.rerank("q", [], top_n=3) == []
    assert len(reranker.rerank("q", ["a", "b", "c"], top_n=2)) == 2
    assert reranker.rerank("", ["a"])[0].score == 0.0


def test_lexical_reranker_is_deterministic() -> None:
    reranker = LexicalOverlapReranker()
    docs = ["aaa bbb", "bbb", "ccc"]
    assert [item.index for item in reranker.rerank("aaa", docs)] == [
        item.index for item in reranker.rerank("aaa", docs)
    ]


# ----------------------------------------------------------------------
# Factory wiring
# ----------------------------------------------------------------------


def test_factory_disables_reranking_by_default() -> None:
    assert build_reranker(RagSettings()) is None


def test_factory_builds_local_and_http_rerankers() -> None:
    local = build_reranker(RagSettings(reranker_enabled=True, reranker_provider="lexical"))
    assert isinstance(local, LexicalOverlapReranker)
    http = build_reranker(
        RagSettings(
            reranker_enabled=True,
            reranker_provider="openai_compatible",
            reranker_base_url="http://127.0.0.1:9997/v1",
            reranker_model="bge-reranker-v2",
        )
    )
    assert isinstance(http, OpenAICompatibleReranker)
    assert http.configured is True


def test_factory_degrades_when_http_reranker_is_misconfigured() -> None:
    assert (
        build_reranker(
            RagSettings(reranker_enabled=True, reranker_provider="openai_compatible")
        )
        is None
    )
    assert (
        build_reranker(RagSettings(reranker_enabled=True, reranker_provider="none")) is None
    )


def test_reranker_api_key_is_never_echoed_in_settings() -> None:
    view = RagSettings(reranker_api_key="super-secret").snapshot_view()
    assert "reranker_api_key" not in view
    assert view["reranker_api_key_set"] is True


# ----------------------------------------------------------------------
# HTTP adapter
# ----------------------------------------------------------------------


def _transport(payload: dict, *, status: int = 200, capture: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture.append(
                {
                    "url": str(request.url),
                    "headers": dict(request.headers),
                    "body": json.loads(request.content),
                }
            )
        return httpx.Response(status, json=payload)

    return httpx.MockTransport(handler)


def test_http_reranker_ranks_documents() -> None:
    captured: list = []
    provider = OpenAICompatibleReranker(
        base_url="http://127.0.0.1:9997/v1",
        model="bge-reranker-v2",
        api_key="secret",
        transport=_transport(
            {
                "results": [
                    {"index": 2, "relevance_score": 0.91},
                    {"index": 0, "relevance_score": 0.72},
                    {"index": 1, "relevance_score": 0.10},
                ]
            },
            capture=captured,
        ),
    )
    items = provider.rerank("云边协同", ["a", "b", "c"])
    assert [(item.index, round(item.score, 2)) for item in items] == [
        (2, 0.91),
        (0, 0.72),
        (1, 0.1),
    ]
    request = captured[0]
    assert request["url"].endswith("/rerank")
    assert request["headers"]["authorization"] == "Bearer secret"
    assert request["body"]["model"] == "bge-reranker-v2"
    assert request["body"]["query"] == "云边协同"
    assert request["body"]["documents"] == ["a", "b", "c"]


def test_http_reranker_accepts_the_alternative_response_shape() -> None:
    provider = OpenAICompatibleReranker(
        base_url="http://127.0.0.1:9997/v1",
        model="m",
        transport=_transport({"data": [{"index": 1, "score": 0.5}, {"index": 0, "score": 0.9}]}),
    )
    assert [item.index for item in provider.rerank("q", ["a", "b"])] == [0, 1]


def test_http_reranker_honours_top_n_and_document_cap() -> None:
    captured: list = []
    provider = OpenAICompatibleReranker(
        base_url="http://127.0.0.1:9997/v1",
        model="m",
        max_documents=2,
        transport=_transport(
            {
                "results": [
                    {"index": 0, "relevance_score": 0.2},
                    {"index": 1, "relevance_score": 0.9},
                ]
            },
            capture=captured,
        ),
    )
    bounded = provider.rerank("q", ["a", "b", "c"], top_n=1)
    assert len(bounded) == 1 and bounded[0].index == 1
    assert captured[-1]["body"]["documents"] == ["a", "b"]  # capped, not the whole Vault
    assert captured[-1]["body"]["top_n"] == 1


def test_http_reranker_reports_why_it_cannot_answer() -> None:
    unconfigured = OpenAICompatibleReranker(base_url="", model="")
    assert unconfigured.configured is False
    with pytest.raises(RerankUnavailable):
        unconfigured.rerank("q", ["a"])

    missing_model = OpenAICompatibleReranker(base_url="http://x/v1", model="")
    with pytest.raises(RerankUnavailable):
        missing_model.rerank("q", ["a"])


@pytest.mark.parametrize("status", [401, 403, 500, 503])
def test_http_reranker_maps_http_failures(status: int) -> None:
    provider = OpenAICompatibleReranker(
        base_url="http://127.0.0.1:9997/v1",
        model="m",
        transport=_transport({}, status=status),
    )
    with pytest.raises(RerankUnavailable):
        provider.rerank("q", ["a"])


def test_http_reranker_rejects_invalid_bodies() -> None:
    for payload in ({}, {"results": []}, {"results": [{"index": 9, "score": 1.0}]}):
        provider = OpenAICompatibleReranker(
            base_url="http://127.0.0.1:9997/v1",
            model="m",
            transport=_transport(payload),
        )
        with pytest.raises(RerankUnavailable):
            provider.rerank("q", ["a"])
    invalid_json = OpenAICompatibleReranker(
        base_url="http://127.0.0.1:9997/v1",
        model="m",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"not json")),
    )
    with pytest.raises(RerankUnavailable):
        invalid_json.rerank("q", ["a"])


def test_parse_rerank_response_ignores_out_of_range_and_non_numeric_rows() -> None:
    items = parse_rerank_response(
        {
            "results": [
                {"index": 0, "relevance_score": "not-a-number"},
                {"index": 1, "relevance_score": 0.4},
                {"index": 42, "relevance_score": 0.9},
                "junk",
            ]
        },
        2,
    )
    assert items == [RerankItem(index=1, score=0.4)]


def test_http_reranker_works_from_inside_a_running_loop() -> None:
    """The retriever is synchronous but tests (and ASGI) may run inside a loop."""

    async def scenario() -> list[RerankItem]:
        provider = OpenAICompatibleReranker(
            base_url="http://127.0.0.1:9997/v1",
            model="m",
            transport=_transport({"results": [{"index": 0, "relevance_score": 0.3}]}),
        )
        return provider.rerank("q", ["a"])

    items = asyncio.run(scenario())
    assert [item.index for item in items] == [0]


def test_reranker_probe_endpoint_reports_offline_without_raising(tmp_path) -> None:
    """The settings probe must answer with a status, not an exception."""
    from server.api.main import create_app
    from server.config import Settings
    from server.runtime import SESSION_HEADER
    from tests.backend.client import TestClient

    settings = Settings(vault={"root": tmp_path}, rag={"enabled": True})
    app = create_app(settings)
    with TestClient(app) as client:
        client.headers[SESSION_HEADER] = app.state.runtime.session_id
        response = client.post(
            "/api/v1/settings/rag/reranker/test",
            json={
                "provider": "openai_compatible",
                # Reserved TEST-NET address: unreachable by definition, so the
                # probe must degrade rather than hang or 500.
                "base_url": "http://127.0.0.1:9/v1",
                "model": "bge-reranker-v2",
            },
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "offline"
    assert body["model"] == "bge-reranker-v2"
    assert body["results"] == []


def test_reranker_probe_rejects_a_bad_endpoint(tmp_path) -> None:
    from server.api.main import create_app
    from server.config import Settings
    from server.runtime import SESSION_HEADER
    from tests.backend.client import TestClient

    app = create_app(Settings(vault={"root": tmp_path}, rag={"enabled": True}))
    with TestClient(app) as client:
        client.headers[SESSION_HEADER] = app.state.runtime.session_id
        response = client.post(
            "/api/v1/settings/rag/reranker/test",
            json={"base_url": "not-a-url", "model": "m"},
        )
    assert response.status_code == 422


def test_http_reranker_health_check_reports_false_on_failure() -> None:
    provider = OpenAICompatibleReranker(
        base_url="http://127.0.0.1:9997/v1",
        model="m",
        transport=_transport({}, status=500),
    )

    async def scenario() -> bool:
        return await provider.health_check()

    assert asyncio.run(scenario()) is False
