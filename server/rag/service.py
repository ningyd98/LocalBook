"""RAG service: retrieve → rerank → EvidencePack → grounded answer (M14 §九).

The service is the only RAG entry point used by the HTTP layer, and it is
deliberately independent of the Web UI: it depends on a Vault-backed index, a
retriever, a context builder and a *generator callable*, so an
Obsidian-compatible host could reuse the whole chain with a different front end.

Grounding rules enforced here (never delegated to the model):

- retrieval runs first; if nothing is found the service answers with the fixed
  "not enough evidence" sentence and returns no sources;
- the model only ever sees :func:`render_evidence_pack` output (numbered,
  path/line-labelled evidence), never raw Vault files;
- every returned ``source`` is built from the pack, after citation validation,
  so a hallucinated path or source id can never reach the client;
- generation failure degrades to the evidence list instead of a 5xx.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from ..ai.errors import AIAdapterError, AIError
from .api_schemas import (
    RagAnswerPayload,
    RagIndexStatusResponse,
    RagQueryResponse,
    RagRetrievalStats,
    RagSearchHit,
    RagSearchResponse,
    RagSource,
)
from .citations import NOT_ENOUGH_EVIDENCE, ensure_grounded_answer, extract_citations
from .context.builder import RagContextBuilder, render_evidence_pack
from .embeddings.base import EmbeddingError
from .errors import RagInvalidRequest
from .index_service import RagIndexService
from .retrieval.base import RetrieverUnavailable
from .retrieval.hybrid import HybridRetriever
from .retrieval.keyword import validate_query
from .schemas import EvidencePack, RAGIndexState, RetrievalStats
from .vector.sqlite import RagStoreUnavailable

logger = logging.getLogger("localnote.rag.service")

GenerateCallable = Callable[[str, str], Awaitable[tuple[str, str]]]
"""``(system_prompt, user_prompt) -> (raw_json_content, model_name)``."""

_ANSWER_PROMPT: str | None = None


def _load_answer_prompt() -> str:
    """Load ``server/rag/prompts/rag_answer.md`` (project Markdown prompt)."""
    global _ANSWER_PROMPT
    if _ANSWER_PROMPT is None:
        from .prompts import load_prompt_by_name

        _ANSWER_PROMPT = load_prompt_by_name("rag_answer").body
    return _ANSWER_PROMPT


class RagService:
    """Orchestrates one RAG question end to end."""

    def __init__(
        self,
        *,
        index: RagIndexService,
        retriever: HybridRetriever,
        context_builder: RagContextBuilder | None = None,
        generate: GenerateCallable | None = None,
        prompt_version: str = "rag_answer@m14.1",
        context_top_k: int = 6,
        require_citation: bool = True,
    ) -> None:
        self._index = index
        self._retriever = retriever
        self._builder = context_builder or RagContextBuilder()
        self._generate = generate
        self.prompt_version = prompt_version
        self.context_top_k = max(1, int(context_top_k))
        self.require_citation = bool(require_citation)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def retriever(self) -> HybridRetriever:
        return self._retriever

    @property
    def adapter_configured(self) -> bool:
        return self._generate is not None

    def set_generator(self, generate: GenerateCallable | None) -> None:
        """Attach/replace the chat generator (settings can change at runtime)."""
        self._generate = generate

    def index_state(self) -> RAGIndexState:
        return self._index.index_status()

    def status(self) -> RagIndexStatusResponse:
        state = self._index.index_status()
        degraded = self._index.embedding_is_degraded
        message = ""
        if not self._index.enabled:
            message = "RAG is disabled in settings."
        elif degraded:
            message = (
                "No embedding endpoint is configured: semantic retrieval is "
                "using the local fallback embedder (lexical quality only)."
            )
        return RagIndexStatusResponse(
            enabled=self._index.enabled,
            status=state.status,
            embedding_provider=state.embedding_provider,
            embedding_model=state.embedding_model,
            embedding_dimension=state.embedding_dimension,
            embedding_version=state.embedding_version,
            embedding_degraded=degraded,
            vector_store=getattr(self._index.store, "name", "sqlite"),
            vector_kernel=(
                self._index.store.kernel()
                if hasattr(self._index.store, "kernel")
                else ""
            ),
            # The retriever owns this answer (wired / reachable / degraded
            # reason); the service only relays it. The defensive default keeps a
            # retriever that does not expose it from failing the status call —
            # "" then renders as "nothing to report", never as "enabled".
            link_retrieval=str(getattr(self._retriever, "link_retrieval", "") or ""),
            indexed_notes=state.indexed_documents,
            chunks=state.chunk_count,
            embedded_chunks=int(self._index.store.embedded_count()),
            pending=state.pending,
            failed=state.failed,
            last_indexed=state.last_indexed_at,
            chunk_target_tokens=int(getattr(self._index.chunker, "target_tokens", 0)),
            chunk_max_tokens=int(getattr(self._index.chunker, "max_tokens", 0)),
            message=message,
        )

    # ------------------------------------------------------------------
    # Retrieval-only path (debug/diagnostics; never calls the chat model)
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        include_debug: bool = False,
        rerank: bool | None = None,
    ) -> RagSearchResponse:
        _validate_or_raise(query)
        outcome = self._retriever.search(
            query, top_k=top_k, include_debug=include_debug, rerank=rerank
        )
        return RagSearchResponse(
            query=query,
            results=[
                RagSearchHit(
                    rank=rank,
                    source_id=item.chunk_id,
                    chunk_id=item.chunk_id,
                    path=item.path,
                    heading=item.heading,
                    heading_path=item.heading_path,
                    excerpt=_excerpt(item.content),
                    score=float(item.score),
                    keyword_rank=item.keyword_rank,
                    vector_rank=item.vector_rank,
                    rerank_score=item.rerank_score,
                    start_line=max(1, int(item.start_line)),
                    end_line=max(1, int(item.end_line)),
                )
                for rank, item in enumerate(outcome.results, start=1)
            ],
            stats=_stats_to_wire(outcome.stats),
            degraded=list(outcome.stats.degraded),
            generated_at=datetime.now(UTC),
        )

    # ------------------------------------------------------------------
    # Full RAG path
    # ------------------------------------------------------------------

    async def query(
        self,
        query: str,
        *,
        top_k: int | None = None,
        rerank: bool | None = None,
        include_debug: bool = False,
    ) -> RagQueryResponse:
        _validate_or_raise(query)
        if not self._index.enabled:
            stats = RetrievalStats(degraded=["rag_disabled"])
            return RagQueryResponse(
                query=query,
                answer="知识库检索（RAG）已在设置中关闭，因此没有检索你的笔记。",
                sources=[],
                evidence=_evidence_summary(EvidencePack(query=query), stats),
                retrieval_stats=_stats_to_wire(stats),
                degraded=list(stats.degraded),
                generated_at=datetime.now(UTC),
            )

        use_reranker = self._retriever.reranker_enabled if rerank is None else bool(rerank)
        outcome = self._retrieve(
            query, top_k=top_k, include_debug=include_debug, rerank=use_reranker
        )
        stats = outcome.stats
        results = outcome.results

        pack = self._builder.build(
            query, results, candidate_count=stats.fused_candidates
        )
        stats.context_chunks = len(pack.sources)
        stats.context_tokens = pack.context_tokens

        if not pack.sources:
            if "index_unavailable" in stats.degraded:
                answer = "知识库索引尚未建立或不可用，因此无法基于你的笔记回答。"
            else:
                answer = NOT_ENOUGH_EVIDENCE
            return RagQueryResponse(
                query=query,
                answer=answer,
                sources=[],
                evidence=_evidence_summary(pack, stats),
                retrieval_stats=_stats_to_wire(stats),
                degraded=list(stats.degraded),
                generated_at=datetime.now(UTC),
            )

        generation_started = time.perf_counter()
        answer, model, invalid, generation_degraded = await self._generate_answer(
            query, pack
        )
        stats.generation_ms = round(
            (time.perf_counter() - generation_started) * 1000.0, 3
        )
        stats.degraded.extend(generation_degraded)

        report = None
        sources: list[RagSource] = []
        if answer is not None:
            answer, report, used, grounded = ensure_grounded_answer(
                answer, pack, require_citation=self.require_citation
            )
            if not grounded:
                stats.degraded.append("answer_not_grounded")
            scores = {
                item.chunk_id: item.score for item in results if item.chunk_id
            }
            sources = [
                _source_to_wire(source, scores.get(source.chunk_id))
                for source in used
            ]
        else:
            answer = _evidence_fallback(pack)
            # No generated prose, but the retrieved evidence is still real and
            # clickable, so the sources are returned together with the warning.
            sources = [
                _source_to_wire(source, None) for source in pack.sources
            ]

        return RagQueryResponse(
            query=query,
            answer=answer,
            sources=sources,
            evidence=_evidence_summary(pack, stats),
            retrieval_stats=_stats_to_wire(stats),
            model=model or "",
            prompt_version=self.prompt_version,
            degraded=sorted(set(stats.degraded)),
            invalid_citations=list(report.invalid_ids) if report else [],
            generated_at=datetime.now(UTC),
        )

    # ------------------------------------------------------------------

    def _retrieve(
        self,
        query: str,
        *,
        top_k: int | None,
        include_debug: bool,
        rerank: bool,
    ):
        from .retrieval.hybrid import HybridSearchOutcome

        try:
            return self._retriever.search(
                query,
                top_k=top_k,
                include_debug=include_debug,
                rerank=rerank,
            )
        except RetrieverUnavailable as exc:  # pragma: no cover - defensive
            logger.info("retrieval unavailable: %s", exc)
            return HybridSearchOutcome(
                results=[], stats=RetrievalStats(degraded=["retrieval_unavailable"])
            )
        except RagStoreUnavailable as exc:  # pragma: no cover - defensive
            logger.info("rag store unavailable: %s", exc)
            return HybridSearchOutcome(
                results=[], stats=RetrievalStats(degraded=["index_unavailable"])
            )
        except EmbeddingError as exc:  # pragma: no cover - defensive
            logger.info("embedding failed: %s", exc.code)
            return HybridSearchOutcome(
                results=[], stats=RetrievalStats(degraded=["vector_unavailable"])
            )

    async def _generate_answer(
        self, query: str, pack: EvidencePack
    ) -> tuple[str | None, str, list[str], list[str]]:
        """Call the chat model; returns ``(answer, model, invalid_ids, degraded)``."""
        if self._generate is None:
            return None, "", [], ["generation_unavailable"]
        system = _load_answer_prompt()
        user = (
            f"Question: {query}\n\n"
            f"Evidence:\n{render_evidence_pack(pack)}\n\n"
            "Answer using only the evidence above."
        )
        try:
            raw, model = await self._generate(system, user)
        except (AIAdapterError, AIError) as exc:
            logger.info("rag generation failed: %s", getattr(exc, "kind", None) or exc)
            return None, "", [], ["generation_failed"]
        except Exception:  # pragma: no cover - provider bug surfaced as degradation
            logger.exception("rag generation raised")
            return None, "", [], ["generation_failed"]
        payload = _parse_payload(raw)
        if payload is None:
            return raw.strip() or None, model, [], ["generation_unstructured"]
        combined = payload.answer
        # ``used_sources`` is a convenience list: only add markers the prose does
        # not already carry, so a citation never appears twice.
        existing = set(extract_citations(combined))
        for token in payload.used_sources:
            digits = "".join(char for char in str(token) if char.isdigit())
            if not digits:
                continue
            marker = f"S{int(digits)}"
            if marker not in existing:
                combined = f"{combined} [{marker}]"
                existing.add(marker)
        return combined, model, [], []


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _validate_or_raise(query: str) -> None:
    """Map query-shape problems to the project-wide 400 ``invalid_request``."""
    try:
        validate_query(query)
    except ValueError as exc:
        raise RagInvalidRequest(str(exc)) from exc


def _parse_payload(raw: str) -> RagAnswerPayload | None:
    """Parse the model's JSON answer, tolerating a fenced code block."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text
        text = text.split("\n", 1)[1] if "\n" in text else text
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    try:
        return RagAnswerPayload.model_validate(parsed)
    except Exception:
        answer = parsed.get("answer")
        if isinstance(answer, str) and answer.strip():
            return RagAnswerPayload(answer=answer.strip(), used_sources=[])
        return None


