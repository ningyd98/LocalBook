import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { render } from "./render";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { Dialog } from "../../packages/ui/src";
import { SettingsPanel } from "../../apps/web/src/components/SettingsPanel";
import { WorkspaceShell } from "../../apps/web/src/components/WorkspaceShell";
import { PreviewPane } from "../../apps/web/src/components/PreviewPane";
import { configureWorkspaceApi, preferenceDefaults, toBase64, useWorkspaceStore } from "../../packages/workspace/src";
import { readPreferences } from "../../packages/workspace/src/preferences";
import { fetchVaultFile, patchVaultFile, setVaultSession } from "../../apps/web/src/api/client";
import type { ServiceSettings } from "../../apps/web/src/api/settings";

const settings: ServiceSettings = {revision:3,vault_session_id:"a",changing:false,version:"0.2.0",vault:{root:"/notes/a",status:"ready"},ai:{enabled:true,base_url:"http://127.0.0.1:8234/v1",chat_model:"auto"}};
const read = (content="original",sha="sha256:base")=>({path:"same.md",content_base64:toBase64(content),byte_length:content.length,sha256:sha,content_type:"text/markdown"});
const mutation = {path:"same.md",sha256:"sha256:new",byte_length:7,operation:"updated" as const};
function setupApi(patch=vi.fn(async()=>mutation)) {
  configureWorkspaceApi({fetchVaultFiles:async()=>({entries:[]}),fetchVaultFile:async()=>read(),patchVaultFile:patch});return patch;
}
const json = (data: unknown, status=200)=>new Response(JSON.stringify(data),{status});
const renderSettings = (section: "appearance"|"editor"|"ai"="appearance", onSaved=vi.fn())=>render(<SettingsPanel settings={settings} onClose={vi.fn()} onSaved={onSaved} onSwitch={vi.fn()} initialSection={section}/>);

beforeEach(()=>{localStorage.clear();useWorkspaceStore.getState().resetVault();useWorkspaceStore.setState({...preferenceDefaults});setVaultSession("a");setupApi();vi.stubGlobal("fetch",vi.fn(async()=>json(settings)));});
afterEach(()=>vi.useRealTimers());

describe("Saved preferences and settings",()=>{
  it("persists appearance and language immediately",async()=>{
    renderSettings();await userEvent.click(screen.getByRole("button",{name:"Light"}));
    expect(readPreferences().theme).toBe("light");
    await userEvent.selectOptions(screen.getByLabelText("Language"),"zh-CN");
    expect(screen.getByRole("dialog",{name:"设置"})).toBeInTheDocument();
    expect(localStorage.getItem("localnote-locale")).toBe("zh-CN");
  });
  it("allows replacing the autosave delay and clamps it on blur",async()=>{
    renderSettings("editor");const field=screen.getByRole("spinbutton");
    await userEvent.clear(field);await userEvent.type(field,"1800");
    expect(useWorkspaceStore.getState().autoSaveDelay).toBe(1800);
    await userEvent.clear(field);await userEvent.type(field,"9000");fireEvent.blur(field);
    expect(readPreferences().autoSaveDelay).toBe(5000);
  });
  it("tests unsaved AI models without saving and preserves discovery choices",async()=>{
    const fetchMock=vi.fn(async(input:RequestInfo|URL)=>String(input).endsWith("/ai/test")?json({status:"connected",models:[{id:"local-model",capabilities:{chat:true}}],selected_model:"local-model",message:null}):json(settings));
    vi.stubGlobal("fetch",fetchMock);renderSettings("ai");
    await userEvent.click(screen.getByRole("button",{name:"Test connection"}));
    expect(await screen.findByText("Connected · local-model")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url])=>String(url).endsWith("/ai/test"))).toBe(true);
    expect(document.querySelector('option[value="local-model"]')).toBeInTheDocument();
  });
  it("saves offline AI configuration with an explicit revision",async()=>{
    const saved=vi.fn();const fetchMock=vi.fn(async(_url:RequestInfo|URL,init?:RequestInit)=>json(init?.method==="PATCH"?{...settings,revision:4}:settings));
    vi.stubGlobal("fetch",fetchMock);renderSettings("ai",saved);
    await userEvent.click(screen.getByRole("button",{name:"Save & apply"}));
    await waitFor(()=>expect(saved).toHaveBeenCalledWith({...settings,revision:4}));
    const body=fetchMock.mock.calls.find(([,init])=>init?.method==="PATCH")?.[1]?.body;
    expect(JSON.parse(String(body))).toEqual({expected_revision:3,ai:settings.ai});
  });
});

