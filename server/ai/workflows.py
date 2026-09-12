"""Read-only structured AI workflows.

Every workflow follows the same pipeline (PLAN-M6 §5.5–§5.7):

1. validate the request DTO (FastAPI) and read note content only through the
   injected Vault/index services;
2. build a bounded context through ContextBuilder (dedupe, safe paths,
   heading slices, char/note limits);
3. resolve the chat model and load the prompt body + version from the
   PromptRegistry — prompt text is the registry's Markdown body, never a
   hardcoded string;
4. hand the model a JSON response schema with service-owned fields stripped
   (S3), then parse + validate its output strictly and locally;
5. overwrite service-owned fields (prompt_version/model/note_path/degraded)
   and enforce the reference allow-list (citations ⊆ context, related ⊆
   candidates).

There is no AI write path here: nothing in this module mutates Markdown,
frontmatter, SQLite, graph, history or settings.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from .adapters.base import ChatMessage, ModelAdapter
from .candidates import reduce_candidates
from .capabilities import resolve_chat_model
from .context import ContextBuilder, ContextSource
from .errors import AIAdapterError, AIError, AIErrorCode
from .registry import Prompt, PromptRegistry
from .schemas import (
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

# Fields the service fills after local validation.  They are stripped from the
# response schema handed to the model (the model must not forge them) and are
# overwritten on every parsed result.
_SERVER_OWNED_FIELDS = (
    "prompt_version",
    "model",
    "note_path",
    "candidates_considered",
    "degraded",
)


def _terms_for_query(title: str, tags: list[str], body: str, *, limit: int = 8) -> str:
    """Derive a bounded, deterministic query from note title/tags/keywords.

    Used by ``related()`` to drive the programmatic FTS/substring candidate
    step.  Whitespace-tokenized; every token is casefolded and de-duplicated,
    with a hard cap so a huge note can never produce an oversized query.
    """
    seen: list[str] = []
    for token in (title + " " + " ".join(tags) + " " + body[:2000]).split():
        folded = token.casefold()
        if len(folded) >= 2 and folded not in seen and not folded.isdigit():
            seen.append(folded)
        if len(seen) >= limit:
            break
    return " ".join(seen)


class AIWorkflowService:
    def __init__(
        self,
        settings,
        adapter: ModelAdapter,
        *,
        vault=None,
        index=None,
        rag=None,
        registry=None,
    ):
        self.settings, self.adapter, self.vault, self.index = settings, adapter, vault, index
        # Optional M14 RAG stack. When present, ``related`` reuses the same
        # chunk-level evidence retrieval that grounds RAG answers (M14 §八),
        # so semantic neighbours are found without any model call; when absent
        # the M4 FTS/substring + link/graph path is used unchanged.
        self.rag = rag
        self.registry = registry or PromptRegistry()
        self.last_prompt_version: str | None = None
        self.last_model: str | None = None
        self.builder = ContextBuilder(
            settings.max_context_notes,
            settings.max_context_chars_per_note,
            settings.max_context_chars_total,
        )

    async def _model(self) -> str:
        try:
            return resolve_chat_model(
                await self.adapter.list_models(),
                self.settings.chat_model,
                self.settings.qwen_match_pattern,
            )
        except AIError:
            raise
        except AIAdapterError:
            raise
        except Exception as exc:
            raise AIError(AIErrorCode.UNAVAILABLE, "AI provider unavailable") from exc

    def _source(self, path: str) -> ContextSource:
        if not self.vault:
            raise AIError(AIErrorCode.NOT_CONFIGURED, "Note context is unavailable")
        try:
            data, _ = self.vault.read_bytes(path)
        except Exception as exc:
            code = getattr(exc, "code", None)
            if str(getattr(code, "value", code or "")) in {
                "path_traversal",
                "invalid_request",
                "symlink_escape",
            }:
                # Path-shape attacks stay a client error (400), never 404.
                raise AIError(
                    AIErrorCode.INVALID_REQUEST, "Note path is invalid", status_code=400
                ) from exc
            # Other Vault domain errors intentionally map to a stable
            # not_found response.
            raise AIError(AIErrorCode.NOT_FOUND, "Note not found", status_code=404) from exc
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AIError(
                AIErrorCode.INVALID_REQUEST, "Note is not readable", status_code=400
            ) from exc
        return ContextSource(path=path, title=Path(path).stem, text=text)

    def _prompt(self, name: str) -> Prompt:
        try:
            return self.registry.get(name)
        except (KeyError, ValueError) as exc:
            raise AIError(
                AIErrorCode.INTERNAL_ERROR, "AI prompt is unavailable", status_code=503
            ) from exc

    @staticmethod
    def _strip_server_fields(schema: dict[str, object]) -> dict[str, object]:
        """Remove service-owned properties + their required entries (S3)."""
        properties = schema.get("properties")
        if isinstance(properties, dict):
            for field in _SERVER_OWNED_FIELDS:
                properties.pop(field, None)
        required = schema.get("required")
        if isinstance(required, list):
            schema["required"] = [name for name in required if name in properties]
        return schema

    async def _run(self, name: str, prompt: str, model_cls: type[BaseModel]):
        model = await self._model()
        registered = self._prompt(name)
        self.last_prompt_version = registered.prompt_version
        self.last_model = model
        response_schema = self._strip_server_fields(model_cls.model_json_schema())
        try:
            result = await self.adapter.chat(
                model=model,
                messages=[
                    ChatMessage(
                        "system",
                        registered.body + "\nReturn only JSON matching the output schema.",
                    ),
                    ChatMessage("user", prompt),
                ],
                temperature=self.settings.temperature,
                response_schema=response_schema,
                timeout_seconds=self.settings.request_timeout_seconds,
                max_output_tokens=self.settings.max_output_tokens,
            )
        except AIAdapterError as exc:
            # Structured failures still report which prompt/model attempt failed.
            exc.meta = {"prompt_version": registered.prompt_version, "model": model}
            raise
        except Exception as exc:
            error = AIError(AIErrorCode.UNAVAILABLE, "AI provider unavailable")
            error.meta = {"prompt_version": registered.prompt_version, "model": model}
            raise error from exc
        try:
            raw = result.content.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
            value = model_cls.model_validate(json.loads(raw))
        except Exception as exc:
            error = AIError(
                AIErrorCode.INVALID_OUTPUT, "AI returned invalid structured output", status_code=502
            )
            error.meta = {"prompt_version": registered.prompt_version, "model": model}
            raise error from exc
        # Fill server-owned metadata; the model is never allowed to choose it.
        updates = {
            "prompt_version": registered.prompt_version,
            "model": model,
            "degraded": False,
        }
        return value.model_copy(
            update={k: v for k, v in updates.items() if k in model_cls.model_fields}
        )

    async def chat(self, request: ChatRequest) -> ChatResponse:
        sources = [self._source(request.note_path)] if request.note_path else []
        for path in request.context_note_paths:
            if path != request.note_path:
                sources.append(self._source(path))
        context = self.builder.build(sources)
        prompt = "\n".join([request.question, *[n.text for n in context.notes]])
        result = await self._run("chat", prompt, ChatResponse)
        allowed = {n.path for n in context.notes}
        citations = [c for c in result.citations if c.path in allowed]
        return result.model_copy(update={"citations": citations})

    async def summarize(self, request: SummarizeRequest) -> SummarizeResponse:
        source = self.builder.build([self._source(request.note_path)])
        result = await self._run(
            "summarize_note",
            "Summarize:\n" + "\n".join(n.text for n in source.notes),
            SummarizeResponse,
        )
        return result.model_copy(update={"note_path": request.note_path})

    async def tags(self, request: TagsRequest) -> TagsResponse:
        source = self.builder.build([self._source(request.note_path)])
        result = await self._run(
            "generate_tags",
            "Generate tags:\n" + "\n".join(n.text for n in source.notes),
            TagsResponse,
        )
        return result.model_copy(update={"note_path": request.note_path})

    async def related(self, request: RelatedRequest) -> RelatedResponse:
        if self.index is None:
            raise AIError(AIErrorCode.INDEX_UNAVAILABLE, "Index is unavailable")
        # Programmatic narrowing first (PLAN-M6 §5.6): FTS/substring driven by
        # the current note's title/keywords, merged with link/graph neighbors,
        # bounded and allow-listed before any model call.
        current = self._source(request.note_path)
        entry = None
        try:
            entry = self.index.entry(request.note_path)
        except Exception:
            entry = None
        title = getattr(entry, "title", None) or Path(request.note_path).stem
        tags = list(getattr(entry, "tags", None) or [])
        query = _terms_for_query(title, tags, current.text)
        rag_hits = self._rag_candidates(query, exclude=request.note_path)
        candidates = reduce_candidates(
            self.index,
            request.note_path,
            limit=self.settings.max_context_notes - 1,
            query=query,
            rag_hits=rag_hits,
        )
        if not candidates:
            registered = self._prompt("suggest_links")
            return RelatedResponse(
                note_path=request.note_path,
                related=[],
                candidates_considered=0,
                prompt_version=registered.prompt_version,
                model=None,
                degraded=True,
            )
        sources = [current] + [self._source(c.path) for c in candidates]
        context = self.builder.build(sources)
        result = await self._run(
            "suggest_links",
            "Suggest related notes from candidates only:\n"
            + "\n".join(n.text for n in context.notes),
            RelatedResponse,
        )
        allowed = {c.path for c in candidates}
        result = result.model_copy(
            update={
                "note_path": request.note_path,
                "candidates_considered": len(allowed),
                "related": [r for r in result.related if r.path in allowed][: request.limit],
            }
        )
        return result

    def _rag_candidates(self, query: str, *, exclude: str) -> list:
        """Chunk-level evidence for ``related`` from the optional RAG stack.

        Reuses :class:`HybridRetriever` (FTS + vector + RRF), so the candidate
        set is derived from the Vault's own chunks — the same boundary the RAG
        answer path uses. It never calls the chat model and never invents a
        path; any failure (no RAG stack, no embeddings, empty query) simply
        returns ``[]`` and the caller falls back to the M4 path.
        """
        stack = self.rag
        if stack is None or not query.strip():
            return []
        limit = max(1, self.settings.max_context_notes - 1)
        try:
            outcome = stack.retriever.search(query, top_k=limit * 3)
        except Exception:  # pragma: no cover - retrieval degrades, never raises
            return []
        seen: set[str] = set()
        hits: list = []
        for item in outcome.results:
            path = getattr(item, "path", None)
            if not isinstance(path, str) or not path or path == exclude:
                continue
            if path in seen:
                continue
            seen.add(path)
            hits.append(item)
            if len(hits) >= limit:
                break
        return hits

    async def extract_todos(self, request: ExtractTodosRequest) -> ExtractTodosResponse:
        source = self.builder.build([self._source(request.note_path)])
        result = await self._run(
            "extract_todos",
            "Extract todos:\n" + "\n".join(n.text for n in source.notes),
            ExtractTodosResponse,
        )
        return result.model_copy(update={"note_path": request.note_path})

    async def classify(self, request: ClassifyRequest) -> ClassifyResponse:
        source = self.builder.build([self._source(request.note_path)])
        prompt = "Classify:\n" + "\n".join(n.text for n in source.notes)
        if request.labels:
            prompt += "\nRequested labels (choose only from these): " + json.dumps(
                request.labels, ensure_ascii=False
            )
        result = await self._run("classify_note", prompt, ClassifyResponse)
        # Server-side allow-list (audit S3), consistent with citations/related:
        # when the client restricted the label set, the model may only pick
        # from it.  Out-of-set alternatives are dropped and an out-of-set
        # label is emptied instead of being trusted; without a restricted set
        # the model's free-form label is passed through.
        if request.labels:
            allowed = set(request.labels)
            label = result.label if result.label in allowed else ""
            alternatives = [item for item in result.alternatives if item in allowed]
        else:
            label, alternatives = result.label, result.alternatives
        return result.model_copy(
            update={
                "note_path": request.note_path,
                "label": label,
                "alternatives": alternatives,
            }
        )
