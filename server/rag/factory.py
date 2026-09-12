"""RAG stack construction from settings (M14 §二/§十二).

One factory assembles the whole chain — vector store → embedding provider →
index service → retrievers → context builder → RAG service — so the wiring
exists in exactly one place and can be used by the Vault lifecycle, the API
dependencies and tests alike.

Degradation is decided here, not sprinkled through the code:

- no embedding endpoint configured (or ``embedding_provider = "none"``) ⇒ the
  index runs with the documented local fallback embedder (``is_degraded``) or
  with lexical retrieval only;
- an embedding failure never prevents the stack from being created: the services
  degrade at request time and report it through ``/rag/index/status``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..index.db import IndexDatabase
from ..index.service import DerivedIndexService
from ..vault.service import VaultService
from .chunking.markdown import MarkdownChunker
from .context.builder import ContextBudget, RagContextBuilder
from .embeddings.base import (
    EmbeddingProvider,
    HashEmbeddingProvider,
)
from .embeddings.openai_compatible import OpenAICompatibleEmbeddingProvider
from .embeddings.runner import EmbeddingRunner
from .index_service import EMBEDDING_VERSION, RagIndexService
from .rerank.base import LexicalOverlapReranker, RerankProvider
from .rerank.openai_compatible import OpenAICompatibleReranker
from .retrieval.hybrid import DEFAULT_LINK_RRF_WEIGHT, HybridRetriever
from .retrieval.keyword import KeywordRetriever
from .retrieval.link import LinkRetriever
from .retrieval.vector import VectorRetriever
from .service import GenerateCallable, RagService
from .vector.sqlite import SqliteVectorStore

logger = logging.getLogger("localnote.rag.factory")


@dataclass(slots=True)
class RagStack:
    """Everything the API/routes need, built once per Vault."""

    store: SqliteVectorStore
    index: RagIndexService
    retriever: HybridRetriever
    service: RagService

    def close(self) -> None:
        self.index.close()


def build_embedding_provider(settings) -> EmbeddingProvider | None:
    """Resolve the configured embedding provider (never the chat model)."""
    provider = str(getattr(settings, "embedding_provider", "hash")).strip().lower()
    base_url = str(getattr(settings, "embedding_base_url", "") or "").strip()
    model = str(getattr(settings, "embedding_model", "") or "").strip()
    api_key = str(getattr(settings, "embedding_api_key", "") or "").strip() or None
    dimension = int(getattr(settings, "embedding_dimension", 0) or 0)
    timeout = float(getattr(settings, "embedding_timeout_seconds", 30.0))
    if provider == "none":
        return None
    if provider in {"openai_compatible", "openai"}:
        if not base_url or not model:
            logger.warning(
                "rag embedding provider %r is missing base_url/model; "
                "falling back to the local embedder",
                provider,
            )
            return HashEmbeddingProvider(
                dimension=dimension or 256, model="local-hash"
            )
        return OpenAICompatibleEmbeddingProvider(
            base_url=base_url,
            model=model,
            api_key=api_key,
            timeout_seconds=timeout,
            batch_size=int(getattr(settings, "embedding_batch_size", 32)),
            dimension=dimension if dimension and dimension > 0 else 0,
            trust_env=bool(getattr(settings, "use_env_proxy", False)),
        )
    return HashEmbeddingProvider(dimension=dimension or 256, model="local-hash")


def build_reranker(settings) -> RerankProvider | None:
    """Optional reranker; disabled by default (no extra dependency in M14).

    ``lexical`` is a local heuristic; ``openai_compatible`` posts to
    ``{reranker_base_url}/rerank``. A missing endpoint/model or ``none`` simply
    means "no reranker": the fused ranking is used, and RAG still works.
    """
    if not bool(getattr(settings, "reranker_enabled", False)):
        return None
    provider = str(getattr(settings, "reranker_provider", "lexical")).strip().lower()
    if provider in {"lexical", "lexical_overlap", "local"}:
        return LexicalOverlapReranker()
    if provider in {"openai_compatible", "openai"}:
        base_url = str(getattr(settings, "reranker_base_url", "") or "").strip()
        model = str(getattr(settings, "reranker_model", "") or "").strip()
        if not base_url or not model:
            logger.warning(
                "rag reranker %r is missing base_url/model; rerank disabled",
                provider,
            )
            return None
        return OpenAICompatibleReranker(
            base_url=base_url,
            model=model,
            api_key=str(getattr(settings, "reranker_api_key", "") or "").strip() or None,
            timeout_seconds=float(getattr(settings, "reranker_timeout_seconds", 30.0)),
            trust_env=bool(getattr(settings, "use_env_proxy", False)),
        )
    logger.warning("rag reranker provider %r is not implemented; rerank disabled", provider)
    return None


def build_link_retriever(
    store: SqliteVectorStore,
    index: DerivedIndexService | None,
    settings,
) -> LinkRetriever | None:
    """Optional link/graph retriever (roadmap item ③); off unless enabled.

    Wiring rules, all of them deliberate:

    - disabled (the default) ⇒ ``None``, so the hybrid fuses exactly the lists it
      fused before the link path existed;
    - enabled with a derived index ⇒ wired and reporting ``enabled``;
    - enabled *without* a derived index ⇒ still wired: the retriever degrades
      internally (``link_unavailable``) and the status card shows the real
      misconfiguration instead of hiding it behind "nothing is configured";
    - any construction failure ⇒ ``None`` plus a logged reason. A broken link
      path must never stop the RAG stack from starting.

    The derived index is *passed in* and never opened here: RAG is a read-only
    consumer of derived data, so it must not create a second connection that
    initialises schema in ``.localnote/index.db``.
    """
    if not bool(getattr(settings, "link_retrieval_enabled", False)):
        return None
    if index is None:
        logger.warning(
            "rag link retrieval is enabled but the derived index is unavailable; "
            "the link path stays wired and reports link_unavailable"
        )
    try:
        return LinkRetriever(
            store,
            index=index,
            top_k=int(getattr(settings, "link_top_k", 20)),
            wikilink_weight=float(getattr(settings, "wikilink_weight", 1.0)),
            backlink_weight=float(getattr(settings, "backlink_weight", 0.8)),
            tag_weight=float(getattr(settings, "tag_weight", 0.6)),
            graph_weight=float(getattr(settings, "graph_weight", 0.4)),
        )
    except Exception:
        logger.exception("rag link retriever construction failed; link path disabled")
        return None


def create_rag_stack(
    vault: VaultService,
    database: IndexDatabase,
    settings,
    *,
    index: DerivedIndexService | None = None,
    generate: GenerateCallable | None = None,
) -> RagStack:
    """Assemble the full RAG stack for one Vault + derived database."""
    chunker = MarkdownChunker(
        target_tokens=int(getattr(settings, "chunk_target_tokens", 800)),
        max_tokens=int(getattr(settings, "chunk_max_tokens", 1200)),
        overlap_tokens=int(getattr(settings, "chunk_overlap_tokens", 100)),
    )
    store = SqliteVectorStore(database)
    store.ensure_ready()

    provider = build_embedding_provider(settings)
    runner = EmbeddingRunner(provider) if provider is not None else None
    model = provider.model if provider is not None else ""
    # Named ``rag_index`` (not ``index``) so it cannot shadow the ``index``
    # parameter, which carries the *derived* M4 index the link path reads.
    rag_index = RagIndexService(
        vault,
        store,
        chunker=chunker,
        embedding_provider=provider,
        embedding_runner=runner,
        embed_batch_size=int(getattr(settings, "embedding_batch_size", 32)),
        embedding_version=str(getattr(settings, "embedding_version", EMBEDDING_VERSION)),
        enabled=bool(getattr(settings, "enabled", True)),
        debounce_seconds=float(getattr(settings, "debounce_seconds", 1.5)),
    )

    vector_retriever = (
        VectorRetriever(
            store,
            provider=provider,
            runner=runner,
            min_score=float(getattr(settings, "vector_min_score", 0.0)),
        )
        if provider is not None
        else None
    )
    retriever = HybridRetriever(
        keyword=KeywordRetriever(store),
        vector=vector_retriever,
        link=build_link_retriever(store, index, settings),
        reranker=build_reranker(settings),
        fts_top_k=int(getattr(settings, "fts_top_k", 30)),
        vector_top_k=int(getattr(settings, "vector_top_k", 30)),
        link_top_k=int(getattr(settings, "link_top_k", 20)),
        # The RRF weight that scales the whole link list is deliberately NOT a
        # setting yet: the imported default is the production value t2 measures
        # against, and it stays visible in exactly this one place. Promoting it
        # to a real field needs a measurement showing the fused result is over-
        # or under-weighted (see docs/rag-architecture.md §5).
        link_rrf_weight=DEFAULT_LINK_RRF_WEIGHT,
        fusion_top_k=int(getattr(settings, "fusion_top_k", 20)),
        rerank_top_k=int(getattr(settings, "rerank_top_k", 10)),
        rrf_k=int(getattr(settings, "rrf_k", 60)),
    )
    builder = RagContextBuilder(
        ContextBudget.from_settings(
            _ContextConfig(
                context_top_k=int(getattr(settings, "context_top_k", 6)),
                context_max_tokens=int(getattr(settings, "context_max_tokens", 4000)),
                context_max_tokens_per_document=int(
                    getattr(settings, "context_max_tokens_per_document", 1800)
                ),
                context_merge_adjacent=bool(
                    getattr(settings, "context_merge_adjacent", True)
                ),
                context_merge_gap_lines=int(
                    getattr(settings, "context_merge_gap_lines", 5)
                ),
            )
        )
    )
    service = RagService(
        index=rag_index,
        retriever=retriever,
        context_builder=builder,
        generate=generate,
        context_top_k=int(getattr(settings, "context_top_k", 6)),
        require_citation=bool(getattr(settings, "require_citation", True)),
    )
    logger.info(
        "rag stack ready store=%s provider=%s model=%s degraded=%s rerank=%s link=%s",
        store.name,
        type(provider).__name__ if provider is not None else "none",
        model,
        rag_index.embedding_is_degraded,
        retriever.reranker_enabled,
        # Defensive: the retriever owns this marker, and a tree where it is not
        # exposed yet must still build the stack (nothing is logged as enabled).
        str(getattr(retriever, "link_retrieval", "") or "") or "off",
    )
    return RagStack(store=store, index=rag_index, retriever=retriever, service=service)


class _ContextConfig:
    """Small adapter so ``ContextBudget.from_settings`` reads plain attributes."""

    def __init__(self, **values) -> None:
        for key, value in values.items():
            setattr(self, key, value)


__all__ = [
    "RagStack",
    "build_embedding_provider",
    "build_link_retriever",
    "build_reranker",
    "create_rag_stack",
]
