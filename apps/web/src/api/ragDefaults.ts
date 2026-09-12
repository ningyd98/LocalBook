import type { RagConfiguration } from "./settings";

/**
 * Defaults mirroring ``server.config.RagSettings``.
 *
 * The settings panel, this RAG section and the tests share one shape: the
 * section renders before the first status/round-trip completes and against a
 * server older than M14 (whose snapshot has no ``rag`` key).
 *
 * This module is the single frontend source of truth for RAG defaults. They are
 * a contract with the server, so they live in the API layer rather than in a
 * React component: importing them from a child component re-created a
 * parent↔child value cycle that happens to survive today but breaks under Fast
 * Refresh and whenever the defaults move again.
 */
const RAG_DEFAULTS: RagConfiguration = Object.freeze({
  enabled: true,
  embedding_provider: "hash",
  embedding_base_url: "",
  embedding_model: "local-hash",
  embedding_api_key_set: false,
  embedding_dimension: 256,
  embedding_version: "v1",
  vector_min_score: 0,
  chunk_target_tokens: 800,
  chunk_max_tokens: 1200,
  chunk_overlap_tokens: 100,
  fts_top_k: 30,
  vector_top_k: 30,
  fusion_top_k: 20,
  rerank_top_k: 10,
  context_top_k: 6,
  rrf_k: 60,
  context_max_tokens: 4000,
  context_max_tokens_per_document: 1800,
  context_merge_adjacent: true,
  context_merge_gap_lines: 5,
  reranker_enabled: false,
  reranker_provider: "lexical",
  reranker_base_url: "",
  reranker_model: "",
  reranker_api_key_set: false,
  reranker_timeout_seconds: 30,
  reranker_min_score: 0,
  index_on_startup: false,
  debounce_seconds: 1.5,
  require_citation: true,
  include_retrieval_debug: false,
  // Roadmap item ③: neutral by default, exactly like ``RagSettings``. The link
  // path is off until a deployment opts in, so an unconfigured install behaves
  // as it did before the feature existed.
  link_retrieval_enabled: false,
  link_top_k: 20,
  wikilink_weight: 1,
  backlink_weight: 0.8,
  tag_weight: 0.6,
  graph_weight: 0.4,
});

/**
 * The frozen default configuration.
 *
 * Returning the *same* object every call keeps its identity stable: the RAG
 * section re-syncs its draft when the stored configuration changes, and a fresh
 * object per call would make every unrelated parent re-render look like a
 * stored change and silently discard unsaved edits. ``Object.freeze`` makes an
 * accidental in-place mutation throw instead of corrupting the shared default.
 */
export function fallbackRagLike(): RagConfiguration {
  return RAG_DEFAULTS;
}
