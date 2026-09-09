import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "./api/client";
import { configureWorkspaceApi, useWorkspaceStore } from "@localnote/workspace";
import { Button, Dialog, Icon, IconButton } from "@localnote/ui";
import { Ribbon } from "./components/Ribbon";
import type { RibbonTool } from "./components/Ribbon";
import { WorkspaceShell } from "./components/WorkspaceShell";
import { SearchPanel } from "./components/SearchPanel";
import { GraphPanel } from "./components/GraphPanel";
import { AIPanel } from "./components/AIPanel";
import { NotesLinksPanel } from "./components/NotesLinksPanel";
import { AIHistoryPanel } from "./components/AIHistoryPanel";
import { SchedulerStatus } from "./components/SchedulerStatus";
import { SettingsPanel } from "./components/SettingsPanel";
import { fetchSettings, switchVault } from "./api/settings";
import type { ServiceSettings } from "./api/settings";
import type { AIStatusResponse } from "./api/types";
import { useI18n } from "./i18n";

configureWorkspaceApi({
  fetchVaultFiles: api.fetchVaultFiles,
  fetchVaultFile: api.fetchVaultFile,
  patchVaultFile: api.patchVaultFile,
  createVaultFile: api.createVaultFile,
  createVaultDirectory: api.createVaultDirectory,
  moveVaultFile: api.moveVaultFile,
  // Attachment upload channel (ATT-10/ATT-11). Both must be registered here or
  // the store short-circuits with "Attachment API is not configured".
  uploadAttachmentBase64: api.uploadAttachmentBase64,
  uploadAttachmentMultipart: api.uploadAttachmentMultipart,
  fetchLinks: api.fetchLinks,
  fetchBacklinks: api.fetchBacklinks,
  searchNotes: api.searchNotes,
  fetchGraph: api.fetchGraph,
  fetchLocalGraph: api.fetchLocalGraph,
  fetchTagGraph: api.fetchTagGraph,
  aiChat: api.aiChat,
  aiSummarize: api.aiSummarize,
  aiTags: api.aiTags,
  aiRelated: api.aiRelated,
  aiExtractTodos: api.aiExtractTodos,
  aiClassify: api.aiClassify,
  listHistory: api.listHistory,
  getHistory: api.getHistory,
  createJob: api.createJob,
  acceptJob: api.acceptJob,
  rejectJob: api.rejectJob,
  undoHistory: api.undoHistory,
});

