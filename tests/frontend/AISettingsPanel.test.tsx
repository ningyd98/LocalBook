import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "./render";
import { SettingsPanel } from "../../apps/web/src/components/SettingsPanel";
import { configureWorkspaceApi, preferenceDefaults, useWorkspaceStore } from "../../packages/workspace/src";
import { setVaultSession } from "../../apps/web/src/api/client";
import type { ServiceSettings } from "../../apps/web/src/api/settings";

const settings: ServiceSettings = {revision:3,vault_session_id:"a",changing:false,version:"0.2.0",vault:{root:"/notes/a",status:"ready"},ai:{enabled:true,base_url:"http://127.0.0.1:8234/v1",api_key_set:false,chat_model:"auto"}};
const json = (data: unknown, status=200)=>new Response(JSON.stringify(data),{status});
const renderAI = (onSaved=vi.fn())=>render(<SettingsPanel settings={settings} onClose={vi.fn()} onSaved={onSaved} onSwitch={vi.fn()} initialSection="ai"/>);

beforeEach(()=>{
  localStorage.clear();
  useWorkspaceStore.getState().resetVault();
  useWorkspaceStore.setState({...preferenceDefaults});
  setVaultSession("a");
  configureWorkspaceApi({fetchVaultFiles:async()=>({entries:[]}),fetchVaultFile:async()=>{throw new Error("unused")},patchVaultFile:async()=>{throw new Error("unused")}});
});

describe("AI model discovery and API key",()=>{
  it("auto-fetches models, offers them in a dropdown and saves the key",async()=>{
    const onSaved=vi.fn();
    const fetchMock=vi.fn(async(input:RequestInfo|URL,init?:RequestInit)=>{
      const url=String(input);
      if(url.endsWith("/ai/models")) return json({status:"connected",models:[{id:"qwen3.5-4b",owned_by:"local"},{id:"gpt-oss-20b",owned_by:"local"}],selected_model:"qwen3.5-4b",error_code:null,message:null});
      if(init?.method==="PATCH") return json({...settings,revision:4,ai:{...settings.ai,chat_model:"gpt-oss-20b",api_key_set:true}});
      return json(settings);
    });
    vi.stubGlobal("fetch",fetchMock);
    renderAI(onSaved);

    // Discovery runs on opening the AI section, no button press needed.
    await waitFor(()=>expect(fetchMock.mock.calls.some(([url])=>String(url).endsWith("/ai/models"))).toBe(true));
    const select=await screen.findByLabelText("Select model");
    expect(screen.getByRole("option",{name:/qwen3\.5-4b/})).toBeInTheDocument();

    await userEvent.selectOptions(select,"gpt-oss-20b");
    await userEvent.type(screen.getByPlaceholderText("sk-…"),"sk-secret");
    await userEvent.click(screen.getByRole("button",{name:"Save & apply"}));

    await waitFor(()=>expect(onSaved).toHaveBeenCalled());
    const body=fetchMock.mock.calls.find(([,init])=>init?.method==="PATCH")?.[1]?.body;
    expect(JSON.parse(String(body))).toEqual({expected_revision:3,ai:{enabled:true,base_url:settings.ai.base_url,chat_model:"gpt-oss-20b",api_key:"sk-secret"}});
  });

  it("surfaces the 401 hint and still lets the user save a key",async()=>{
    const onSaved=vi.fn();
    const fetchMock=vi.fn(async(input:RequestInfo|URL,init?:RequestInit)=>{
      const url=String(input);
      if(url.endsWith("/ai/models")) return json({status:"offline",models:[],selected_model:null,error_code:"auth_error",http_status:401,message:"rejected"});
      if(init?.method==="PATCH") return json({...settings,revision:4,ai:{...settings.ai,api_key_set:true}});
      return json(settings);
    });
    vi.stubGlobal("fetch",fetchMock);
    renderAI(onSaved);

    expect(await screen.findByText(/requires authentication/i)).toBeInTheDocument();
    expect(screen.queryByLabelText("Select model")).not.toBeInTheDocument();

    await userEvent.type(screen.getByPlaceholderText("sk-…"),"sk-secret");
    await userEvent.click(screen.getByRole("button",{name:"Save & apply"}));
    await waitFor(()=>expect(onSaved).toHaveBeenCalled());
  });

  it("never sends the stored key when the field is untouched",async()=>{
    const fetchMock=vi.fn(async(input:RequestInfo|URL,init?:RequestInit)=>{
      if(init?.method==="PATCH") return json({...settings,revision:4});
      if(String(input).endsWith("/ai/models")) return json({status:"connected",models:[{id:"m"}],selected_model:"m",error_code:null,message:null});
      return json(settings);
    });
    vi.stubGlobal("fetch",fetchMock);
    renderAI();
    await screen.findByLabelText("Select model");
    await userEvent.click(screen.getByRole("button",{name:"Save & apply"}));
    await waitFor(()=>expect(fetchMock.mock.calls.some(([,init])=>init?.method==="PATCH")).toBe(true));
    const body=fetchMock.mock.calls.find(([,init])=>init?.method==="PATCH")?.[1]?.body;
    expect(JSON.parse(String(body))).toEqual({expected_revision:3,ai:{enabled:true,base_url:settings.ai.base_url,chat_model:"auto"}});
  });
});
