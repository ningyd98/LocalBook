import type { DiscoveredModel } from "@localnote/protocol";
import { request } from "./client";

/** ``api_key`` is write-only: the API never returns it, only ``api_key_set``. */
export interface AIConfiguration { enabled: boolean; base_url: string | null; api_key?: string | null; api_key_set?: boolean; chat_model: string; }
export interface ServiceSettings {
  revision: number; vault_session_id: string; changing: boolean; version: string;
  vault: { root: string | null; status: "ready" | "not_configured" | "unavailable" };
  ai: AIConfiguration;
}
export interface AIConnectionTest {http_status?: number; status: "connected" | "offline" | "not_configured"; models: DiscoveredModel[]; selected_model: string | null; message: string | null; error_code?: string; /** true when the request carried an API key (stored or submitted) */ key_sent?: boolean; }
/** ``/settings/ai/models`` result: the dropdown source. */
export interface AIModelList extends AIConnectionTest {}
const json = (body: unknown, method = "POST"): RequestInit => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
export const fetchSettings = () => request<ServiceSettings>("/settings");
export const updateAISettings = (ai: AIConfiguration, expected_revision: number) => request<ServiceSettings>("/settings", json({ ai, expected_revision }, "PATCH"));
export const testAIConnection = (ai: AIConfiguration) => request<AIConnectionTest>("/settings/ai/test", json(ai));
/** Fetch the provider's model list (uses the stored key when ``api_key`` is omitted). */
export const fetchAIModels = (ai: AIConfiguration) => request<AIModelList>("/settings/ai/models", json(ai));
export const switchVault = (root: string, expected_revision: number, vault_session_id: string) => request<ServiceSettings>("/settings/vault/switch", json({ root, expected_revision, vault_session_id }));
