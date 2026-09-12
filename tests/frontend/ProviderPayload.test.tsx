/**
 * Provider form → `POST /settings/ai/profiles` payload.
 *
 * Regression: a cleared numeric box produced `0` (because `Number("") === 0`
 * passes `Number.isFinite`), the server rejected it (`temperature >= 0.1`,
 * `max_output_tokens >= 64`, `request_timeout_seconds > 0`) with a 422, and the
 * UI could only report it as a vague "invalid request" — which is exactly how
 * "无法新增供应商" presented itself.
 *
 * Rules now locked in:
 *  - a blank box means "keep the stored value" and is omitted from the payload;
 *  - a non-empty but out-of-range box names the field, blocks the save and never
 *    reaches the server;
 *  - valid boxes keep flowing through unchanged.
 */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "./render";
import { SettingsPanel } from "../../apps/web/src/components/SettingsPanel";
import { preferenceDefaults, useWorkspaceStore } from "../../packages/workspace/src";
import type { AIProviderProfile, ServiceSettings } from "../../apps/web/src/api/settings";

const profile = (): AIProviderProfile => ({ id: "default", name: "127.0.0.1:8234", kind: "omlx", base_url: "http://127.0.0.1:8234/v1", chat_model: "auto", temperature: 0.1, max_output_tokens: 1200, request_timeout_seconds: 60, connect_timeout_seconds: 0.5, max_models_response_bytes: 1000000, api_key_set: true, builtin: true, source: "default", from_env: false, is_active: true });
const settings = (): ServiceSettings => ({ revision: 5, vault_session_id: "a", changing: false, version: "1.0.0", vault: { root: "/notes", status: "ready" }, ai: { enabled: true, base_url: "http://127.0.0.1:8234/v1", api_key_set: true, chat_model: "auto", active_profile_id: "default", profiles: [profile()] } });
const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });

/** POSTs that would write a profile — the probe endpoint shares the prefix. */
const saves = (fetchMock: ReturnType<typeof vi.fn>) =>
  fetchMock.mock.calls.filter(([url, init]) => new URL(String(url), "http://local").pathname === "/api/v1/settings/ai/profiles" && (init as RequestInit | undefined)?.method === "POST");
const savedProvider = (fetchMock: ReturnType<typeof vi.fn>) => JSON.parse(String(saves(fetchMock)[0]![1]?.body)).provider;

beforeEach(() => {
  localStorage.clear();
  useWorkspaceStore.getState().resetVault();
  useWorkspaceStore.setState({ ...preferenceDefaults });
});

function mountProviderForm() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/settings/ai/profiles")) return init?.method === "POST" ? json(settings()) : json({ revision: 5, active_profile_id: "default", profiles: [profile()] });
    if (url.endsWith("/settings")) return json(settings());
    if (url.endsWith("/ai/models")) return json({ status: "connected", models: [], selected_model: null, error_code: null, message: null });
    return json({});
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<SettingsPanel settings={settings()} onClose={vi.fn()} onSaved={vi.fn()} onSwitch={vi.fn()} initialSection="ai" />);
  return fetchMock;
}

/** The provider form itself; the legacy flat AI form has its own controls. */
const form = () => document.querySelector("form.provider-form") as HTMLElement | null;

/** Open the "new provider" form from a preset and return its three number boxes. */
async function openForm() {
  await screen.findAllByText("127.0.0.1:8234");
  await userEvent.click(screen.getByRole("button", { name: "DeepSeek" }));
  const scope = form();
  if (!scope) throw new Error("provider form did not open");
  return within(scope).getAllByRole("spinbutton");
}

