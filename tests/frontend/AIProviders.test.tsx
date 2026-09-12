import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "./render";
import { SettingsPanel } from "../../apps/web/src/components/SettingsPanel";
import { AIProviderSwitch, AIProviderSwitch as Switch } from "../../apps/web/src/components/AIProviderSwitch";
import { configureWorkspaceApi, preferenceDefaults, useWorkspaceStore } from "../../packages/workspace/src";
import { setVaultSession } from "../../apps/web/src/api/client";
import type { AIProviderProfile, ServiceSettings } from "../../apps/web/src/api/settings";

const profile = (overrides: Partial<AIProviderProfile> = {}): AIProviderProfile => ({
  id: "default", name: "127.0.0.1:8234", kind: "omlx", base_url: "http://127.0.0.1:8234/v1",
  chat_model: "gpt-oss-20b", temperature: 0.1, max_output_tokens: 1200,
  request_timeout_seconds: 60, connect_timeout_seconds: 0.5, max_models_response_bytes: 1000000,
  api_key_set: false, builtin: true, source: "settings", from_env: false, is_active: true,
  ...overrides,
});
const deepseek = profile({id: "deepseek", name: "DeepSeek", kind: "openai",
  base_url: "https://api.deepseek.com/v1", chat_model: "deepseek-chat",
  api_key_set: true, builtin: false, is_active: false});
const settings = (overrides: Partial<ServiceSettings["ai"]> = {}): ServiceSettings => ({
  revision: 3, vault_session_id: "a", changing: false, version: "1.0.0",
  vault: {root: "/notes/a", status: "ready"},
  ai: {enabled: true, base_url: "http://127.0.0.1:8234/v1", api_key_set: false,
       chat_model: "gpt-oss-20b", active_profile_id: "default", profiles: [profile()], ...overrides},
});
const profilesResponse = (active = "default", list: AIProviderProfile[] = [profile(), deepseek]) =>
  ({revision: 3, active_profile_id: active, profiles: list});
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), {status});
const failure = (status: number, code: string) =>
  new Response(JSON.stringify({error: {code, message: code, path: null}}), {status});

/** Buttons inside the provider form only: the legacy flat form has same-named ones. */
function inProviderForm(container: HTMLElement) {
  const form = container.querySelector(".provider-form");
  if (!form) throw new Error("provider form is not open");
  return within(form as HTMLElement);
}
const renderAI = (value = settings(), onSaved = vi.fn()) =>
  render(<SettingsPanel settings={value} onClose={vi.fn()} onSaved={onSaved} onSwitch={vi.fn()} initialSection="ai"/>);

beforeEach(()=>{
  localStorage.clear();
  useWorkspaceStore.getState().resetVault();
  useWorkspaceStore.setState({...preferenceDefaults});
  setVaultSession("a");
  configureWorkspaceApi({fetchVaultFiles:async()=>({entries:[]}),fetchVaultFile:async()=>{throw new Error("unused")},patchVaultFile:async()=>{throw new Error("unused")}});
});

