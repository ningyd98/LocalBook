import type { DiscoveredModel } from "@localnote/protocol";
import { request } from "./client";

/** ``api_key`` is write-only: the API never returns it, only ``api_key_set``. */
export interface AIConfiguration { enabled: boolean; base_url: string | null; api_key?: string | null; api_key_set?: boolean; chat_model: string; active_profile_id?: string; profiles?: AIProviderProfile[]; }
/** One saved provider route (PLAN-PROVIDERS): endpoint + credential + defaults. */
export interface AIProviderProfile {
  id: string; name: string;
  kind: "omlx" | "openai" | "openai-compatible" | "custom";
  base_url: string | null; chat_model: string;
  temperature: number; max_output_tokens: number;
  request_timeout_seconds: number; connect_timeout_seconds: number; max_models_response_bytes: number;
  api_key_set: boolean; builtin: boolean; source: "settings" | "env" | "default"; from_env: boolean; is_active: boolean;
}
/** M14 RAG settings as returned by ``GET /settings`` (secret never echoed). */
export interface RagConfiguration {
  enabled: boolean;
  embedding_provider: "hash" | "openai_compatible" | "openai" | "none";
  embedding_base_url: string;
  embedding_model: string;
  embedding_api_key_set: boolean;
  embedding_dimension: number;
  embedding_version: string;
  vector_min_score: number;
  chunk_target_tokens: number;
  chunk_max_tokens: number;
  chunk_overlap_tokens: number;
  fts_top_k: number; vector_top_k: number; fusion_top_k: number; rerank_top_k: number; context_top_k: number;
  rrf_k: number;
  context_max_tokens: number; context_max_tokens_per_document: number;
  context_merge_adjacent: boolean; context_merge_gap_lines: number;
  reranker_enabled: boolean; reranker_provider: string; reranker_base_url: string; reranker_model: string;
  reranker_api_key_set: boolean; reranker_timeout_seconds: number; reranker_min_score: number;
  index_on_startup: boolean; debounce_seconds: number;
  require_citation: boolean; include_retrieval_debug: boolean;
  /** Roadmap item ③ (M14): optional link/graph expansion, neutral by default. */
  link_retrieval_enabled: boolean; link_top_k: number;
  wikilink_weight: number; backlink_weight: number; tag_weight: number; graph_weight: number;
}
/**
 * Server ``RagSettings`` fields this snapshot type still omits because they have
 * no UI input yet — they are configured through ``LOCALNOTE_RAG__*`` env vars:
 * ``embedding_batch_size``, ``embedding_timeout_seconds``, ``use_env_proxy``.
 * Two further knobs are constructor-only and deliberately not settings at all:
 * ``HybridRetriever.link_rrf_weight`` (0.5) and ``link_add_only`` (true), both
 * passed explicitly by ``server/rag/factory.py``.
 *
 * This is a documented boundary, not an invitation: status-only keys such as
 * ``link_retrieval`` (a field of ``/rag/index/status``, never of ``GET
 * /settings``) must never be added to the settings type or to a PATCH body.
 */
/**
 * Every key ``PATCH /settings/rag`` accepts, mirroring the server model
 * (``server/api/routes/settings.py::RagConfiguration``) and nothing else.
 *
 * A contract test keeps this list equal to the server's declared set: a stale
 * whitelist silently drops user input, while an undeclared key makes the
 * server's ``extra="forbid"`` reject the whole save with 422.
 */
export const RAG_PATCH_KEYS = [
  "enabled", "embedding_provider", "embedding_base_url", "embedding_model",
  "embedding_api_key", "embedding_dimension",
  "chunk_target_tokens", "chunk_max_tokens", "chunk_overlap_tokens",
  "fts_top_k", "vector_top_k", "context_top_k",
  "reranker_enabled", "reranker_provider", "reranker_base_url", "reranker_model",
  "index_on_startup",
  "link_retrieval_enabled", "link_top_k",
  "wikilink_weight", "backlink_weight", "tag_weight", "graph_weight",
] as const satisfies readonly (keyof RagConfiguration | "embedding_api_key")[];
export type RagPatchKey = (typeof RAG_PATCH_KEYS)[number];
/**
 * Build a settings PATCH body from the server-declared key set only.
 *
 * The snapshot the UI renders is wider than the PATCH model (response-side flags
 * such as ``embedding_api_key_set``, plus env-only knobs), so spreading a whole
 * draft sends undeclared keys and the server's ``extra="forbid"`` rejects the
 * request. Status-only keys — ``link_retrieval`` above all — can never reach the
 * wire through this helper.
 */
