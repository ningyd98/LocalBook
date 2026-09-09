"""AI status and strict read-only workflow DTOs."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AIStatus(StrEnum):
    NOT_CONFIGURED = "not_configured"
    OFFLINE = "offline"
    CONNECTED = "connected"


class AICapabilities(BaseModel):
    chat: bool = False
    embedding: bool = False
    rerank: bool = False


class DiscoveredModel(BaseModel):
    id: str
    owned_by: str | None = None
    capabilities: AICapabilities = Field(default_factory=AICapabilities)


AIErrorCode = Literal[
    "not_configured",
    "connection_refused",
    "timeout",
    "http_error",
    "auth_error",
    "invalid_response",
    "no_matching_model",
    "unknown",
]


class AIStatusResponse(BaseModel):
    status: AIStatus
    provider: Literal["omlx"] = "omlx"
    endpoint: str | None = None
    qwen_model: str | None = None
    selected_model: str | None = None
    models: list[DiscoveredModel] = Field(default_factory=list)
    capabilities: AICapabilities = Field(default_factory=AICapabilities)
    error_code: AIErrorCode | None = None
    message: str | None = None
    checked_at: datetime | None = None


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChatRequest(StrictModel):
    note_path: str | None = None
    question: str = Field(min_length=1, max_length=4000)
    context_note_paths: list[str] = Field(default_factory=list, max_length=50)


class ChatCitation(StrictModel):
    path: str
    heading: str | None = None
    quote: str = Field(max_length=1000)


class ChatResponse(StrictModel):
    answer: str
    citations: list[ChatCitation] = Field(default_factory=list, max_length=20)
    # Server-owned fields: filled by AIWorkflowService after local validation.
    # Defaults exist only so strict parsing tolerates their absence when the
    # wire schema handed to the model strips them (PLAN-M6 §5.5 / S3).
    prompt_version: str = ""
    model: str = ""
    degraded: bool = False


class NoteRequest(StrictModel):
    note_path: str


class SummarizeRequest(NoteRequest):
    pass


class TagsRequest(NoteRequest):
    pass


class RelatedRequest(NoteRequest):
    limit: int = Field(default=5, ge=1, le=20)


class ExtractTodosRequest(NoteRequest):
    pass


class ClassifyRequest(NoteRequest):
    labels: list[str] = Field(default_factory=list, max_length=20)


class SummarizeResponse(StrictModel):
    note_path: str = ""
    summary: str
    key_points: list[str] = Field(default_factory=list)
    # Server-owned fields (filled by AIWorkflowService; defaults for strict parse).
    prompt_version: str = ""
    model: str = ""
    degraded: bool = False


class TagSuggestion(StrictModel):
    name: str
    reason: str


class TagsResponse(StrictModel):
    note_path: str = ""
    tags: list[TagSuggestion]
    # Server-owned fields (filled by AIWorkflowService; defaults for strict parse).
    prompt_version: str = ""
    model: str = ""
    degraded: bool = False


class RelatedSuggestion(StrictModel):
    path: str
    title: str
    reason: str
    score: float = 0


class RelatedResponse(StrictModel):
    note_path: str = ""
    related: list[RelatedSuggestion] = Field(default_factory=list)
    candidates_considered: int = 0
    # Server-owned fields (filled by AIWorkflowService; defaults for strict parse).
    prompt_version: str = ""
    model: str | None = None
    degraded: bool = False


class TodoItem(StrictModel):
    text: str
    source_heading: str | None = None
    due_hint: str | None = None


class ExtractTodosResponse(StrictModel):
    note_path: str = ""
    items: list[TodoItem] = Field(default_factory=list)
    # Server-owned fields (filled by AIWorkflowService; defaults for strict parse).
    prompt_version: str = ""
    model: str = ""
    degraded: bool = False


class ClassifyResponse(StrictModel):
    note_path: str = ""
    label: str
    confidence: float = Field(ge=0, le=1)
    alternatives: list[str] = Field(default_factory=list)
    # Server-owned fields (filled by AIWorkflowService; defaults for strict parse).
    prompt_version: str = ""
    model: str = ""
    degraded: bool = False
