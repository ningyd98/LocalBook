"""Prompt Registry contract tests (PLAN-M6 §9.1.6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from server.ai import schemas as ai_schemas
from server.ai.registry import PromptRegistry, load_prompt
from server.ai.schemas import (
    ChatRequest,
    ChatResponse,
    ClassifyRequest,
    ClassifyResponse,
    ExtractTodosRequest,
    ExtractTodosResponse,
    RelatedRequest,
    RelatedResponse,
    SummarizeRequest,
    SummarizeResponse,
    TagsRequest,
    TagsResponse,
)


def test_default_registry_exposes_exactly_the_six_m6_prompts() -> None:
    registry = PromptRegistry()
    prompts = registry.all()
    assert {prompt.name for prompt in prompts} == {
        "chat",
        "summarize_note",
        "generate_tags",
        "suggest_links",
        "extract_todos",
        "classify_note",
    }
    versions = {prompt.version for prompt in prompts}
    assert versions == {"m6.1"}


def test_prompt_metadata_schema_names_resolve_to_pydantic_models() -> None:
    expected = {
        "chat": (ChatRequest, ChatResponse),
        "summarize_note": (SummarizeRequest, SummarizeResponse),
        "generate_tags": (TagsRequest, TagsResponse),
        "suggest_links": (RelatedRequest, RelatedResponse),
        "extract_todos": (ExtractTodosRequest, ExtractTodosResponse),
        "classify_note": (ClassifyRequest, ClassifyResponse),
    }
    registry = PromptRegistry()
    by_name = {prompt.name: prompt for prompt in registry.all()}
    for name, (input_cls, output_cls) in expected.items():
        prompt = by_name[name]
        assert getattr(ai_schemas, prompt.input_schema) is input_cls
        assert getattr(ai_schemas, prompt.output_schema) is output_cls


def test_get_returns_single_prompt_and_version_string() -> None:
    registry = PromptRegistry()
    prompt = registry.get("chat")
    assert prompt.prompt_version == "chat@m6.1"
    assert registry.get("chat", "m6.1").prompt_version == "chat@m6.1"
    assert prompt.body  # body text really exists
    assert "Answer using only the supplied" in prompt.body


def test_unknown_or_ambiguous_prompt_raises_key_error() -> None:
    registry = PromptRegistry()
    with pytest.raises(KeyError):
        registry.get("not_a_prompt")
    with pytest.raises(KeyError):
        registry.get("chat", "v99")  # safe failure on unknown version


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "p.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_missing_frontmatter_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Invalid prompt metadata"):
        load_prompt(_write(tmp_path, "no frontmatter here"))


def test_incomplete_metadata_rejected(tmp_path: Path) -> None:
    text = "---\nname: chat\nversion: m6.1\n---\nbody"
    with pytest.raises(ValueError, match="Incomplete prompt metadata"):
        load_prompt(_write(tmp_path, text))


def test_bad_schema_name_rejected(tmp_path: Path) -> None:
    text = (
        "---\nname: chat\nversion: m6.1\n"
        "input_schema: NotARealSchema\noutput_schema: ChatResponse\n---\nbody"
    )
    with pytest.raises(ValueError, match="not parseable"):
        load_prompt(_write(tmp_path, text))


def test_empty_body_rejected(tmp_path: Path) -> None:
    text = (
        "---\nname: chat\nversion: m6.1\n"
        "input_schema: ChatRequest\noutput_schema: ChatResponse\n---\n   \n"
    )
    with pytest.raises(ValueError, match="empty"):
        load_prompt(_write(tmp_path, text))


def test_duplicate_name_version_rejected_across_files(tmp_path: Path) -> None:
    text = (
        "---\nname: chat\nversion: m6.1\n"
        "input_schema: ChatRequest\noutput_schema: ChatResponse\n---\nbody one\n"
    )
    (tmp_path / "a.md").write_text(text, encoding="utf-8")
    (tmp_path / "b.md").write_text(text, encoding="utf-8")
    registry = PromptRegistry(directory=tmp_path)
    with pytest.raises(ValueError, match="must be unique"):
        registry.all()


def test_invalid_prompt_name_rejected(tmp_path: Path) -> None:
    text = (
        "---\nname: bad name!\nversion: m6.1\n"
        "input_schema: ChatRequest\noutput_schema: ChatResponse\n---\nbody\n"
    )
    with pytest.raises(ValueError, match="Invalid prompt name"):
        load_prompt(_write(tmp_path, text))
