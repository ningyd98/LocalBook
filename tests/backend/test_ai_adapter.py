"""OpenAI-compatible adapter matrix (PLAN-M6 §9.1.2) — all HTTP mocked."""

from __future__ import annotations

import json

import httpx
import pytest

from server.ai.adapters.base import ChatMessage
from server.ai.adapters.openai_compatible import OpenAICompatibleAdapter
from server.ai.errors import AIAdapterError, CapabilityUnavailable


def _adapter(handler, *, max_bytes: int = 1_000_000) -> OpenAICompatibleAdapter:
    transport = httpx.MockTransport(handler)
    return OpenAICompatibleAdapter(
        "http://127.0.0.1:8000/v1", transport=transport, max_response_bytes=max_bytes
    )


def _chat_response(content: str) -> dict[str, object]:
    return {"choices": [{"message": {"content": content}}], "model": "upstream-model"}


async def test_models_parse_and_capability_whitelist() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "Qwen3.5-4B-Instruct", "owned_by": "omlx",
                     "capabilities": {"chat": True, "embedding": "junk", "evil": True}},
                    {"id": "bge-reranker", "capabilities": {"rerank": True}},
                ]
            },
        )

    adapter = _adapter(handler)
    models = await adapter.list_models()
    assert [m.id for m in models] == ["Qwen3.5-4B-Instruct", "bge-reranker"]
    assert models[0].capabilities.chat is True
    assert models[0].capabilities.embedding is False  # non-bool ignored
    assert models[1].capabilities.rerank is True


async def test_models_malformed_entry_is_invalid_response() -> None:
    adapter = _adapter(
        lambda _req: httpx.Response(200, json={"data": [{"id": "ok"}, "junk"]})
    )
    with pytest.raises(AIAdapterError) as info:
        await adapter.list_models()
    assert info.value.kind == "invalid_response"


async def test_chat_payload_and_result_shape() -> None:
    captured: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=_chat_response('{"answer": "ok"}'))

    adapter = _adapter(handler)
    result = await adapter.chat(
        model="Qwen3.5-4B-Instruct",
        messages=[ChatMessage("system", "sys"), ChatMessage("user", "hi")],
        temperature=0.2,
        response_schema={"type": "object", "properties": {"answer": {}}},
        timeout_seconds=2.0,
        max_output_tokens=700,
    )
    body = captured[0]
    assert body["model"] == "Qwen3.5-4B-Instruct"
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert body["temperature"] == 0.2
    assert body["max_output_tokens"] == 700  # S1: settings value reaches the wire
    assert body["response_format"]["type"] == "json_schema"
    assert result.content == '{"answer": "ok"}'
    assert result.model == "upstream-model"


async def test_http_404_maps_to_model_not_found_without_retry() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404, json={"error": "model missing"})

    adapter = _adapter(handler)
    with pytest.raises(AIAdapterError) as info:
        await adapter.chat(
            model="nope",
            messages=[ChatMessage("user", "hi")],
            temperature=0.1,
            response_schema=None,
            timeout_seconds=1,
        )
    assert info.value.kind == "model_not_found"
    assert info.value.http_status == 404
    assert calls == 1


async def test_http_5xx_maps_to_http_error_503() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "super-secret-internal-string"})

    adapter = _adapter(handler)
    with pytest.raises(AIAdapterError) as info:
        await adapter.chat(
            model="m", messages=[ChatMessage("user", "hi")], temperature=0.1,
            response_schema=None, timeout_seconds=1,
        )
    assert info.value.kind == "http_error"
    assert info.value.http_status == 500
    assert "super-secret-internal-string" not in str(info.value)


async def test_timeout_maps_to_timeout_kind() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    adapter = _adapter(handler)
    with pytest.raises(AIAdapterError) as info:
        await adapter.chat(
            model="m", messages=[ChatMessage("user", "hi")], temperature=0.1,
            response_schema=None, timeout_seconds=1,
        )
    assert info.value.kind == "timeout"


async def test_connect_error_maps_to_offline() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    adapter = _adapter(handler)
    with pytest.raises(AIAdapterError) as info:
        await adapter.chat(
            model="m", messages=[ChatMessage("user", "hi")], temperature=0.1,
            response_schema=None, timeout_seconds=1,
        )
    assert info.value.kind == "offline"


async def test_invalid_json_body_maps_to_invalid_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    adapter = _adapter(handler)
    with pytest.raises(AIAdapterError) as info:
        await adapter.chat(
            model="m", messages=[ChatMessage("user", "hi")], temperature=0.1,
            response_schema=None, timeout_seconds=1,
        )
    assert info.value.kind == "invalid_response"


async def test_choices_schema_broken_maps_to_invalid_response() -> None:
    adapter = _adapter(lambda _req: httpx.Response(200, json={"choices": "nope"}))
    with pytest.raises(AIAdapterError) as info:
        await adapter.chat(
            model="m", messages=[ChatMessage("user", "hi")], temperature=0.1,
            response_schema=None, timeout_seconds=1,
        )
    assert info.value.kind == "invalid_response"


async def test_oversized_response_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "x" * 100}}]})

    adapter = _adapter(handler, max_bytes=8)
    with pytest.raises(AIAdapterError) as info:
        await adapter.chat(
            model="m", messages=[ChatMessage("user", "hi")], temperature=0.1,
            response_schema=None, timeout_seconds=1,
        )
    assert info.value.kind == "invalid_response"


async def test_response_format_400_falls_back_to_plain_json_once() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if "response_format" in body:
            return httpx.Response(400, json={"error": "response_format unsupported"})
        return httpx.Response(200, json=_chat_response('{"answer": "plain"}'))

    adapter = _adapter(handler)
    result = await adapter.chat(
        model="m",
        messages=[ChatMessage("user", "hi")],
        temperature=0.1,
        response_schema={"type": "object", "properties": {"answer": {}}},
        timeout_seconds=1,
    )
    assert result.content == '{"answer": "plain"}'
    assert len(requests) == 2
    assert "response_format" not in requests[1]


async def test_response_format_400_does_not_fallback_when_absent() -> None:
    adapter = _adapter(
        lambda _req: httpx.Response(400, json={"error": "reject"}),
    )
    with pytest.raises(AIAdapterError) as info:
        await adapter.chat(
            model="m", messages=[ChatMessage("user", "hi")], temperature=0.1,
            response_schema=None, timeout_seconds=1,
        )
    assert info.value.kind == "http_error"


async def test_embed_and_rerank_are_capability_unavailable_not_faked() -> None:
    adapter = _adapter(lambda _req: httpx.Response(200, json={"data": []}))
    with pytest.raises(CapabilityUnavailable):
        await adapter.embed(model="m", inputs=["x"], timeout_seconds=1)
    with pytest.raises(CapabilityUnavailable):
        await adapter.rerank(model="m", query="q", documents=["d"], timeout_seconds=1)