export default function App() {
  const {t,tr,errorText} = useI18n(); const store = useWorkspaceStore();
  const [sidebarRequest,setSidebarRequest] = useState(0);
  const [tool,setTool] = useState<RibbonTool>("files"); const [inspector,setInspector] = useState<"ai"|"links"|null>(null);
  const [settingsOpen,setSettingsOpen] = useState(false); const [settingsSection,setSettingsSection] = useState<"appearance"|"vault">("appearance");
  const [settings,setSettings] = useState<ServiceSettings|null>(null); const [ready,setReady] = useState(false); const [server,setServer] = useState("checking"); const [ai,setAI] = useState<AIStatusResponse|null>(null); const [systemOpen,setSystemOpen] = useState(false); const [connectionError,setConnectionError] = useState<string|null>(null); const [activityTab,setActivityTab] = useState<"history"|"scheduler">("history");
  const boundRoot = useRef<string|null>(null); const refreshVersion = useRef(0);
  const switchUncertain = useRef(false);
  const refresh = useCallback(async () => {
    const version = ++refreshVersion.current;
    const results = await Promise.allSettled([api.fetchHealth(),api.fetchAIStatus(),fetchSettings()]);
    if(version !== refreshVersion.current) return;
    setServer(results[0].status === "fulfilled" ? "connected" : "disconnected");
    setAI(results[1].status === "fulfilled" ? results[1].value : null);
    if(results[2].status === "fulfilled") {
      const next = results[2].value; const prior = api.getVaultSession();
      if(next.changing) {
        switchUncertain.current=true;
        useWorkspaceStore.setState({workspaceFrozen:true});
        setConnectionError(tr("工作空间正在切换，请稍后刷新状态。", "The workspace is switching. Refresh its status shortly."));
        return;
      }
      switchUncertain.current=false;
      if(prior && next.vault_session_id !== prior) useWorkspaceStore.setState({vaultStale:true, workspaceFrozen:true});
      else {api.setVaultSession(next.vault_session_id);boundRoot.current=next.vault.root;useWorkspaceStore.setState({workspaceFrozen:useWorkspaceStore.getState().vaultStale || switchUncertain.current});}
      setSettings(next);setConnectionError(null);setReady(true);
    } else {setConnectionError(tr("无法读取服务配置，请确认后端已更新并运行。", "Cannot load service configuration. Check that the updated backend is running."));}
  },[tr]);
  // tr is recreated when locale changes; use a ref for focus refresh without duplicate startup requests.
  const refreshRef = useRef(refresh); refreshRef.current=refresh;
  useEffect(()=>{void refreshRef.current(); const stale=()=>useWorkspaceStore.setState({vaultStale:true,workspaceFrozen:true}); const focus=()=>void refreshRef.current(); window.addEventListener("localnote-vault-changed",stale);window.addEventListener("focus",focus);return()=>{window.removeEventListener("localnote-vault-changed",stale);window.removeEventListener("focus",focus);};},[]);
  const showNotes=()=>setTool("files");
  const openNote=(path:string)=>{void store.openFile(path);setTool("files");};
  const openSettings=(section:"appearance"|"vault"="appearance")=>{setSettingsSection(section);setSettingsOpen(true);};
  const select=(next:RibbonTool)=>{if(next==="settings")openSettings();else if(next==="ai"){setInspector(v=>v==="ai"?null:"ai");setTool("files");}else {setTool(next);if(window.matchMedia?.("(max-width:1099px)").matches)setInspector(null);if(next==="files"||next==="search"){store.setPreferences({sidebarOpen:true});setSidebarRequest(v=>v+1);}}};
  const applySettings=(value:ServiceSettings)=>{setSettings(value);void refreshRef.current();};
  const changeVault=async(root:string,current:ServiceSettings)=>{
    const snapshot=useWorkspaceStore.getState(); const dirty=Object.values(snapshot.sessions).some(s=>s.dirty);
    const recovering=snapshot.vaultStale && dirty && root.replace(/\/+$/,"") === boundRoot.current;
    if(snapshot.vaultStale && dirty && !recovering) throw new Error(tr("草稿属于原笔记库。请先切回原路径，或复制并处理未保存的内容。", "Your draft belongs to the previous vault. Switch back to that path or copy and resolve the unsaved content first."));
    useWorkspaceStore.setState({workspaceFrozen:true});
    try {
      if(!recovering && dirty && !await snapshot.saveAll()) throw new Error(tr("有笔记尚未成功保存，请先解决冲突或保存错误。", "Some notes could not be saved. Resolve conflicts or save errors first."));
      let next:ServiceSettings;
      try {next=await switchVault(root,current.revision,current.vault_session_id);}
      catch(error){
        if(error instanceof api.ApiError && error.status===0){
          switchUncertain.current=true;
          let actual:ServiceSettings;
          try {actual=await fetchSettings();}
          catch {
            const message=tr("连接中断，尚不能确认切换结果。草稿已保留，编辑与保存暂停，请先刷新状态。", "Connection lost; the switch result is uncertain. Drafts are preserved and editing is paused. Refresh status before continuing.");
            setConnectionError(message);throw new Error(message);
          }
          if(actual.changing) throw new Error(tr("切换仍在进行，请稍后刷新状态。", "The switch is still running. Refresh status shortly."));
          switchUncertain.current=false;
          if(actual.vault.root===root.replace(/\/+$/,""))next=actual;
          else throw error;
        }
        else throw error;
      }
      if(next.vault_session_id===api.getVaultSession() && next.vault.root===boundRoot.current) {setSettings(next);return;}
      if(!recovering) snapshot.resetVault();
      api.setVaultSession(next.vault_session_id);boundRoot.current=next.vault.root;
      useWorkspaceStore.setState({vaultStale:false,workspaceFrozen:false});setSettings(next);setTool("files");setInspector(null);
      await useWorkspaceStore.getState().loadTree();void refreshRef.current();
    } finally {useWorkspaceStore.setState({workspaceFrozen:useWorkspaceStore.getState().vaultStale || switchUncertain.current});}
  };
  const session=store.activePath?store.sessions[store.activePath]:undefined;
  const vaultLabel=store.tree.status==="ready"?t.status.connected:store.tree.status==="not_configured"?t.status.notConfigured:store.tree.status==="error"?t.status.error:store.tree.status==="unavailable"?t.status.unavailable:t.status.checking;
  const aiLabel=settings?.ai.enabled===false?tr("已关闭","Disabled"):ai?.status==="connected"?(ai.error_code==="no_matching_model"?tr("模型不可用","Model unavailable"):t.status.connected):ai?.status==="not_configured"?t.status.notConfigured:t.status.offline;
  const right=inspector?<><header className="inspector-header"><div role="tablist" aria-label={tr("辅助工具","Inspector tools")}><button role="tab" aria-selected={inspector==="links"} onClick={()=>setInspector("links")}><Icon name="link" size={15}/>{tr("关联","Links")}</button><button role="tab" aria-selected={inspector==="ai"} onClick={()=>setInspector("ai")}><Icon name="ai" size={15}/>{t.ai.title}</button></div><IconButton onClick={()=>setInspector(null)} aria-label={tr("关闭辅助面板","Close inspector")}><Icon name="close" size={15}/></IconButton></header>{inspector==="ai"?<AIPanel enabled={settings?.ai.enabled !== false} onClose={()=>setInspector(null)} onOpenNote={openNote}/>:store.activePath?<NotesLinksPanel relations={store.relations} onOpen={openNote} onCreate={target => void store.openOrCreateLinkedNote(store.activePath, target).catch(() => undefined)} onRetry={()=>{if(store.activePath)void store.loadRelations(store.activePath);}}/>:<div className="side-empty"><Icon name="link" size={28}/><p>{tr("打开笔记，查看引用与反向链接。","Open a note to explore its outgoing links and backlinks.")}</p></div>}</>:undefined;
  const center=tool==="graph"?<GraphPanel onOpenNote={openNote} onClose={showNotes}/>:tool==="history"?<section className="activity"><header className="activity-header"><div><p className="eyebrow">WORKSPACE ACTIVITY</p><h1>{tr("任务与历史","Tasks & history")}</h1></div><div className="view-switch"><button aria-pressed={activityTab==="history"} onClick={()=>setActivityTab("history")}>{tr("操作记录","History")}</button><button aria-pressed={activityTab==="scheduler"} onClick={()=>setActivityTab("scheduler")}>{tr("计划任务","Schedules")}</button></div></header>{activityTab==="history"?<AIHistoryPanel onClose={showNotes}/>:<SchedulerStatus active={server==="connected"} onPreview={id=>{void store.selectHistory(id);setActivityTab("history");}}/>}</section>:undefined;
  return <div className="app-shell" data-theme={store.theme}><Ribbon activeTool={settingsOpen?"settings":inspector==="ai"&&tool==="files"?"ai":tool} onSelect={select}/><div className="app-content">{connectionError&&<div role="alert" className="connection-banner">{connectionError}<Button onClick={()=>void refresh()}>{t.status.refreshStatus}</Button></div>}<WorkspaceShell sidebarRequest={sidebarRequest} apiReady={ready} vaultName={boundRoot.current?.split("/").at(-1)??"LocalNote"} leftView={tool==="search"?<SearchPanel onOpen={openNote} onClose={showNotes}/>:undefined} centerView={center} inspector={right} onToggleInspector={()=>setInspector(v=>v?null:"links")} onShowNotes={showNotes} onOpenSettings={()=>openSettings("vault")} onOpenSearch={()=>select("search")} onOpenGraph={()=>setTool("graph")}/><footer className="status-bar"><button onClick={()=>setSystemOpen(true)} title={tr("查看连接状态","View connection status")}><span className={`status-dot ${server==="connected"?"ok":"warn"}`}/>{tr("本地服务","Local service")} · {server==="connected"?t.status.connected:server==="disconnected"?t.status.disconnected:t.status.checking}</button><span className="status-divider"/><button onClick={()=>openSettings("vault")}>{tr("笔记库","Vault")}：{vaultLabel}</button><button onClick={()=>setSystemOpen(true)}><Icon name="ai" size={12}/>AI：{aiLabel}</button><div className="status-spacer"/>{session&&<><span className="word-count">{tr(`${Array.from(session.content).length} 字符`,`${Array.from(session.content).length} characters`)}</span><span className={`save-indicator ${session.saveState}`} role="status"><span className={`status-dot ${session.dirty?"warn":"ok"}`}/>{session.dirty&&session.saveState==="saved"?tr("未保存","Unsaved"):t.saveState[session.saveState]}</span><span className="encoding">UTF-8 · {session.lineSeparator}</span></>}<span className="local-label">LOCAL FIRST</span></footer></div>
    {settingsOpen&&<SettingsPanel settings={settings} onClose={()=>setSettingsOpen(false)} onSaved={applySettings} onSwitch={changeVault} initialSection={settingsSection}/>}
    {systemOpen&&<Dialog label={tr("连接状态","Connection status")} onClose={()=>setSystemOpen(false)} className="connection-dialog"><header className="dialog-header"><h2>{tr("连接状态","Connection status")}</h2><IconButton onClick={()=>setSystemOpen(false)} aria-label={t.buttons.close}><Icon name="close"/></IconButton></header><div className="connection-content"><div className="setting-row"><span>{t.status.server}</span><strong>{server==="connected"?t.status.connected:server==="disconnected"?t.status.disconnected:t.status.checking}</strong></div><div className="setting-row"><span>{t.status.vault}</span><strong>{vaultLabel}</strong></div><div className="setting-row"><span>AI</span><strong>{aiLabel}</strong></div><p className="path-text">{settings?.ai.chat_model === "auto" ? ai?.selected_model ?? ai?.qwen_model ?? tr("自动选择模型","Automatic model") : settings?.ai.chat_model}</p>{ai?.message&&<p className="settings-tip">{errorText({code:ai.error_code,message:ai.message})}</p>}<div className="settings-actions"><Button onClick={()=>void refresh()}><Icon name="refresh" size={15}/>{t.status.refreshStatus}</Button><Button onClick={()=>{setSystemOpen(false);setTool("history");setActivityTab("scheduler");}}>{tr("查看计划任务","View schedules")}</Button></div></div></Dialog>}
  </div>;
}