export function ragPatchPayload(
  rag: RagConfiguration,
  extra: RagConfigurationPatch = {},
): RagConfigurationPatch {
  const source: Record<string, unknown> = { ...rag, ...extra };
  const payload: Record<string, unknown> = {};
  for (const key of RAG_PATCH_KEYS) {
    if (source[key] !== undefined) payload[key] = source[key];
  }
  return payload as RagConfigurationPatch;
}
/** Partial edit; ``null``/omitted keeps the stored value, ``""`` clears the key. */
export type RagConfigurationPatch = Partial<Omit<RagConfiguration, "embedding_api_key_set">> & { embedding_api_key?: string | null };
export interface ServiceSettings {
  revision: number; vault_session_id: string; changing: boolean; version: string;
  vault: { root: string | null; status: "ready" | "not_configured" | "unavailable" };
  ai: AIConfiguration;
  /** M14. Optional so a server older than M14 still deserializes cleanly. */
  rag?: RagConfiguration;
}
export interface AIConnectionTest {http_status?: number; status: "connected" | "offline" | "not_configured"; models: DiscoveredModel[]; selected_model: string | null; message: string | null; error_code?: string; /** true when the request carried an API key (stored or submitted) */ key_sent?: boolean; }
/** ``/settings/ai/models`` result: the dropdown source. */
export interface AIModelList extends AIConnectionTest {}
export interface AIProfileList { revision: number; active_profile_id: string; profiles: AIProviderProfile[]; }
/** Payload for creating/editing a profile; the server never accepts its own bookkeeping fields. */
export interface AIProviderPayload {
  id: string; name?: string; kind: AIProviderProfile["kind"];
  base_url: string | null; chat_model?: string;
  /** Omitted = keep the stored secret; "" = clear it. */
  api_key?: string | null;
  temperature?: number; max_output_tokens?: number;
  request_timeout_seconds?: number; connect_timeout_seconds?: number; max_models_response_bytes?: number;
}
const json = (body: unknown, method = "POST"): RequestInit => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
export const fetchSettings = () => request<ServiceSettings>("/settings");
export const updateAISettings = (ai: AIConfiguration, expected_revision: number) => request<ServiceSettings>("/settings", json({ ai, expected_revision }, "PATCH"));
export const testAIConnection = (ai: AIConfiguration) => request<AIConnectionTest>("/settings/ai/test", json(ai));
/** Fetch the provider's model list (uses the stored key when ``api_key`` is omitted). */
export const fetchAIModels = (ai: AIConfiguration) => request<AIModelList>("/settings/ai/models", json(ai));
export interface RerankerProbeResult { status: "connected" | "offline"; model: string; message: string; results: Array<{ index: number; score: number }>; }
/** Probe a rerank endpoint without saving anything (optional feature). */
export const testReranker = (args: { provider?: "openai_compatible"; base_url: string; model: string; api_key?: string | null }) => request<RerankerProbeResult>("/settings/rag/reranker/test", json(args));
/** Persist RAG settings; the server rebuilds only the derived RAG layer. */
export const updateRagSettings = (rag: RagConfigurationPatch, expected_revision: number) => request<ServiceSettings>("/settings/rag", json({ rag, expected_revision }, "PATCH"));
export const switchVault = (root: string, expected_revision: number, vault_session_id: string) => request<ServiceSettings>("/settings/vault/switch", json({ root, expected_revision, vault_session_id }));

/** The saved provider library plus which entry is currently applied. */
export const fetchAIProfiles = () => request<AIProfileList>("/settings/ai/profiles");
/** Create or replace one provider profile; ``activate`` applies it in the same transaction. */
export const saveAIProfile = (provider: AIProviderPayload, expected_revision: number, activate = true) => request<ServiceSettings>("/settings/ai/profiles", json({ provider, expected_revision, activate }));
/** Switch the applied provider route without editing it. */
export const activateAIProfile = (profile_id: string, expected_revision: number) => request<ServiceSettings>("/settings/ai/profiles/activate", json({ profile_id, expected_revision }));
export const deleteAIProfile = (profile_id: string, expected_revision: number) => request<ServiceSettings>("/settings/ai/profiles/delete", json({ profile_id, expected_revision }));
/** Probe one profile's endpoint without writing anything (reuses the stored key when omitted). */
export const testAIProfile = (provider: AIProviderPayload) => request<AIConnectionTest>("/settings/ai/profiles/test", json({ provider }));
