"""Provider-neutral adapter contracts and internal DTOs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..errors import CapabilityUnavailable
from ..schemas import DiscoveredModel


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


@dataclass(frozen=True)
class ChatResult:
    content: str
    model: str


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: list[list[float]]


@dataclass(frozen=True)
class RerankResult:
    indices: list[int]
    scores: list[float]


class ModelAdapter(Protocol):
    async def list_models(self) -> list[DiscoveredModel]: ...
    async def chat(
        self,
        *,
        model: str,
        messages: list[ChatMessage],
        temperature: float,
        response_schema: dict[str, object] | None,
        timeout_seconds: float,
        max_output_tokens: int = 1200,
    ) -> ChatResult: ...
    async def embed(
        self, *, model: str, inputs: list[str], timeout_seconds: float
    ) -> EmbeddingResult: ...
    async def rerank(
        self, *, model: str, query: str, documents: list[str], timeout_seconds: float
    ) -> RerankResult: ...


__all__ = [
    "ModelAdapter",
    "ChatMessage",
    "ChatResult",
    "EmbeddingResult",
    "RerankResult",
    "CapabilityUnavailable",
]
