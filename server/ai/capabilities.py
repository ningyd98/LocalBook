"""Capability normalization and deterministic model resolution."""

from __future__ import annotations

from .errors import AIError, AIErrorCode
from .matching import matches_qwen35_4b
from .schemas import DiscoveredModel


def aggregate_capabilities(models: list[DiscoveredModel]):
    from .schemas import AICapabilities

    return AICapabilities(
        chat=any(m.capabilities.chat or matches_qwen35_4b(m.id) for m in models),
        embedding=any(m.capabilities.embedding for m in models),
        rerank=any(m.capabilities.rerank for m in models),
    )


def resolve_chat_model(
    models: list[DiscoveredModel], requested: str, pattern: str = r"qwen3\.?5[-_ ]?4b"
) -> str:
    chat = [m for m in models if m.capabilities.chat or matches_qwen35_4b(m.id, pattern)]
    if requested != "auto":
        if not any(m.id == requested for m in chat):
            raise AIError(
                AIErrorCode.MODEL_NOT_FOUND, "Requested model is unavailable", status_code=400
            )
        return requested
    if not chat:
        raise AIError(AIErrorCode.MODEL_NOT_FOUND, "No chat model is available")
    q = next((m.id for m in chat if matches_qwen35_4b(m.id, pattern)), None)
    return q or chat[0].id
