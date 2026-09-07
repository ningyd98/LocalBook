import { useEffect, useRef } from "react";
import { Panel, Button } from "@localnote/ui";
import { PanelGroup, Panel as ResizePanel, PanelResizeHandle } from "react-resizable-panels";
import { createDebouncedSave, isEditableMarkdown, useWorkspaceStore } from "@localnote/workspace";
import { Sidebar } from "./Sidebar";
import { TabBar } from "./TabBar";
import { EditorPane } from "./EditorPane";
import { PreviewPane } from "./PreviewPane";
import { ConflictBanner } from "./ConflictBanner";
import { WorkspaceStatus } from "./WorkspaceStatus";
import { NotesLinksPanel } from "./NotesLinksPanel";

export function WorkspaceShell() {
  const tree = useWorkspaceStore((s) => s.tree), tabs = useWorkspaceStore((s) => s.tabs), active = useWorkspaceStore((s) => s.activePath), sessions = useWorkspaceStore((s) => s.sessions), theme = useWorkspaceStore((s) => s.theme), splitRatio = useWorkspaceStore((s) => s.splitRatio), relations = useWorkspaceStore((s) => s.relations);
  const loadTree = useWorkspaceStore((s) => s.loadTree), toggle = useWorkspaceStore((s) => s.toggleDirectory), open = useWorkspaceStore((s) => s.openFile), activate = useWorkspaceStore((s) => s.activateTab), close = useWorkspaceStore((s) => s.closeTab), update = useWorkspaceStore((s) => s.updateContent), save = useWorkspaceStore((s) => s.save), reload = useWorkspaceStore((s) => s.reloadConflict), keep = useWorkspaceStore((s) => s.keepLocal), setTheme = useWorkspaceStore((s) => s.setTheme), setSplitRatio = useWorkspaceStore((s) => s.setSplitRatio), loadRelations = useWorkspaceStore((s) => s.loadRelations), clearRelations = useWorkspaceStore((s) => s.clearRelations);
  const session = active ? sessions[active] : undefined;
  const debouncedSave = useRef<ReturnType<typeof createDebouncedSave> | null>(null);
  useEffect(() => { void loadTree(); }, [loadTree]);
  // M3: fetch outgoing + backlinks for the active markdown note.
  useEffect(() => {
    if (active && isEditableMarkdown(active)) void loadRelations(active);
    else clearRelations();
  }, [active, loadRelations, clearRelations]);
  useEffect(() => {
    debouncedSave.current?.cancel();
    debouncedSave.current = session ? createDebouncedSave((reason) => void save(session.path, reason)) : null;
    return () => { debouncedSave.current?.cancel(); debouncedSave.current = null; };
  }, [session?.path, save]);
  useEffect(() => {
    if (!session?.dirty || session.saveState === "conflict" || session.saveState === "saving" || session.saveState === "error") {
      debouncedSave.current?.cancel();
      return;
    }
    debouncedSave.current?.schedule("auto");
    return () => debouncedSave.current?.cancel();
  }, [session?.path, session?.content, session?.dirty, session?.saveState]);
  const saveNow = () => { debouncedSave.current?.cancel(); void save(undefined, "manual"); };
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
        if (event.defaultPrevented) return;
        event.preventDefault();
        saveNow();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  });
  return <div className={`workspace ${theme}`}>
    <Sidebar tree={tree} onRetry={() => void loadTree()} onToggle={toggle} onOpen={(path) => void open(path)} />
    <main className="workspace-main">
      <header className="workspace-header"><TabBar tabs={tabs} active={active} sessions={sessions} onActivate={activate} onRetry={(path) => void save(path, "manual")} onClose={(path) => close(path, () => window.confirm("Discard unsaved changes?"))} /><Button onClick={() => setTheme(theme === "light" ? "dark" : "light")}>{theme === "light" ? "Dark" : "Light"} theme</Button></header>
      {session?.conflict && <ConflictBanner onReload={() => void reload(session.path)} onKeepLocal={() => keep(session.path)} />}
      {session?.notice && <div role="status" className="workspace-notice">{session.notice}</div>}
      {session?.error && session.saveState === "error" && <div role="alert" className="workspace-error">{session.error.message}</div>}
      <NotesLinksPanel relations={relations} onOpen={(path) => void open(path)} onRetry={() => { if (active) void loadRelations(active); }} />
      {session ? <PanelGroup direction="horizontal" className="split" onLayout={(sizes) => { if (sizes[0] != null) setSplitRatio(sizes[0]); }}>
        <ResizePanel defaultSize={splitRatio} minSize={20}><Panel className="editor-panel"><EditorPane session={session} theme={theme} onChange={(value) => update(session.path, value)} onSave={() => void save(session.path, "manual")} /></Panel></ResizePanel>
        <PanelResizeHandle className="resize-handle" />
        <ResizePanel defaultSize={100 - splitRatio} minSize={20}><Panel className="preview-panel"><PreviewPane source={session.content} /></Panel></ResizePanel>
      </PanelGroup> : <WorkspaceStatus message="Open a Markdown file to start editing." />}
    </main>
  </div>;
}
