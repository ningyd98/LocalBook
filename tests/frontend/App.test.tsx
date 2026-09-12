import { screen, waitFor } from "@testing-library/react";
import { render } from "./render";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "../../apps/web/src/App";
import { setVaultSession } from "../../apps/web/src/api/client";
import type { AIStatusResponse } from "../../apps/web/src/api/types";
import { useWorkspaceStore } from "../../packages/workspace/src";

const settings = {revision:1,vault_session_id:"workspace-a",changing:false,version:"0.2.0",vault:{root:"/notes",status:"ready"},ai:{enabled:true,base_url:null,chat_model:"auto",active_profile_id:"default",profiles:[]}};
const ok = (payload: unknown) => new Response(JSON.stringify(payload), {status:200});
const failure = (status: number, code: string) => new Response(JSON.stringify({error:{code,message:code,path:null}}), {status});
function aiStatus(overrides: Partial<AIStatusResponse> = {}): AIStatusResponse {
  return {status:"not_configured",provider:"omlx",endpoint:null,qwen_model:null,models:[],capabilities:{chat:false,embedding:false,rerank:false},error_code:"not_configured",message:"AI endpoint is not configured",checked_at:null,...overrides};
}
function install(ai: AIStatusResponse = aiStatus(), vault: () => Response = () => ok({entries:[]})) {
  const mock = vi.fn(async (input: RequestInfo | URL) => {
    const url=String(input);
    if(url.endsWith("/health")) return ok({status:"ok"});
    if(url.endsWith("/ai/status")) return ok(ai);
    // PLAN-PROVIDERS: the provider switch reads this path; an empty library keeps
    // the status bar free of a provider selector.
    if(url.endsWith("/settings/ai/profiles")) return ok({revision:1,active_profile_id:"default",profiles:[]});
    if(url.endsWith("/settings")) return ok(settings);
    if(url.includes("/vault/files")) return vault();
    return failure(404,"not_found");
  });
  vi.stubGlobal("fetch",mock);return mock;
}
beforeEach(()=>{vi.restoreAllMocks();setVaultSession(null);useWorkspaceStore.getState().resetVault();});

