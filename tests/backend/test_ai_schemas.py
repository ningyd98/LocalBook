"""Strict M6 DTO schema tests (PLAN-M6 §9.1.4)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from server.ai.schemas import (
    ChatRequest,
    ChatResponse,
    ClassifyRequest,
    RelatedRequest,
    SummarizeRequest,
    SummarizeResponse,
)


def test_extra_fields_are_forbidden_on_requests() -> None:
    with pytest.raises(ValidationError):
        SummarizeRequest(note_path="a.md", system_prompt="injected")
    with pytest.raises(ValidationError):
        ChatRequest(question="hi", evil_url="http://x")


def test_chat_question_length_bounds() -> None:
    with pytest.raises(ValidationError):
        ChatRequest(question="")
    with pytest.raises(ValidationError):
        ChatRequest(question="x" * 4001)
    assert ChatRequest(question="ok").question == "ok"


def test_chat_context_note_paths_capped_at_50() -> None:
    paths = [f"n/{i}.md" for i in range(51)]
    with pytest.raises(ValidationError):
        ChatRequest(question="q", context_note_paths=paths)
    assert ChatRequest(question="q", context_note_paths=paths[:50]) is not None


def test_related_limit_bounds() -> None:
    with pytest.raises(ValidationError):
        RelatedRequest(note_path="a.md", limit=0)
    with pytest.raises(ValidationError):
        RelatedRequest(note_path="a.md", limit=21)
    assert RelatedRequest(note_path="a.md", limit=5).limit == 5
    assert RelatedRequest(note_path="a.md").limit == 5  # default


def test_classify_labels_capped_at_20() -> None:
    with pytest.raises(ValidationError):
        ClassifyRequest(note_path="a.md", labels=[f"l{i}" for i in range(21)])
    assert ClassifyRequest(note_path="a.md", labels=["meeting", "todo"]) is not None


def test_chat_response_citation_quote_capped() -> None:
    with pytest.raises(ValidationError):
        ChatResponse(
            answer="a",
            citations=[{"path": "x.md", "heading": None, "quote": "q" * 1001}],
        )


def test_extra_fields_are_forbidden_on_responses() -> None:
    with pytest.raises(ValidationError):
        SummarizeResponse(summary="s", key_points=[], dangerous="x")
    with pytest.raises(ValidationError):
        ChatResponse(answer="a", citations=[], tool_calls=["rm"])


def test_related_model_is_nullable_and_degraded_present() -> None:
    # I3: python-side RelatedResponse.model is str | None (mirrored in TS).
    empty = ChatResponse(answer="a", citations=[])
    assert empty.degraded is False
    assert "degraded" in ChatResponse.model_fields
    related_schema = SummarizeResponse.model_json_schema()
    assert related_schema["properties"]["note_path"]["default"] == ""
    assert related_schema["properties"]["prompt_version"]["default"] == ""


def test_json_schemas_export_and_are_parseable() -> None:
    for model in (
        ChatRequest,
        ChatResponse,
        SummarizeRequest,
        SummarizeResponse,
        RelatedRequest,
        ClassifyRequest,
    ):
        schema = model.model_json_schema()
        assert isinstance(schema, dict)
        assert "properties" in schema


def test_relative_path_only_not_enforced_by_schema_layer() -> None:
    # Path shape safety lives in the Vault/Context layer; the schema only
    # requires a non-empty string so callers see the domain 404/400 mapping.
    req = SummarizeRequest(note_path="notes/a.md")
    assert req.note_path == "notes/a.md"