describe("provider save payload", () => {
  it("sends a valid profile unchanged", async () => {
    const fetchMock = mountProviderForm();
    const spin = await openForm();
    expect(spin.map(field => (field as HTMLInputElement).value)).toEqual(["0.1", "1200", "60"]);

    await userEvent.click(within(form()!).getByRole("button", { name: "Save & switch" }));

    await waitFor(() => expect(saves(fetchMock)).toHaveLength(1));
    expect(savedProvider(fetchMock)).toEqual({
      id: "deepseek", name: "DeepSeek", kind: "openai",
      base_url: "https://api.deepseek.com/v1", chat_model: "deepseek-chat",
      temperature: 0.1, max_output_tokens: 1200, request_timeout_seconds: 60,
    });
  });

  it("omits every blank number instead of sending zero", async () => {
    const fetchMock = mountProviderForm();
    for (const field of await openForm()) await userEvent.clear(field);

    expect(within(form()!).queryByTestId("provider-number-error")).not.toBeInTheDocument();
    await userEvent.click(within(form()!).getByRole("button", { name: "Save & switch" }));

    await waitFor(() => expect(saves(fetchMock).length).toBeGreaterThan(0));
    const provider = savedProvider(fetchMock);
    expect(provider).not.toHaveProperty("temperature");
    expect(provider).not.toHaveProperty("max_output_tokens");
    expect(provider).not.toHaveProperty("request_timeout_seconds");
  });

  it("omits a single blank box and keeps the others", async () => {
    const fetchMock = mountProviderForm();
    const spin = await openForm();
    await userEvent.clear(spin[0]!);

    await userEvent.click(screen.getByRole("button", { name: "Save & switch" }));

    await waitFor(() => expect(saves(fetchMock).length).toBeGreaterThan(0));
    const provider = savedProvider(fetchMock);
    expect(provider).not.toHaveProperty("temperature");
    expect(provider.max_output_tokens).toBe(1200);
    expect(provider.request_timeout_seconds).toBe(60);
  });

  it("names an out-of-range field and blocks the save", async () => {
    const fetchMock = mountProviderForm();
    const spin = await openForm();
    await userEvent.clear(spin[1]!);
    await userEvent.type(spin[1]!, "10"); // below the 64 minimum

    const error = await within(form()!).findByTestId("provider-number-error");
    expect(error).toHaveTextContent(/max output tokens \(64–8192\)/);
    expect(within(form()!).getByRole("button", { name: "Save only" })).toBeDisabled();
    expect(within(form()!).getByRole("button", { name: "Save & switch" })).toBeDisabled();
    expect(saves(fetchMock)).toHaveLength(0);
  });

  it("blocks an out-of-range timeout and temperature too", async () => {
    const fetchMock = mountProviderForm();
    const spin = await openForm();
    await userEvent.clear(spin[2]!);
    await userEvent.type(spin[2]!, "500");
    await userEvent.clear(spin[0]!);
    await userEvent.type(spin[0]!, "5");

    const error = await within(form()!).findByTestId("provider-number-error");
    expect(error).toHaveTextContent(/temperature \(0.1–1\)/);
    expect(error).toHaveTextContent(/request timeout \(s\) \(1–120\)/);
    expect(saves(fetchMock)).toHaveLength(0);
  });

  it("recovers as soon as the value is valid again", async () => {
    const fetchMock = mountProviderForm();
    const spin = await openForm();
    await userEvent.clear(spin[1]!);
    await userEvent.type(spin[1]!, "10");
    expect(await within(form()!).findByTestId("provider-number-error")).toBeInTheDocument();

    await userEvent.clear(spin[1]!);
    await userEvent.type(spin[1]!, "2048");

    await waitFor(() => expect(within(form()!).queryByTestId("provider-number-error")).not.toBeInTheDocument());
    await userEvent.click(within(form()!).getByRole("button", { name: "Save & switch" }));

    // What matters is the payload that reaches the server, not the click count.
    await waitFor(() => expect(saves(fetchMock).length).toBeGreaterThan(0));
    const payloads = saves(fetchMock).map(([, init]) => JSON.parse(String((init as RequestInit).body)).provider);
    expect(payloads.every(provider => provider.max_output_tokens === 2048)).toBe(true);
  });
});