describe("LocalNote workbench status",()=>{
  it("shows a compact status bar and keeps the note workspace central",async()=>{
    install();render(<App/>);
    expect(await screen.findByRole("button",{name:"Local service · Connected"})).toBeInTheDocument();
    await waitFor(()=>expect(useWorkspaceStore.getState().tree.status).toBe("ready"));
    expect(screen.getByRole("button",{name:"Vault：Connected"})).toBeInTheDocument();
    expect(screen.getByRole("button",{name:"AI：Not configured"})).toBeInTheDocument();
    expect(screen.getByRole("heading",{name:"A quiet space for your thoughts."})).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
  it("keeps the local service connected when AI is offline",async()=>{
    install(aiStatus({status:"offline",error_code:"timeout"}));render(<App/>);
    expect(await screen.findByRole("button",{name:"Local service · Connected"})).toBeInTheDocument();
    expect(screen.getByRole("button",{name:"AI：Offline"})).toBeInTheDocument();
  });
  it("shows the discovered model in connection details",async()=>{
    install(aiStatus({status:"connected",qwen_model:"Qwen3.5-4B-Instruct-4bit",error_code:null}));render(<App/>);
    await userEvent.click(await screen.findByRole("button",{name:"AI：Connected"}));
    expect(screen.getByRole("dialog",{name:"Connection status"})).toBeInTheDocument();
    expect(screen.getByText("Qwen3.5-4B-Instruct-4bit")).toBeInTheDocument();
  });
  it("distinguishes a reachable service from an unavailable model",async()=>{
    install(aiStatus({status:"connected",error_code:"no_matching_model"}));render(<App/>);
    expect(await screen.findByRole("button",{name:"AI：Model unavailable"})).toBeInTheDocument();
  });
  it("retains a usable shell and pauses loading until settings can be read",async()=>{
    vi.stubGlobal("fetch",vi.fn(async()=>{throw new TypeError("Failed to fetch");}));render(<App/>);
    expect(await screen.findByRole("button",{name:"Local service · Disconnected"})).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Cannot load service configuration");
    expect(useWorkspaceStore.getState().tree.status).toBe("idle");
  });
  it.each([[503,"vault_not_configured","not_configured","Not configured"],[503,"vault_unavailable","unavailable","Unavailable"],[500,"internal_error","error","Error"]])("reflects vault failure %s %s",async(status,code,state,label)=>{
    install(aiStatus(),()=>failure(Number(status),String(code)));render(<App/>);
    await waitFor(()=>expect(useWorkspaceStore.getState().tree.status).toBe(state));
    expect(screen.getByRole("button",{name:`Vault：${label}`})).toBeInTheDocument();
  });
  it("reconnects through the failure banner and restores the file tree",async()=>{
    vi.stubGlobal("fetch",vi.fn(async()=>{throw new TypeError("Failed to fetch");}));render(<App/>);
    await screen.findByRole("button",{name:"Local service · Disconnected"});install();
    await userEvent.click(screen.getByRole("button",{name:"Refresh status"}));
    expect(await screen.findByRole("button",{name:"Local service · Connected"})).toBeInTheDocument();
    await waitFor(()=>expect(useWorkspaceStore.getState().tree.status).toBe("ready"));
  });
});

describe("Vault switch recovery",()=>{
  it("reconciles a lost switch response without submitting the switch twice",async()=>{
    let switched=false;let switchCalls=0;
    const next={...settings,revision:2,vault_session_id:"workspace-b",vault:{root:"/notes-b",status:"ready"}};
    vi.stubGlobal("fetch",vi.fn(async(input:RequestInfo|URL)=>{
      const url=String(input);
      if(url.endsWith("/vault/switch")){switched=true;switchCalls++;throw new TypeError("response lost");}
      if(url.endsWith("/settings"))return ok(switched?next:settings);
      if(url.endsWith("/health"))return ok({status:"ok"});
      if(url.endsWith("/ai/status"))return ok(aiStatus());
      return ok({entries:[]});
    }));
    render(<App/>);await userEvent.click(await screen.findByRole("button",{name:"Vault：Connected"}));
    const field=await screen.findByRole("textbox");await waitFor(()=>expect(field).toHaveValue("/notes"));
    await userEvent.clear(field);await userEvent.type(field,"/notes-b");
    await userEvent.click(screen.getByRole("button",{name:"Save notes & switch"}));
    await waitFor(()=>expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(switchCalls).toBe(1);expect(useWorkspaceStore.getState().vaultStale).toBe(false);
    expect(useWorkspaceStore.getState().workspaceFrozen).toBe(false);
  });
  it("pauses the workspace while a disconnected switch outcome is unknown",async()=>{
    let lost=false;
    vi.stubGlobal("fetch",vi.fn(async(input:RequestInfo|URL)=>{
      const url=String(input);
      if(url.endsWith("/vault/switch")){lost=true;throw new TypeError("disconnected");}
      if(url.endsWith("/settings")){if(lost)throw new TypeError("disconnected");return ok(settings);}
      if(url.endsWith("/health"))return ok({status:"ok"});
      if(url.endsWith("/ai/status"))return ok(aiStatus());
      return ok({entries:[]});
    }));
    render(<App/>);await userEvent.click(await screen.findByRole("button",{name:"Vault：Connected"}));
    const field=await screen.findByRole("textbox");await waitFor(()=>expect(field).toHaveValue("/notes"));
    await userEvent.clear(field);await userEvent.type(field,"/notes-b");
    await userEvent.click(screen.getByRole("button",{name:"Save notes & switch"}));
    await waitFor(()=>expect(screen.getAllByText(/switch result is uncertain/).length).toBeGreaterThan(0));
    expect(useWorkspaceStore.getState().workspaceFrozen).toBe(true);
    expect(screen.getByRole("dialog",{name:"Settings"})).toBeInTheDocument();
  });
});
