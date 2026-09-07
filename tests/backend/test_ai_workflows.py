"""Six read-only workflow tests (PLAN-M6 §9.1.7): success, degrade, prompts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.ai.adapters.base import ChatResult
from server.ai.errors import AIAdapterError, AIError
from server.ai.registry import PromptRegistry
from server.ai.schemas import (
    AICapabilities,
    ChatRequest,
    ClassifyRequest,
    DiscoveredModel,
    ExtractTodosRequest,
    RelatedRequest,
    SummarizeRequest,
    TagsRequest,
)
from server.ai.workflows import AIWorkflowService
from server.config import AISettings


class FakeAdapter:
    """Records every chat call; serves canned content or raises."""

    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []
        self.list_models_calls = 0

    async def list_models(self) -> list[DiscoveredModel]:
        self.list_models_calls += 1
        return [
            DiscoveredModel(
                id="Qwen3.5-4B-Instruct-4bit",
                capabilities=AICapabilities(chat=True),
            )
        ]

    async def chat(self, **kwargs) -> ChatResult:
        self.calls.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return ChatResult(content=item, model="Qwen3.5-4B-Instruct-4bit")

    async def embed(self, **kwargs) -> None:
        raise AIAdapterError("capability_unavailable")

    async def rerank(self, **kwargs) -> None:
        raise AIAdapterError("capability_unavailable")


@pytest.fixture
def registry() -> PromptRegistry:
    return PromptRegistry()


def make_service(vault=None, index=None, responses: list[object] | None = None):
    adapter = FakeAdapter(responses or ['{"summary": "s", "key_points": []}'])
    service = AIWorkflowService(AISettings(), adapter, vault=vault, index=index)
    return service, adapter


@pytest.fixture
def note_vault(vault_service_factory, tmp_path: Path):
    """Throwaway vault with plain Markdown notes (no index)."""
    root = tmp_path / "vault"
    root.mkdir()
    (root / "alpha.md").write_text(
        "# Alpha\nrocket science quantum widgets\nShared project notes.", encoding="utf-8"
    )
    (root / "beta.md").write_text(
        "# Beta\nbudget planning\nTotally unrelated diary.", encoding="utf-8"
    )
    (root / "lonely.md").write_text(
        "# Lonely\nzzzqqqxx unique nobody matches this.", encoding="utf-8"
    )
    return vault_service_factory(root)


@pytest.fixture
def linked_vault(vault_service_factory, index_service_factory, tmp_path: Path):
    """Vault + real rebuilt index with a link from me.md → alpha.md."""
    root = tmp_path / "vault"
    root.mkdir()
    (root / "me.md").write_text(
        "# Me\nrocket science quantum widgets\nRead [[alpha]] next.", encoding="utf-8"
    )
    (root / "alpha.md").write_text(
        "# Alpha\nrocket science quantum widgets notes.", encoding="utf-8"
    )
    (root / "other.md").write_text(
        "# Other\ncompletely unrelated stuff here.", encoding="utf-8"
    )
    vault = vault_service_factory(root)
    index = index_service_factory(vault)
    return vault, index


def _user_message(adapter: FakeAdapter, call_index: int = 0) -> str:
    messages = adapter.calls[call_index]["messages"]
    return next(m.content for m in messages if m.role == "user")


def _system_message(adapter: FakeAdapter, call_index: int = 0) -> str:
    messages = adapter.calls[call_index]["messages"]
    return next(m.content for m in messages if m.role == "system")


async def test_chat_prompt_body_version_context_and_citation_allowlist(
    note_vault, registry: PromptRegistry
) -> None:
    service, adapter = make_service(
        note_vault,
        responses=[
            json.dumps(
                {
                    "answer": "ans",
                    "citations": [
                        {"path": "alpha.md", "heading": None, "quote": "q"},
                        {"path": "ghost.md", "heading": None, "quote": "nope"},
                    ],
                    "prompt_version": "forged@x",
                    "model": "forged-model",
                }
            )
        ],
    )
    result = await service.chat(ChatRequest(note_path="alpha.md", question="What?"))
    assert result.answer == "ans"
    # Citation allow-list (I2): ghost.md is outside this round's context.
    assert [c.path for c in result.citations] == ["alpha.md"]
    assert result.prompt_version == registry.get("chat").prompt_version
    assert result.model == "Qwen3.5-4B-Instruct-4bit"
    assert result.degraded is False
    # B2: the real prompt file body reaches the model.
    assert registry.get("chat").body in _system_message(adapter)
    user = _user_message(adapter)
    assert "What?" in user and "rocket science" in user


async def test_chat_context_note_paths_expands_context(note_vault) -> None:
    service, adapter = make_service(
        note_vault, responses=[json.dumps({"answer": "ok", "citations": []})]
    )
    result = await service.chat(
        ChatRequest(question="q", context_note_paths=["alpha.md", "beta.md"])
    )
    assert result.answer == "ok"
    user = _user_message(adapter)
    assert "Shared project notes" in user and "budget planning" in user


async def test_chat_without_vault_can_answer_with_empty_context() -> None:
    service, adapter = make_service(
        vault=None, responses=[json.dumps({"answer": "hi", "citations": []})]
    )
    result = await service.chat(ChatRequest(question="hello"))
    assert result.answer == "hi"
    assert result.citations == []
    assert _user_message(adapter) == "hello"


async def test_chat_with_note_path_but_no_vault_is_not_configured() -> None:
    service, adapter = make_service(vault=None)
    with pytest.raises(AIError) as info:
        await service.chat(ChatRequest(note_path="a.md", question="q"))
    assert info.value.code.value == "ai_not_configured"
    assert info.value.status_code == 503
    assert adapter.calls == []


async def test_missing_note_is_404_before_model(note_vault) -> None:
    service, adapter = make_service(note_vault)
    with pytest.raises(AIError) as info:
        await service.tags(TagsRequest(note_path="nope.md"))
    assert info.value.code.value == "not_found"
    assert info.value.status_code == 404
    assert adapter.calls == []


async def test_non_utf8_note_is_400(vault_service_factory, tmp_path: Path) -> None:
    root = tmp_path / "bin"
    root.mkdir()
    (root / "bad.md").write_bytes(b"# bad\n\xff\xfe binary garbage")
    vault = vault_service_factory(root)
    service, adapter = make_service(vault)
    with pytest.raises(AIError) as info:
        await service.tags(TagsRequest(note_path="bad.md"))
    assert info.value.code.value == "ai_invalid_request"
    assert info.value.status_code == 400
    assert adapter.calls == []


async def test_server_fields_are_overwritten_after_strict_validation(
    note_vault, registry: PromptRegistry
) -> None:
    forged = json.dumps(
        {
            "summary": "real summary",
            "key_points": ["a", "b"],
            "note_path": "forged.md",
            "prompt_version": "forged@0",
            "model": "forged-model",
            "degraded": True,
        }
    )
    service, adapter = make_service(note_vault, responses=[forged])
    result = await service.summarize(SummarizeRequest(note_path="alpha.md"))
    assert result.note_path == "alpha.md"
    assert result.prompt_version == registry.get("summarize_note").prompt_version
    assert result.model == "Qwen3.5-4B-Instruct-4bit"
    assert result.degraded is False
    assert result.summary == "real summary"
    assert result.key_points == ["a", "b"]


async def test_schema_given_to_model_strips_server_fields(note_vault) -> None:
    service, adapter = make_service(
        note_vault, responses=['{"tags": [{"name": "x", "reason": "r"}]}']
    )
    await service.tags(TagsRequest(note_path="alpha.md"))
    schema = adapter.calls[0]["response_schema"]
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    for forbidden in ("prompt_version", "model", "note_path", "degraded", "candidates_considered"):
        assert forbidden not in properties
        assert forbidden not in required
    assert "tags" in properties  # client-relevant fields remain


async def test_extract_todos_success(note_vault, registry: PromptRegistry) -> None:
    canned = json.dumps({"items": [{"text": "ship it", "source_heading": None}]})
    service, adapter = make_service(note_vault, responses=[canned])
    result = await service.extract_todos(ExtractTodosRequest(note_path="alpha.md"))
    assert result.items[0].text == "ship it"
    assert result.note_path == "alpha.md"
    assert result.prompt_version == registry.get("extract_todos").prompt_version


async def test_classify_injects_labels_into_prompt(note_vault, registry: PromptRegistry) -> None:
    canned = json.dumps({"label": "会议", "confidence": 0.9, "alternatives": []})
    service, adapter = make_service(note_vault, responses=[canned])
    result = await service.classify(
        ClassifyRequest(note_path="alpha.md", labels=["会议", "todo"])
    )
    assert result.label == "会议"
    assert result.note_path == "alpha.md"
    user = _user_message(adapter)
    assert "Requested labels" in user
    assert "会议" in user and "todo" in user
    assert result.prompt_version == registry.get("classify_note").prompt_version


async def test_classify_allowlist_empties_label_outside_requested_set(note_vault) -> None:
    # Audit S3: an out-of-set label must not be trusted; it is emptied and
    # out-of-set alternatives are dropped, mirroring the citations/related
    # allow-lists.
    canned = json.dumps(
        {"label": "随便", "confidence": 0.8, "alternatives": ["会议", "todo", "随便"]}
    )
    service, adapter = make_service(note_vault, responses=[canned])
    result = await service.classify(
        ClassifyRequest(note_path="alpha.md", labels=["会议", "todo"])
    )
    assert result.label == ""
    assert result.alternatives == ["会议", "todo"]
    assert result.confidence == 0.8  # only label/alternatives are filtered
    assert result.note_path == "alpha.md"


async def test_classify_allowlist_keeps_requested_label(note_vault) -> None:
    canned = json.dumps(
        {"label": "todo", "confidence": 0.6, "alternatives": ["会议", "外部"]}
    )
    service, adapter = make_service(note_vault, responses=[canned])
    result = await service.classify(
        ClassifyRequest(note_path="alpha.md", labels=["会议", "todo"])
    )
    assert result.label == "todo"
    assert result.alternatives == ["会议"]


async def test_classify_without_labels_is_not_allowlisted(note_vault) -> None:
    # No requested set → the model's free-form label/alternatives pass through.
    canned = json.dumps({"label": "free-form", "confidence": 0.7, "alternatives": ["a", "b"]})
    service, adapter = make_service(note_vault, responses=[canned])
    result = await service.classify(ClassifyRequest(note_path="alpha.md"))
    assert result.label == "free-form"
    assert result.alternatives == ["a", "b"]


async def test_related_filters_to_candidates_and_uses_final_version(
    linked_vault, registry: PromptRegistry
) -> None:
    vault, index = linked_vault
    canned = json.dumps(
        {
            "related": [
                {"path": "alpha.md", "title": "Alpha", "reason": "shared terms", "score": 0.9},
                {"path": "ghost.md", "title": "Ghost", "reason": "hallucinated", "score": 9.9},
            ],
            "candidates_considered": 99,
            "degraded": True,
        }
    )
    service, adapter = make_service(vault, index=index, responses=[canned])
    result = await service.related(RelatedRequest(note_path="me.md", limit=5))
    # Allow-list: only real candidate paths survive; server fields overwritten.
    assert [r.path for r in result.related] == ["alpha.md"]
    assert result.candidates_considered == 1
    assert result.degraded is False
    assert result.prompt_version == registry.get("suggest_links").prompt_version
    assert result.model == "Qwen3.5-4B-Instruct-4bit"


async def test_related_empty_candidates_never_calls_model(
    vault_service_factory, index_service_factory, tmp_path: Path, registry: PromptRegistry
) -> None:
    root = tmp_path / "rv"
    root.mkdir()
    (root / "lonely.md").write_text(
        "# Lonely\nzzzqqqxx unique nobody matches this text.", encoding="utf-8"
    )
    vault = vault_service_factory(root)
    index = index_service_factory(vault)
    service, adapter = make_service(vault, index=index)
    result = await service.related(RelatedRequest(note_path="lonely.md"))
    assert result.related == []
    assert result.candidates_considered == 0
    assert result.degraded is True
    assert result.model is None
    assert result.prompt_version == registry.get("suggest_links").prompt_version
    assert adapter.calls == []


async def test_invalid_output_is_502_with_meta(note_vault, registry: PromptRegistry) -> None:
    service, adapter = make_service(note_vault, responses=["<html>not json</html>"])
    with pytest.raises(AIError) as info:
        await service.tags(TagsRequest(note_path="alpha.md"))
    assert info.value.code.value == "ai_invalid_output"
    assert info.value.status_code == 502
    assert info.value.meta == {
        "prompt_version": registry.get("generate_tags").prompt_version,
        "model": "Qwen3.5-4B-Instruct-4bit",
    }


async def test_adapter_offline_is_503_with_meta(note_vault, registry: PromptRegistry) -> None:
    service, adapter = make_service(note_vault, responses=[AIAdapterError("offline")])
    with pytest.raises(AIAdapterError) as info:
        await service.tags(TagsRequest(note_path="alpha.md"))
    assert info.value.status_code == 503
    assert info.value.meta == {
        "prompt_version": registry.get("generate_tags").prompt_version,
        "model": "Qwen3.5-4B-Instruct-4bit",
    }


async def test_missing_prompt_registry_entry_is_internal_503(
    note_vault, tmp_path: Path
) -> None:
    service, adapter = make_service(note_vault)
    service.registry = PromptRegistry(directory=tmp_path)  # empty → lookup fails
    with pytest.raises(AIError) as info:
        await service.tags(TagsRequest(note_path="alpha.md"))
    assert info.value.code.value == "internal_error"
    assert info.value.status_code == 503
    assert adapter.calls == []