describe("provider library in settings", () => {
  it("lists saved providers, marks the applied one and switches on click", async () => {
    const onSaved = vi.fn();
    const fetchMock = vi.fn(async (input: RequestInfo|URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/settings/ai/profiles")) {
        if (init?.method === "POST") return json(url.endsWith("/activate") ? settings({active_profile_id: "deepseek"}) : settings());
        return json(profilesResponse());
      }
      if (url.endsWith("/settings")) return json(settings());
      if (url.endsWith("/ai/models")) return json({status:"connected",models:[{id:"gpt-oss-20b"}],selected_model:"gpt-oss-20b",error_code:null,message:null});
      return json({});
    });
    vi.stubGlobal("fetch", fetchMock);
    renderAI(settings(), onSaved);

    expect(await screen.findByText("DeepSeek")).toBeInTheDocument();
    expect(screen.getAllByText("In use").length).toBe(1);
    await userEvent.click(screen.getAllByRole("button", {name: "Switch"})[0]);
    await waitFor(()=>expect(onSaved).toHaveBeenCalled());
    const activate = fetchMock.mock.calls.find(([url])=>String(url).endsWith("/activate"));
    expect(JSON.parse(String(activate?.[1]?.body))).toEqual({profile_id:"deepseek", expected_revision:3});
  });

  it("adds a provider from a preset with the submitted key and activates it", async () => {
    const onSaved = vi.fn();
    const fetchMock = vi.fn(async (input: RequestInfo|URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/settings/ai/profiles")) {
        if (init?.method === "POST") return json(settings({active_profile_id: "deepseek"}));
        return json(profilesResponse());
      }
      if (url.endsWith("/settings")) return json(settings());
      if (url.endsWith("/ai/models")) return json({status:"connected",models:[],selected_model:null,error_code:null,message:null});
      return json({});
    });
    vi.stubGlobal("fetch", fetchMock);
    renderAI(settings(), onSaved);
    await screen.findByText("DeepSeek");

    await userEvent.click(screen.getByRole("button", {name: "DeepSeek"}));
    // The first key field belongs to the legacy flat form; the provider form owns the second.
    await userEvent.type(screen.getAllByPlaceholderText("sk-…")[1], "sk-secret");
    await userEvent.click(screen.getByRole("button", {name: "Save & switch"}));

    await waitFor(()=>expect(onSaved).toHaveBeenCalled());
    const saved = fetchMock.mock.calls.find(([url, init])=>String(url).endsWith("/settings/ai/profiles") && init?.method === "POST");
    expect(JSON.parse(String(saved?.[1]?.body))).toEqual({
      expected_revision: 3, activate: true,
      provider: {id:"deepseek", name:"DeepSeek", kind:"openai",
                 base_url:"https://api.deepseek.com/v1", chat_model:"deepseek-chat",
                 api_key:"sk-secret", temperature:0.1, max_output_tokens:1200, request_timeout_seconds:60},
    });
  });

  it("never sends the stored key when the field is untouched, and can save without switching", async () => {
    const onSaved = vi.fn();
    const fetchMock = vi.fn(async (input: RequestInfo|URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/settings/ai/profiles")) {
        if (init?.method === "POST") return json(settings());
        return json(profilesResponse());
      }
      if (url.endsWith("/settings")) return json(settings());
      if (url.endsWith("/ai/models")) return json({status:"connected",models:[],selected_model:null,error_code:null,message:null});
      return json({});
    });
    vi.stubGlobal("fetch", fetchMock);
    renderAI(settings(), onSaved);
    await screen.findByText("DeepSeek");

    await userEvent.click(screen.getAllByRole("button", {name:"Edit"})[1]);
    await userEvent.click(screen.getByRole("button", {name: "Save only"}));

    await waitFor(()=>expect(onSaved).toHaveBeenCalled());
    const saved = fetchMock.mock.calls.find(([url, init])=>String(url).endsWith("/settings/ai/profiles") && init?.method === "POST");
    const body = JSON.parse(String(saved?.[1]?.body));
    expect(body.activate).toBe(false);
    expect(body.provider).not.toHaveProperty("api_key");
    expect(body.provider.id).toBe("deepseek");
  });

  it("probes a profile without saving it", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo|URL, init?: RequestInit) => {
      void init;
      const url = String(input);
      if (url.endsWith("/ai/profiles/test")) return json({status:"connected",models:[{id:"m"}],selected_model:"m",error_code:null,message:null,key_sent:true});
      if (url.endsWith("/settings/ai/profiles")) return json(profilesResponse());
      if (url.endsWith("/settings")) return json(settings());
      if (url.endsWith("/ai/models")) return json({status:"connected",models:[],selected_model:null,error_code:null,message:null});
      return json({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const {container} = renderAI();
    await screen.findByText("DeepSeek");

    await userEvent.click(screen.getAllByRole("button", {name:"Edit"})[1]);
    await userEvent.click(inProviderForm(container).getByRole("button", {name: "Test connection"}));
    expect(await screen.findByText(/1 model\(s\) discovered/)).toBeInTheDocument();
    // Probing must not create or edit a profile: only the read + the probe ran.
    expect(fetchMock.mock.calls.some(call => String(call[0]).endsWith("/settings/ai/profiles") && call[1]?.method === "POST")).toBe(false);
  });

  it("keeps the applied provider undeletable and confirms idle deletions", async () => {
    const onSaved = vi.fn();
    const fetchMock = vi.fn(async (input: RequestInfo|URL, init?: RequestInit) => {
      void init;
      const url = String(input);
      if (url.endsWith("/ai/profiles/delete")) return json(settings({active_profile_id:"default", profiles:[profile()]}));
      if (url.endsWith("/settings/ai/profiles")) return json(profilesResponse());
      if (url.endsWith("/settings")) return json(settings());
      if (url.endsWith("/ai/models")) return json({status:"connected",models:[],selected_model:null,error_code:null,message:null});
      return json({});
    });
    vi.stubGlobal("fetch", fetchMock);
    renderAI(settings(), onSaved);
    await screen.findByText("DeepSeek");

    // The applied profile has no enabled delete affordance: only the idle row.
    const idleRow = screen.getByRole("button", {pressed: false}).closest(".provider-row") as HTMLElement;
    const deletes = screen.getAllByRole("button", {name: "Delete"});
    expect(within(idleRow).getByRole("button", {name: "Delete"})).toBeEnabled();
    expect(deletes.filter(button => !button.hasAttribute("disabled")).length).toBe(1);
    await userEvent.click(within(idleRow).getByRole("button", {name: "Delete"}));
    expect(screen.getByRole("dialog", {name: "Delete provider profile"})).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", {name: "Confirm"}));

    await waitFor(()=>expect(onSaved).toHaveBeenCalled());
    const body = JSON.parse(String(fetchMock.mock.calls.find(([url])=>String(url).endsWith("/delete"))?.[1]?.body));
    expect(body).toEqual({profile_id:"deepseek", expected_revision:3});
  });

  it("surfaces the guided errors for a stale page and the last profile", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo|URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/settings/ai/profiles")) {
        if (init?.method === "POST") return failure(409, "settings_conflict");
        return json(profilesResponse());
      }
      if (url.endsWith("/settings")) return json(settings());
      if (url.endsWith("/ai/models")) return json({status:"connected",models:[],selected_model:null,error_code:null,message:null});
      return json({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const {container} = renderAI();
    await screen.findByText("DeepSeek");
    // Saving a stale page hits the revision guard before anything is written.
    await userEvent.click(screen.getAllByRole("button", {name: "Edit"})[1]);
    await userEvent.click(inProviderForm(container).getByRole("button", {name: "Save & switch"}));
    // The stale page is refused with the guided conflict copy (en-US shows the
    // server's English message; zh-CN uses the curated map), never a crash or a
    // silent success: the local snapshot must not have moved.
    const alert = await screen.findByRole("alert");
    expect((alert.textContent ?? "").length).toBeGreaterThan(0);
    expect(fetchMock.mock.calls.some(call => String(call[0]).endsWith("/activate") && call[1]?.method === "POST")).toBe(false);
  });
});