def _excerpt(content: str, *, limit: int = 700) -> str:
    text = " ".join((content or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _stats_to_wire(stats: RetrievalStats) -> RagRetrievalStats:
    return RagRetrievalStats(
        fts_candidates=int(stats.fts_candidates),
        vector_candidates=int(stats.vector_candidates),
        link_candidates=int(stats.link_candidates),
        fused_candidates=int(stats.fused_candidates),
        reranked=bool(stats.reranked),
        context_chunks=int(stats.context_chunks),
        context_tokens=int(stats.context_tokens),
        retrieval_ms=float(stats.retrieval_ms),
        embedding_ms=float(stats.embedding_ms),
        rerank_ms=float(stats.rerank_ms),
        generation_ms=float(stats.generation_ms),
        degraded=list(stats.degraded),
        retrieval_debug=stats.debug,
    )


def _evidence_summary(pack: EvidencePack, stats: RetrievalStats) -> dict[str, object]:
    """Return UI summary metadata derived solely from the evidence pack."""
    return {
        "source_count": len(pack.sources),
        "paths": pack.paths,
        "context_tokens": int(pack.context_tokens),
        "candidate_count": int(pack.candidate_count),
        "truncated": bool(pack.truncated),
        "grounded": bool(pack.sources),
        "degraded": list(stats.degraded),
    }


def _source_to_wire(source, score: float | None) -> RagSource:
    return RagSource(
        id=source.source_id,
        path=source.path,
        heading=source.heading,
        heading_path=source.heading_path,
        start_line=max(1, int(source.start_line)),
        end_line=max(1, int(source.end_line)),
        excerpt=_excerpt(source.content, limit=400),
        score=score,
    )


def _evidence_fallback(pack: EvidencePack) -> str:
    """Human-readable degradation used when the chat model is unavailable."""
    lines = [
        "无法调用生成模型，下面是知识库中检索到的相关片段：",
    ]
    for source in pack.sources:
        heading = source.heading_path or source.heading or ""
        label = f"{source.path}" + (f" › {heading}" if heading else "")
        lines.append(f"- [{source.source_id}] {label}（{source.start_line}-{source.end_line} 行）")
    return "\n".join(lines)


def load_answer_prompt() -> str:
    """The grounded system prompt (loadable/exposed for tests and diagnostics)."""
    return _load_answer_prompt()


__all__ = ["RagService", "load_answer_prompt"]
