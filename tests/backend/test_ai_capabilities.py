"""Capability aggregation and chat-model resolution (PLAN-M6 §9.1.3)."""

from __future__ import annotations

import pytest

from server.ai.capabilities import aggregate_capabilities, resolve_chat_model
from server.ai.errors import AIError, AIErrorCode
from server.ai.schemas import AICapabilities, DiscoveredModel


def _model(
    model_id: str, chat: bool = False, embedding: bool = False, rerank: bool = False
) -> DiscoveredModel:
    return DiscoveredModel(
        id=model_id, capabilities=AICapabilities(chat=chat, embedding=embedding, rerank=rerank)
    )


def test_auto_resolution_prefers_qwen_and_falls_back() -> None:
    models = [
        _model("llama-3.1-8b-instruct", chat=True),
        _model("Qwen3.5-4B-Instruct-4bit", chat=True),
    ]
    assert resolve_chat_model(models, "auto") == "Qwen3.5-4B-Instruct-4bit"
    # No Qwen match → first chat-capable model.
    models = [_model("Qwen3.5-8B-Instruct", chat=True), _model("gpt", chat=True)]
    assert resolve_chat_model(models, "auto") == "Qwen3.5-8B-Instruct"


def test_qwen_discovery_implies_chat_without_explicit_metadata() -> None:
    models = [_model("Qwen3.5-4B-Instruct-gguf-q4_k_m")]
    assert resolve_chat_model(models, "auto") == "Qwen3.5-4B-Instruct-gguf-q4_k_m"
    caps = aggregate_capabilities(models)
    assert caps.chat is True
    assert caps.embedding is False
    assert caps.rerank is False


def test_no_chat_model_is_a_503_model_not_found() -> None:
    models = [_model("text-embedding-nomic-embed-text", embedding=True)]
    with pytest.raises(AIError) as info:
        resolve_chat_model(models, "auto")
    assert info.value.code == AIErrorCode.MODEL_NOT_FOUND
    assert info.value.status_code == 503


def test_explicit_model_must_be_discovered_and_chat_capable() -> None:
    models = [_model("Qwen3.5-4B-Instruct-4bit", chat=True)]
    assert resolve_chat_model(models, "Qwen3.5-4B-Instruct-4bit") == "Qwen3.5-4B-Instruct-4bit"
    with pytest.raises(AIError) as info:
        resolve_chat_model(models, "missing-model")
    assert info.value.code == AIErrorCode.MODEL_NOT_FOUND
    assert info.value.status_code == 400
    # Present but embedding-only is not a chat model.
    with pytest.raises(AIError) as info:
        resolve_chat_model(models, "text-embedding-nomic-embed-text")
    assert info.value.code == AIErrorCode.MODEL_NOT_FOUND


def test_aggregate_capabilities_whitelists_and_never_fakes() -> None:
    models = [
        _model("Qwen3.5-4B-Instruct", chat=True),
        _model("bge-reranker-v2", rerank=True),
    ]
    caps = aggregate_capabilities(models)
    assert caps == AICapabilities(chat=True, embedding=False, rerank=True)
    caps = aggregate_capabilities([_model("llama-3.1-8b")])
    assert caps == AICapabilities(chat=False, embedding=False, rerank=False)


def test_custom_qwen_pattern_is_honored() -> None:
    models = [_model("Qwen3.5-32B-Instruct", chat=True), _model("Qwen3.5-4B-Instruct", chat=True)]
    pattern = r"qwen3\.?5[-_ ]?32b"
    assert resolve_chat_model(models, "auto", pattern) == "Qwen3.5-32B-Instruct"
