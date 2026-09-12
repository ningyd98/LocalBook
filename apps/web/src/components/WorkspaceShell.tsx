import { useEffect, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { Button, Icon, IconButton } from "@localnote/ui";
import { createDebouncedSave, fontScaleVars, fontStacks, isEditableMarkdown, useWorkspaceStore } from "@localnote/workspace";
import { vaultResourceUrl } from "../api/client";
import { noteAncestorPaths, resolveVaultRelativePath } from "@localnote/protocol";
import { AttachmentPreview } from "./AttachmentPreview";
import { ContextMenu } from "./ContextMenu";
import type { ContextMenuItem } from "./ContextMenu";
import { Sidebar } from "./Sidebar";
import { TabBar } from "./TabBar";
import { EditorPane } from "./EditorPane";
import { PreviewPane } from "./PreviewPane";
import { ConflictBanner } from "./ConflictBanner";
import { ConfirmDialog } from "./ConfirmDialog";
import { NewNoteDialog } from "./NewNoteDialog";
import { NewFolderDialog } from "./NewFolderDialog";
import { TrashPanel } from "./TrashPanel";
import { useSyncedScroll } from "./useSyncedScroll";
import { PaneResize } from "./PaneResize";
import { useI18n } from "../i18n";

export function WorkspaceShell({ leftView, centerView, inspector, onToggleInspector, onShowNotes, onOpenSettings, onOpenSearch, onOpenGraph, vaultName = "LocalNote", apiReady = true, sidebarRequest = 0 }: {leftView?: ReactNode; centerView?: ReactNode; inspector?: ReactNode; onToggleInspector?: () => void; onShowNotes?: () => void; onOpenSettings?: () => void; onOpenSearch?: () => void; onOpenGraph?: () => void; vaultName?: string; apiReady?: boolean; sidebarRequest?: number}) {
  const { t, tr, errorText } = useI18n();
  const s = useWorkspaceStore(); const session = s.activePath ? s.sessions[s.activePath] : undefined;
  const [closing, setClosing] = useState<string | null>(null);
  const [newNoteOpen, setNewNoteOpen] = useState(false);
  const [newFolderOpen, setNewFolderOpen] = useState(false);
  const [attachmentPath, setAttachmentPath] = useState<string | null>(null);
  const [wikilinkError, setWikilinkError] = useState<string | null>(null);
  const [treeError, setTreeError] = useState<string | null>(null);
  const [menu, setMenu] = useState<{ path: string | null; x: number; y: number } | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [trashOpen, setTrashOpen] = useState(false);
  const [mobile, setMobile] = useState(() => typeof matchMedia === "function" && matchMedia("(max-width: 759px)").matches);
  const [mobileOpen, setMobileOpen] = useState(false);
  const timers = useRef(new Map<string, ReturnType<typeof createDebouncedSave>>());
  // Split-view scroll anchors: the editor pane (CodeMirror lives inside it) and
  // the preview pane. The hook resolves the real scroll boxes itself.
  const editorPaneRef = useRef<HTMLDivElement>(null);
  const previewPaneRef = useRef<HTMLElement>(null);
  const scheduled = useRef(new Map<string, string>());
  const delay = useRef(s.autoSaveDelay);
  const leftOpen = mobile ? mobileOpen : s.sidebarOpen;
  const toggleLeft = () => mobile ? setMobileOpen(v => !v) : s.setPreferences({sidebarOpen: !s.sidebarOpen});
  useEffect(() => { const mq = window.matchMedia?.("(max-width: 759px)"); if (!mq) return; const fn = () => {setMobile(mq.matches); setMobileOpen(false);}; mq.addEventListener("change", fn); return () => mq.removeEventListener("change", fn); }, []);
  useEffect(() => { if (sidebarRequest) setMobileOpen(true); }, [sidebarRequest]);
  useEffect(() => { if (s.activePath) setMobileOpen(false); }, [s.activePath]);
  useEffect(() => { document.documentElement.dataset.theme = s.theme; }, [s.theme]);
  /**
   * Font preferences are document-level: the scale tokens resize every text
   * size in the stylesheet (chrome, panels, preview and editor alike) and the
   * stacks switch the family, either everywhere or only in note surfaces.
   */
  useEffect(() => {
    const root = document.documentElement;
    for (const [name, value] of Object.entries(fontScaleVars(s.fontScale))) root.style.setProperty(name, value);
    // A note-only family keeps the chrome sans and gives note surfaces the
    // chosen stack; "all" applies it to the chrome as well.
    root.style.setProperty("--font-ui", s.fontTarget === "note" ? fontStacks.sans : fontStacks[s.fontFamily]);
    root.style.setProperty("--font-note", fontStacks[s.fontFamily]);
    root.dataset.fontTarget = s.fontTarget;
  }, [s.fontScale, s.fontFamily, s.fontTarget]);
  useEffect(() => { if (apiReady) void s.loadTree(); }, [apiReady, s.loadTree]);
  /**
   * Split view scrolls as one document. The refs above hold the panes of the
   * *active* note; the reset key re-binds when that note or the layout changes.
   */
  useSyncedScroll(editorPaneRef, previewPaneRef, s.editorMode === "split" && s.syncScroll, `${s.activePath ?? ""}|${s.editorMode}`);
  useEffect(() => { if (s.activePath && isEditableMarkdown(s.activePath) && !s.vaultStale) void s.loadRelations(s.activePath); else s.clearRelations(); }, [s.activePath, s.vaultStale, s.loadRelations, s.clearRelations]);
  useEffect(() => {
    if (delay.current !== s.autoSaveDelay) { for (const timer of timers.current.values()) timer.cancel(); timers.current.clear(); scheduled.current.clear(); delay.current = s.autoSaveDelay; }
    for (const [path, timer] of timers.current) if (!s.sessions[path]) {timer.cancel(); timers.current.delete(path); scheduled.current.delete(path);}
    for (const [path, doc] of Object.entries(s.sessions)) {
      let timer = timers.current.get(path);
      if (!timer) {timer = createDebouncedSave(reason => void useWorkspaceStore.getState().save(path, reason), s.autoSaveDelay); timers.current.set(path, timer);}
      if (!s.autoSave || s.workspaceFrozen || s.vaultStale || !doc.dirty || ["conflict", "saving", "error"].includes(doc.saveState)) {timer.cancel(); scheduled.current.delete(path);}
      else if (scheduled.current.get(path) !== doc.content) {timer.schedule("auto"); scheduled.current.set(path, doc.content);}
    }
  }, [s.sessions, s.autoSave, s.autoSaveDelay, s.workspaceFrozen, s.vaultStale]);
  useEffect(() => () => {for (const timer of timers.current.values()) timer.cancel(); timers.current.clear();}, []);
  useEffect(() => {
    const key = (event: KeyboardEvent) => { if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s" && !event.defaultPrevented) {event.preventDefault(); const current = useWorkspaceStore.getState(); if (current.activePath) timers.current.get(current.activePath)?.cancel(); void current.save(undefined, "manual");} if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "n" && !event.defaultPrevented && apiReady) {event.preventDefault(); setNewNoteOpen(true);} };
    const unload = (event: BeforeUnloadEvent) => { if (Object.values(useWorkspaceStore.getState().sessions).some(doc => doc.dirty)) {event.preventDefault(); event.returnValue = "";} };
    window.addEventListener("keydown", key); window.addEventListener("beforeunload", unload);
    return () => {window.removeEventListener("keydown", key); window.removeEventListener("beforeunload", unload);};
  }, [apiReady]);
  useEffect(() => {
    if (inspector) setMobileOpen(false);
    const escape = (event: KeyboardEvent) => {if(event.key === "Escape" && !event.defaultPrevented){if(mobileOpen)setMobileOpen(false);else if(inspector)onToggleInspector?.();}};
    window.addEventListener("keydown", escape);return()=>window.removeEventListener("keydown",escape);
  }, [!!inspector, mobileOpen, onToggleInspector]);
  const open = (path: string) => {void s.openFile(path); onShowNotes?.(); setMobileOpen(false);};
  const activate = (path: string) => {if (s.activePath && path !== s.activePath && s.autoSave && !s.workspaceFrozen && !s.vaultStale) timers.current.get(s.activePath)?.flush(); s.activateTab(path); onShowNotes?.();};
  const close = (path: string) => {if (s.sessions[path]?.dirty) setClosing(path); else s.closeTab(path);};
  /**
   * Upload attachment(s) and insert a reference into the current note.
   * `targetDirectory` is only supplied by the tree context menu; every other
   * entry point passes `undefined` so the store derives the current note's
   * directory ("" for a root note) itself.
   */
  const uploadFiles = async (files: File[], notePath: string | undefined, targetDirectory: string | undefined, source: "toolbar" | "editor-drop" | "preview-drop" | "paste" | "tree-context") => {
    for (const file of files) {
      await s.uploadAndInsertAttachment(file, { notePath, targetDirectory, source });
    }
  };
  /** Obsidian rule: a wikilink resolves by basename anywhere in the Vault. */
  const noteStems = new Set(s.tree.entries.filter(entry => entry.kind === "file").map(entry => {
    const name = entry.path.split("/").at(-1) ?? entry.path;
    return name.replace(/\.(md|markdown)$/i, "").toLowerCase();
  }));
  const wikilinkExists = (target: string) => noteStems.has((target.split("/").at(-1) ?? target).replace(/\.(md|markdown)$/i, "").toLowerCase());
  const openWikilink = (target: string) => {
    setWikilinkError(null);
    void s.openOrCreateLinkedNote(s.activePath, target).catch(error => {
      setWikilinkError(errorText(error));
    });
  };
  const pickAndUpload = (directory: string) => {
    const input = document.createElement("input");
    input.type = "file";
    input.multiple = true;
    input.onchange = () => {
      const files = Array.from(input.files ?? []);
      if (files.length) void uploadFiles(files, s.activePath ?? undefined, directory, "tree-context");
    };
    input.click();
  };
  /**
   * Nested-document creation. A new document is written into the child
   * directory of the document it belongs to (`notes/A.md` → `notes/A/…`), so
   * the hierarchy is plain folders on disk and the tree can collapse one level
   * per document.
   */
  const createChild = (path: string) => {
    setTreeError(null);
    void s.createChildNote(path, { name: tr("未命名文档", "Untitled document") }).catch(error => setTreeError(errorText(error)));
  };
  const createSibling = (path: string) => {
    setTreeError(null);
    void s.createSiblingNote(path, { name: tr("未命名文档", "Untitled document") }).catch(error => setTreeError(errorText(error)));
  };
  /**
   * Deleting moves the item to the recycle bin, so the dialog explains what the
   * user can still get back: the retention window, every affected tab (a draft
   * inside means unsaved work), and for a folder, that it goes as a whole.
   */
  const confirmDelete = async () => {
    const target = deleting;
    if (!target) return;
    setDeleteBusy(true);
    try {
      await s.moveToTrash(target);
      setDeleting(null);
      setTreeError(null);
      if (trashOpen) void s.loadTrash();
    } catch (error) {
      setDeleting(null);
      setTreeError(errorText(error));
    } finally {
      setDeleteBusy(false);
    }
  };
  const deleteSummary = (path: string) => {
    const isFolder = s.tree.entries.some(entry => entry.path === path && entry.kind === "directory");
    const days = s.trash.retentionDays;
    const inside = s.tree.entries.filter(entry => entry.path.startsWith(`${path}/`)).length;
    const parts = [isFolder
      ? tr(`文件夹「${path}」连同里面 ${inside} 个文件一起移到回收站，${days} 天内可以恢复。`, `The folder “${path}” and its ${inside} file(s) move to the recycle bin; they can be restored for ${days} days.`)
      : tr(`「${path}」会移到回收站，${days} 天内可以恢复。`, `“${path}” moves to the recycle bin and can be restored for ${days} days.`)];
    const openInside = s.tabs.filter(tab => tab.path === path || tab.path.startsWith(`${path}/`));
    if (s.sessions[path]?.dirty) parts.push(tr("这个文件有未保存的改动，先保存再删除。", "This file has unsaved changes; save it before deleting."));
    if (openInside.length > 1 || (isFolder && openInside.length)) parts.push(tr(`会同时关闭 ${openInside.length} 个已打开的标签。`, `${openInside.length} open tab(s) will be closed as well.`));
    return parts.join(" ");
  };

  /** Right-click inside a note (toolbar, tab, editor, preview). */
  const openDocumentMenu = (path: string, x: number, y: number) => setMenu({ path, x, y });
  const documentMenuItems = (path: string): ContextMenuItem[] => [
    { id: "child", label: tr("新建子文档", "New nested document"), icon: "note", onSelect: () => createChild(path) },
    { id: "sibling", label: tr("新建同级文档", "New document at this level"), icon: "note", onSelect: () => createSibling(path) },
  ];
  const ancestors = session ? noteAncestorPaths(session.path, new Set(s.tree.entries.filter(entry => entry.kind === "file").map(entry => entry.path))) : [];
  return <div className={`workspace ${s.theme}`} style={{"--sidebar-width": `${s.sidebarWidth}px`, "--inspector-width": `${s.inspectorWidth}px`} as CSSProperties}>
    {leftOpen && <><aside className="sidebar"><div className="vault-heading"><span className="vault-avatar">L</span><div><strong>{vaultName}</strong><small>{tr("本地笔记库", "LOCAL WORKSPACE")}</small></div><IconButton aria-label={tr("收起侧栏", "Collapse sidebar")} onClick={toggleLeft}><Icon name="panelLeft" size={16}/></IconButton></div>{leftView ?? <Sidebar tree={s.tree} onRetry={() => void s.loadTree()} onToggle={s.toggleDirectory} onOpen={open} onNewNote={apiReady ? () => setNewNoteOpen(true) : undefined} onNewFolder={apiReady ? () => setNewFolderOpen(true) : undefined} onMove={apiReady ? (source, destination) => void s.moveEntry(source, destination).catch(() => undefined) : undefined} onOpenAttachment={path => {setAttachmentPath(path); onShowNotes?.(); setMobileOpen(false);}} onUploadToDirectory={apiReady ? directory => pickAndUpload(directory) : undefined} onRename={apiReady ? (path, newName) => s.renameEntry(path, newName) : undefined} onNewChildNote={apiReady ? createChild : undefined} onNewSiblingNote={apiReady ? createSibling : undefined} onDelete={apiReady ? setDeleting : undefined} activeAttachmentPath={attachmentPath}/>}<TrashPanel open={trashOpen} onToggle={() => setTrashOpen(v => !v)} onOpen={open}/>
      <div className="sidebar-footer"><span className={`status-dot ${s.tree.status === "ready" ? "ok" : "warn"}`}/>{s.tree.status === "ready" ? tr(`${s.tree.entries.filter(e => e.kind === "file").length} 个文件 · 保存在本机`, `${s.tree.entries.filter(e => e.kind === "file").length} files · Stored locally`) : tr("等待连接笔记库", "Awaiting a vault")}</div></aside><PaneResize value={s.sidebarWidth} onChange={v => s.setPreferences({sidebarWidth:v})} label={tr("调整文件侧栏宽度", "Resize file sidebar")}/>{mobile && <button className="drawer-scrim" aria-label={tr("关闭侧栏", "Close sidebar")} onClick={toggleLeft}/>}</>}
    <main className="workspace-main"><header className="workspace-header">{!leftOpen && <IconButton onClick={toggleLeft} aria-label={tr("显示文件侧栏", "Show file sidebar")} title={tr("显示文件侧栏", "Show file sidebar")}><Icon name="panelLeft" size={17}/></IconButton>}<TabBar tabs={s.tabs} active={s.activePath} sessions={s.sessions} onActivate={activate} onClose={close} onRetry={path => void s.save(path, "manual")} onContextMenu={apiReady ? openDocumentMenu : undefined}/>{!s.tabs.length && <span className="workspace-caption">{tr("工作空间", "Workspace")}</span>}<IconButton className="inspector-toggle" onClick={onToggleInspector} aria-label={tr("切换辅助面板", "Toggle inspector")} aria-pressed={!!inspector} title={tr("笔记关联与 AI 助手", "Note links & AI assistant")}><Icon name="panelRight" size={17}/></IconButton></header>
      {wikilinkError && <div role="alert" className="workspace-error workspace-error-soft"><strong>{tr("无法创建或打开该链接", "Cannot create or open that link")}</strong><span>{wikilinkError}</span><Button onClick={() => setWikilinkError(null)}>{tr("关闭", "Dismiss")}</Button></div>}
      {s.vaultStale && <div role="alert" className="workspace-error"><strong>{tr("笔记库已在其他页面切换", "Vault changed in another page")}</strong><span>{tr("当前草稿仍保留，自动保存已暂停。请在设置中切回原库后处理草稿。", "Your draft is preserved and saving is paused. Switch back to the original vault in Settings to recover it.")}</span><Button onClick={onOpenSettings}>{t.settings.title}</Button></div>}
      {s.attachment.error && <div role="alert" className="workspace-error attachment-error">{errorText(s.attachment.error)}<Button onClick={s.clearAttachmentError}>{t.tabs.close}</Button></div>}
      {attachmentPath && <AttachmentPreview path={attachmentPath} resolveResourceUrl={vaultResourceUrl} onClose={() => setAttachmentPath(null)}/>}
      {treeError && <div role="alert" className="workspace-error workspace-error-soft"><strong>{tr("无法新建文档", "Could not create the document")}</strong><span>{treeError}</span><Button onClick={() => setTreeError(null)}>{tr("关闭", "Dismiss")}</Button></div>}
      {centerView && <div className="center-tool">{centerView}</div>}
      <div className="notes-view" hidden={!!centerView}>
        {session ? <><div className="document-toolbar" onContextMenu={apiReady ? event => { event.preventDefault(); openDocumentMenu(session.path, event.clientX, event.clientY); } : undefined}>
          <span className="document-path" title={session.path}><Icon name="note" size={14}/><span>{session.path.replace(/\.md$/i, "")}</span></span>
          {ancestors.length > 0 && <nav className="document-ancestors" aria-label={tr("上层文档", "Parent documents")}>
            {ancestors.map(parent => <button type="button" key={parent} title={tr(`打开上层文档 ${parent}`, `Open parent document ${parent}`)} onClick={() => open(parent)}><Icon name="chevron" size={10}/>{parent.replace(/\.md$/i, "").split("/").at(-1)}</button>)}
          </nav>}
          {s.editorMode === "split" && <button type="button" className="sync-scroll-toggle" aria-pressed={s.syncScroll} onClick={() => s.setPreferences({syncScroll: !s.syncScroll})} title={tr("编辑与预览同步滚动", "Scroll editor and preview together")}><Icon name="split" size={13}/><span>{tr("同步滚动", "Sync scroll")}</span></button>}
          <div className="view-switch" aria-label={tr("笔记视图", "Note view")}>{(["source", "live", "preview", "split"] as const).map(mode => <button type="button" key={mode} aria-pressed={s.editorMode === mode} onClick={() => s.setPreferences({editorMode:mode})} title={mode === "source" ? tr("编辑", "Edit") : mode === "live" ? tr("实时预览", "Live preview") : mode === "preview" ? tr("预览", "Preview") : tr("分屏", "Split")}><Icon name={mode === "source" ? "edit" : mode === "live" ? "preview" : mode} size={14}/><span>{mode === "source" ? tr("编辑", "Edit") : mode === "live" ? tr("实时", "Live") : mode === "preview" ? tr("预览", "Preview") : tr("分屏", "Split")}</span></button>)}</div></div>
          {session.conflict && <ConflictBanner onReload={() => void s.reloadConflict(session.path)} onKeepLocal={() => void s.keepLocal(session.path)}/>}
          {session.notice && <div role="status" className="workspace-notice">{tr("已保留本地内容；下次保存将覆盖磁盘版本。", session.notice)}</div>}
          {session.error && session.saveState === "error" && <div role="alert" className="workspace-error">{errorText(session.error)}<Button disabled={s.vaultStale} onClick={() => void s.save(session.path, "manual")}>{t.tabs.retry}</Button></div>}
        </> : <div className="welcome"><div className="welcome-symbol"><Icon name="files" size={35}/></div><p className="eyebrow">LOCALNOTE · YOUR THINKING SPACE</p><h1>{tr("给思考，一个安静的空间。", "A quiet space for your thoughts.")}</h1><p>{s.tree.status === "not_configured" ? tr("连接一个本地文件夹，让笔记、关联与灵感在这里汇聚。", "Connect a local folder to bring your notes, connections and ideas together.") : t.workspace.openFilePrompt}</p><div className="welcome-actions"><Button className="primary" onClick={s.tree.status === "not_configured" ? onOpenSettings : onOpenSearch}><Icon name={s.tree.status === "not_configured" ? "folder" : "search"} size={16}/>{s.tree.status === "not_configured" ? tr("连接笔记库", "Connect a vault") : tr("查找笔记", "Find a note")}</Button><Button onClick={onOpenGraph}><Icon name="graph" size={16}/>{tr("浏览知识图谱", "Explore graph")}</Button></div><div className="welcome-footnote"><span/><span>{tr("Markdown 文件 · 本地存储 · 自由连接", "Markdown files · Local storage · Connected ideas")}</span><span/></div></div>}
        {Object.values(s.sessions).map(doc => <div key={doc.path} hidden={doc.path !== s.activePath} className={`note-surface mode-${s.editorMode}`} style={{"--split-ratio": `${s.splitRatio}%`} as CSSProperties}><section className="editor-panel" hidden={s.editorMode === "preview"} ref={doc.path === s.activePath ? editorPaneRef : undefined}><EditorPane session={doc} theme={s.theme} readOnly={s.workspaceFrozen || s.vaultStale} onChange={value => s.updateContent(doc.path, value)} onSave={() => {timers.current.get(doc.path)?.cancel(); void s.save(doc.path, "manual");}} onUploadFiles={apiReady ? (files, source) => void uploadFiles(files, doc.path, undefined, source) : undefined} onRegisterCaretInsert={handler => s.registerCaretInsert(handler ? (path, markdown) => (path === doc.path ? handler(markdown) : false) : null)}
      livePreview={s.editorMode === "live" ? { notePath: doc.path, // A relative reference in the note resolves against the note's own
      // directory first (same rule as the preview pane); passing it to the
      // resource endpoint verbatim 404s for every note in a sub-folder.
      resolveResourceUrl: (path) => { const resolved = resolveVaultRelativePath(doc.path, path); return resolved ? vaultResourceUrl(resolved) : null; }, onOpenLink: (target, kind) => { if (kind === "wikilink") openWikilink(target); else void s.openFile(target).catch(() => undefined); }, onToggleTask: (index) => { s.toggleTask(doc.path, index); } } : null} attachmentBusy={s.attachment.busy} attachmentHint={tr("拖到此处或粘贴图片", "Drop here or paste an image")} onContextMenuAt={apiReady ? openDocumentMenu : undefined}/></section><div hidden={s.editorMode !== "split"} className="split-divider"><PaneResize value={s.splitRatio} onChange={s.setSplitRatio} percent label={tr("调整编辑与预览比例", "Resize editor and preview")}/></div><section className="preview-panel" hidden={s.editorMode === "source" || s.editorMode === "live"} ref={doc.path === s.activePath ? previewPaneRef : undefined}><PreviewPane source={doc.content} notePath={doc.path} resolveResourceUrl={vaultResourceUrl} wikilinkExists={wikilinkExists} onOpenWikilink={openWikilink} onToggleTask={index => s.toggleTask(doc.path, index)} attachmentBusy={s.attachment.busy} disabled={s.workspaceFrozen || s.vaultStale} onDropFiles={apiReady ? files => void uploadFiles(files, doc.path, undefined, "preview-drop") : undefined} onContextMenuAt={apiReady ? openDocumentMenu : undefined}/></section></div>)}
      </div>
    </main>
    {inspector && <><button className="inspector-scrim" aria-label={tr("关闭辅助面板", "Close inspector drawer")} onClick={onToggleInspector}/><PaneResize reverse value={s.inspectorWidth} onChange={v => s.setPreferences({inspectorWidth:v})} label={tr("调整辅助面板宽度", "Resize inspector")}/><aside className="inspector">{inspector}</aside></>}
    {menu && menu.path && <ContextMenu x={menu.x} y={menu.y} label={tr("文档操作", "Document actions")} items={documentMenuItems(menu.path)} onClose={() => setMenu(null)}/>}
    <ConfirmDialog open={deleting !== null} busy={deleteBusy} title={tr("移到回收站", "Move to recycle bin")} message={deleting ? deleteSummary(deleting) : ""} onCancel={() => setDeleting(null)} onConfirm={() => void confirmDelete()}/>
    <NewNoteDialog open={newNoteOpen} onClose={() => setNewNoteOpen(false)}/>
    <NewFolderDialog open={newFolderOpen} onClose={() => setNewFolderOpen(false)}/>
    <ConfirmDialog open={closing !== null} title={tr("关闭未保存的笔记", "Close unsaved note")} message={t.tabs.unsavedChanges} onCancel={() => setClosing(null)} onConfirm={() => {if (closing) s.closeTab(closing, () => true); setClosing(null);}}/>
  </div>;
}