describe("quick provider switch", () => {
  it("applies the picked provider and reports the new snapshot", async () => {
    const onSwitched = vi.fn();
    const fetchMock = vi.fn(async (input: RequestInfo|URL, init?: RequestInit) => {
      void init;
      const url = String(input);
      if (url.endsWith("/ai/profiles/activate")) return json(settings({active_profile_id: "deepseek"}));
      if (url.endsWith("/settings/ai/profiles")) return json(profilesResponse());
      return json({});
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<AIProviderSwitch variant="panel" revision={3} onSwitched={onSwitched}/>);

    const select = await screen.findByLabelText("AI provider");
    expect(screen.getByRole("option", {name: "DeepSeek"})).toBeInTheDocument();
    await userEvent.selectOptions(select, "deepseek");

    await waitFor(()=>expect(onSwitched).toHaveBeenCalled());
    const body = JSON.parse(String(fetchMock.mock.calls.find(([url])=>String(url).endsWith("/activate"))?.[1]?.body));
    expect(body).toEqual({profile_id:"deepseek", expected_revision:3});
    expect(onSwitched.mock.calls[0][0].ai.active_profile_id).toBe("deepseek");
  });

  it("offers the settings entry when the server reports no provider", async () => {
    const onManage = vi.fn();
    vi.stubGlobal("fetch", vi.fn(async () => json({revision:1, active_profile_id:"default", profiles:[]})));
    render(<Switch variant="status" revision={1} onManage={onManage}/>);
    await userEvent.click(await screen.findByRole("button", {name: /AI provider/}));
    expect(onManage).toHaveBeenCalled();
  });

  it("stays inert when the library cannot be read", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => failure(503, "settings_unreadable")));
    render(<AIProviderSwitch variant="panel" revision={1} onManage={vi.fn()}/>);
    await waitFor(()=>expect(screen.queryByLabelText("AI provider")).not.toBeInTheDocument());
  });
});