describe("Editor lifecycle and save safety",()=>{
  it("cancels automatic saves when disabled and keeps manual save working",async()=>{
    vi.useFakeTimers();const patch=setupApi();await useWorkspaceStore.getState().openFile("same.md");render(<WorkspaceShell/>);
    await act(async()=>useWorkspaceStore.getState().updateContent("same.md","edited"));
    await act(async()=>useWorkspaceStore.getState().setPreferences({autoSave:false}));
    await act(async()=>vi.advanceTimersByTime(5000));expect(patch).not.toHaveBeenCalled();
    await act(async()=>useWorkspaceStore.getState().save("same.md","manual"));expect(patch).toHaveBeenCalledTimes(1);
  });
  it("reschedules dirty content after the delay changes",async()=>{
    vi.useFakeTimers();const patch=setupApi();await useWorkspaceStore.getState().openFile("same.md");render(<WorkspaceShell/>);
    await act(async()=>useWorkspaceStore.getState().updateContent("same.md","edited"));
    await act(async()=>vi.advanceTimersByTime(500));
    await act(async()=>useWorkspaceStore.getState().setPreferences({autoSaveDelay:1600}));
    await act(async()=>vi.advanceTimersByTime(1599));expect(patch).not.toHaveBeenCalled();
    await act(async()=>vi.advanceTimersByTime(1));expect(patch).toHaveBeenCalledTimes(1);
  });
  it("keeps the editor instance and local draft through all modes",async()=>{
    await useWorkspaceStore.getState().openFile("same.md");render(<WorkspaceShell/>);
    const editor=document.querySelector(".cm-editor");
    await act(async()=>useWorkspaceStore.getState().updateContent("same.md","retained draft"));
    for(const mode of ["Preview","Split","Edit"]){await userEvent.click(screen.getByRole("button",{name:mode}));expect(document.querySelector(".cm-editor")).toBe(editor);}
    expect(useWorkspaceStore.getState().sessions["same.md"]?.content).toBe("retained draft");
  });
  it("leaves source bytes alone when presenting note properties",()=>{
    render(<PreviewPane source={"---\ntitle: Title\ntags: [notes]\n---\n# Actual heading\n"}/>);
    expect(screen.getByRole("heading",{name:"Actual heading"})).toBeInTheDocument();
    expect(screen.queryByRole("heading",{name:/title:/})).not.toBeInTheDocument();
    expect(screen.getByText("Note properties")).toBeInTheDocument();
  });
  it("does not discard drafts when saveAll encounters a conflict",async()=>{
    setupApi(vi.fn(async()=>{throw Object.assign(new Error("changed on disk"),{status:409,code:"file_conflict"});}));
    await useWorkspaceStore.getState().openFile("same.md");useWorkspaceStore.getState().updateContent("same.md","local draft");
    useWorkspaceStore.setState({workspaceFrozen:true});expect(await useWorkspaceStore.getState().saveAll()).toBe(false);
    expect(useWorkspaceStore.getState().sessions["same.md"]).toMatchObject({content:"local draft",dirty:true,saveState:"conflict"});
  });
  it("ignores late file responses after a vault reset",async()=>{
    let resolve!: (value:ReturnType<typeof read>)=>void;
    configureWorkspaceApi({fetchVaultFiles:async()=>({entries:[]}),fetchVaultFile:()=>new Promise(r=>{resolve=r;}),patchVaultFile:async()=>mutation});
    const pending=useWorkspaceStore.getState().openFile("same.md");useWorkspaceStore.getState().resetVault();setupApi();
    await useWorkspaceStore.getState().openFile("same.md");resolve(read("old vault response"));await pending;
    expect(useWorkspaceStore.getState().sessions["same.md"]?.content).toBe("original");
  });
  it("freezes both automatic and manual saves for a stale page",async()=>{
    const patch=setupApi();await useWorkspaceStore.getState().openFile("same.md");useWorkspaceStore.getState().updateContent("same.md","local draft");
    useWorkspaceStore.setState({vaultStale:true,workspaceFrozen:true});await useWorkspaceStore.getState().save("same.md","manual");await useWorkspaceStore.getState().save("same.md","auto");
    expect(patch).not.toHaveBeenCalled();expect(useWorkspaceStore.getState().sessions["same.md"]?.dirty).toBe(true);
  });
});

describe("HTTP session and dialog contracts",()=>{
  it("attaches the vault session to writes and rejects late successful responses",async()=>{
    const mock=vi.fn(async()=>json(mutation));vi.stubGlobal("fetch",mock);
    await patchVaultFile({path:"same.md",contentBase64:toBase64("new"),expectedSha256:"sha256:old"});
    const init=(mock.mock.calls[0] as unknown as [string,RequestInit])[1];expect(new Headers(init.headers).get("X-LocalNote-Vault-Session")).toBe("a");
    let resolve!:(value:Response)=>void;vi.stubGlobal("fetch",vi.fn(()=>new Promise<Response>(r=>{resolve=r;})));
    const pending=fetchVaultFile("same.md");setVaultSession("b");resolve(json(read()));
    await expect(pending).rejects.toMatchObject({code:"stale_response"});
  });
  it("traps focus and returns it after closing a dialog",async()=>{
    function Harness(){const [open,setOpen]=useState(false);return <><button onClick={()=>setOpen(true)}>Open settings</button>{open&&<Dialog label="Settings" onClose={()=>setOpen(false)}><button>First</button><button>Last</button></Dialog>}</>;}
    render(<Harness/>);await userEvent.click(screen.getByRole("button",{name:"Open settings"}));
    expect(screen.getByRole("button",{name:"First"})).toHaveFocus();await userEvent.tab({shift:true});expect(screen.getByRole("button",{name:"Last"})).toHaveFocus();
    await userEvent.tab();expect(screen.getByRole("button",{name:"First"})).toHaveFocus();await userEvent.keyboard("{Escape}");expect(screen.getByRole("button",{name:"Open settings"})).toHaveFocus();
  });
});
