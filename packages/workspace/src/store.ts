import { create } from "zustand";
import type { WorkspaceApi, WorkspaceError, WorkspaceState, EditorSession } from "./types";

const encoder = new TextEncoder();
const toBase64 = (value: string) => {
  let binary = "";
  for (const byte of encoder.encode(value)) binary += String.fromCharCode(byte);
  return btoa(binary);
};
function fromBase64(value: string) {
  const binary = atob(value);
  const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
  const hasBom = bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf;
  const decoded = new TextDecoder("utf-8", { fatal: true }).decode(hasBom ? bytes.slice(3) : bytes);
  return hasBom ? `\ufeff${decoded}` : decoded;
}
function decodeFileContent(contentBase64: string, path: string): Pick<EditorSession, "content" | "encoding" | "error"> {
  try {
    return { content: fromBase64(contentBase64), encoding: "utf8", error: null };
  } catch {
    return { content: "", encoding: "invalid_utf8", error: { kind: "decode", code: "invalid_utf8", message: "File is not valid UTF-8 and is read-only", path } };
  }
}
function apiError(error: unknown, path?: string): WorkspaceError {
  const object = error && typeof error === "object" ? error as Record<string, unknown> : null;
  const metaObject = object && "meta" in object && object.meta && typeof object.meta === "object"
    ? object.meta as { prompt_version?: string; model?: string } : undefined;
  return {
    kind: object && "status" in object ? "api" : "network",
    status: object && "status" in object ? Number(object.status) : undefined,
    code: object && "code" in object ? String(object.code) : undefined,
    message: error instanceof Error ? error.message : "Request failed",
    path,
    meta: metaObject,
  };
}

let api: WorkspaceApi = {
  fetchVaultFiles: async () => ({ entries: [] }),
  fetchVaultFile: async () => { throw new Error("Workspace API is not configured"); },
  patchVaultFile: async () => { throw new Error("Workspace API is not configured"); },
  fetchLinks: async () => { throw new Error("Workspace API is not configured"); },
  fetchBacklinks: async () => { throw new Error("Workspace API is not configured"); },
  searchNotes: async () => { throw new Error("Workspace API is not configured"); },
  fetchGraph: async () => { throw new Error("Workspace API is not configured"); },
  fetchLocalGraph: async () => { throw new Error("Workspace API is not configured"); },
  fetchTagGraph: async () => { throw new Error("Workspace API is not configured"); },
};
/** M2 test doubles configure only the vault trio; merge keeps M3 defaults. */
export function configureWorkspaceApi(next: WorkspaceApi) { api = { ...api, ...next }; }

// M3 request/response race guards (PLAN-M3 §9.2 store matrix).
const relationVersions = new Map<string, number>();
let searchVersion = 0;
let graphVersion = 0;
let aiVersion = 0;
let historyVersion = 0;

// The controller owns one queue per path. A second save request is coalesced as
// pending rather than issuing a second PATCH with the same expected hash.
type SaveQueue = { running?: Promise<void>; pending: boolean; pendingReason: "auto" | "manual" };
const saveQueues = new Map<string, SaveQueue>();
const openVersions = new Map<string, number>();
const openRequests = new Map<string, Promise<void>>();
const queueFor = (path: string) => {
  let queue = saveQueues.get(path);
  if (!queue) { queue = { pending: false, pendingReason: "auto" }; saveQueues.set(path, queue); }
  return queue;
};

export const useWorkspaceStore = create<WorkspaceState>((set, get) => {
  const save = (path = get().activePath ?? undefined, reason: "auto" | "manual" = "auto"): Promise<void> => {
    if (!path) return Promise.resolve();
    const queue = queueFor(path);
    if (queue.running) {
      queue.pending = true;
      if (reason === "manual") queue.pendingReason = "manual";
      return queue.running;
    }
    const run = async () => {
      const initial = get().sessions[path];
      if (!initial || !initial.dirty || initial.encoding !== "utf8" || initial.saveState === "conflict" || (initial.saveState === "error" && reason !== "manual")) return;
      const sentVersion = initial.requestVersion;
      const sentContent = initial.content;
      const sentBase = initial.baseSha256;
      const sentBase64 = toBase64(sentContent);
      set((state) => {
        const current = state.sessions[path];
        if (!current || current.requestVersion !== sentVersion) return state;
        return { sessions: { ...state.sessions, [path]: { ...current, saveState: "saving", error: null } } };
      });
      try {
        const result = await api.patchVaultFile({ path, contentBase64: sentBase64, expectedSha256: sentBase });
        if (typeof result.sha256 !== "string") throw new Error("Invalid PATCH response");
        set((state) => {
          const latest = state.sessions[path];
          if (!latest) return state;
          const unchanged = latest.requestVersion === sentVersion && latest.content === sentContent;
          const next: EditorSession = {
            ...latest,
            baseSha256: result.sha256 ?? latest.baseSha256,
            baseContentBase64: sentBase64,
            byteLength: encoder.encode(sentContent).length,
            dirty: unchanged ? false : latest.dirty,
            saveState: unchanged ? "saved" : latest.saveState,
            error: unchanged ? null : latest.error,
          };
          return { sessions: { ...state.sessions, [path]: next }, tabs: state.tabs.map((tab) => tab.path === path ? { ...tab, dirty: next.dirty } : tab) };
        });
        if (get().sessions[path]?.requestVersion !== sentVersion) queue.pending = true;
      } catch (error) {
        const e = apiError(error, path);
        // A failed request must always unblock the latest session.  If edits
        // arrived while this request was in flight, retain those edits and
        // surface this failure against the latest request version rather than
        // silently leaving the session stuck in `saving`.
        set((state) => {
          const latest = state.sessions[path];
          if (!latest) return state;
          const conflict = e.code === "file_conflict";
          return { sessions: { ...state.sessions, [path]: { ...latest, saveState: conflict ? "conflict" : "error", error: e, conflict: conflict ? { code: "file_conflict", path, message: e.message, baseSha256: sentBase, detectedAt: new Date().toISOString() } : null } } };
        });
        // Do not automatically replay a newer edit after a failure.  Recovery
        // is explicitly gated on the user's Retry action.
        queue.pending = false;
        queue.pendingReason = "auto";
      }
    };
    const running = run();
    queue.running = running;
    running.then(() => {
      queue.running = undefined;
      const latest = get().sessions[path];
      if (queue.pending) {
        queue.pending = false;
        if (latest?.dirty && latest.saveState !== "conflict" && latest.saveState !== "error" && latest.encoding === "utf8") void save(path, queue.pendingReason);
        queue.pendingReason = "auto";
      }
    }, () => { queue.running = undefined; });
    return running;
  };

  return {
    tree: { entries: [], expandedPaths: [], status: "idle", error: null }, tabs: [], activePath: null, sessions: {},
    relations: { status: "idle", path: null, outgoing: null, backlinks: null, brokenCount: 0, error: null },
    search: { status: "idle", query: "", response: null, error: null },
    graph: { status: "idle", scope: "global", note: null, depth: 1, direction: "both", tag: null, includeBroken: true, limit: 500, offset: 0, response: null, error: null, requestVersion: 0 }, ai: { status: "idle", action: null, notePath: null, response: null, error: null, requestVersion: 0 },
    theme: "light", splitRatio: 50,
    history: { status: "idle", page: null, selected: null, error: null, requestVersion: 0, busy: false },
    loadHistory: async () => {
      const version = ++historyVersion;
      set((s) => ({ history: { ...s.history, status: "loading", error: null, requestVersion: version } }));
      if (!api.listHistory) { set((s) => ({ history: { ...s.history, status: "error", error: { kind: "network", message: "History API is not configured" } } })); return; }
      try { const page = await api.listHistory(); if (historyVersion === version) set((s) => ({ history: { ...s.history, status: "ready", page, error: null } })); }
      catch (error) { if (historyVersion === version) set((s) => ({ history: { ...s.history, status: "error", error: apiError(error) } })); }
    },
    selectHistory: async (id) => {
      if (!api.getHistory) return;
      set((s) => ({ history: { ...s.history, busy: true, error: null } }));
      try { const selected = await api.getHistory(id); set((s) => ({ history: { ...s.history, selected, busy: false } })); }
      catch (error) { set((s) => ({ history: { ...s.history, busy: false, error: apiError(error) } })); }
    },
    createJob: async (request) => { if (!api.createJob) return null; try { const job = await api.createJob(request); await get().loadHistory(); return job; } catch (error) { set((s) => ({ history: { ...s.history, error: apiError(error) } })); return null; } },
    acceptJob: async (id, request = { confirm: true }) => { if (!api.acceptJob) return null; try { const job = await api.acceptJob(id, request); await get().loadHistory(); await get().selectHistory(id); return job; } catch (error) { set((s) => ({ history: { ...s.history, error: apiError(error) } })); return null; } },
    rejectJob: async (id) => { if (!api.rejectJob) return null; try { const job = await api.rejectJob(id); await get().loadHistory(); await get().selectHistory(id); return job; } catch (error) { set((s) => ({ history: { ...s.history, error: apiError(error) } })); return null; } },
    undoHistory: async (id) => { if (!api.undoHistory) return null; try { const result = await api.undoHistory(id); await get().loadHistory(); await get().selectHistory(id); return result; } catch (error) { set((s) => ({ history: { ...s.history, error: apiError(error) } })); return null; } },
    loadTree: async () => {
      set((s) => ({ tree: { ...s.tree, status: "loading", error: null } }));
      try {
        const result = await api.fetchVaultFiles({ recursive: true });
        set((s) => ({ tree: { ...s.tree, entries: result.entries.filter((e) => !e.path.split("/").some((part) => part.startsWith("."))).sort((a, b) => a.path.localeCompare(b.path)), status: "ready" } }));
      } catch (error) {
        const e = apiError(error);
        set((s) => ({ tree: { ...s.tree, status: e.code === "vault_not_configured" ? "not_configured" : e.status === 503 ? "unavailable" : "error", error: e } }));
      }
    },
    toggleDirectory: (path) => set((s) => ({ tree: { ...s.tree, expandedPaths: s.tree.expandedPaths.includes(path) ? s.tree.expandedPaths.filter((v) => v !== path) : [...s.tree.expandedPaths, path] } })),
    openFile: async (path) => {
      const existing = get().sessions[path];
      if (existing) { set({ activePath: path }); return; }
      const prior = openRequests.get(path);
      if (prior) { set({ activePath: path }); return prior; }
      const version = (openVersions.get(path) ?? 0) + 1;
      openVersions.set(path, version);
      set((s) => ({ tabs: s.tabs.some((t) => t.path === path) ? s.tabs : [...s.tabs, { path, title: path.split("/").at(-1) ?? path, dirty: false, loading: true, error: null }], activePath: path }));
      const request = (async () => {
        try {
          const result = await api.fetchVaultFile(path);
          const { content, encoding, error: decodeError } = decodeFileContent(result.content_base64, path);
          if (openVersions.get(path) !== version) return;
          const session: EditorSession = { path, content, baseSha256: result.sha256, baseContentBase64: result.content_base64, byteLength: result.byte_length, encoding, dirty: false, saveState: "saved", error: decodeError, conflict: null, notice: null, requestVersion: version };
          set((s) => ({ sessions: { ...s.sessions, [path]: session }, tabs: s.tabs.map((tab) => tab.path === path ? { ...tab, loading: false, error: decodeError } : tab) }));
        } catch (error) {
          if (openVersions.get(path) !== version) return;
          const e = apiError(error, path);
          set((s) => ({ tabs: s.tabs.map((tab) => tab.path === path ? { ...tab, loading: false, error: e } : tab) }));
        } finally { openRequests.delete(path); }
      })();
      openRequests.set(path, request);
      return request;
    },
    activateTab: (path) => set({ activePath: path }),
    closeTab: (path, confirm = () => true) => {
      const tab = get().tabs.find((v) => v.path === path);
      if (tab?.dirty && !confirm()) return false;
      set((s) => { const tabs = s.tabs.filter((v) => v.path !== path); const index = s.tabs.findIndex((v) => v.path === path); const activePath = s.activePath === path ? (tabs[Math.min(index, tabs.length - 1)]?.path ?? null) : s.activePath; const sessions = { ...s.sessions }; delete sessions[path]; return { tabs, activePath, sessions }; });
      return true;
    },
    updateContent: (path, content) => set((s) => {
      const session = s.sessions[path];
      if (!session || session.encoding !== "utf8") return s;
      // No-op on content echo (e.g. the editor mirroring an externally applied
      // value back at us): nothing changed, so do not bump requestVersion or
      // dirty — otherwise an in-flight save whose snapshot matches this content
      // would be discarded as stale and re-PATCHed forever.
      if (content === session.content) return s;
      const dirty = true;
      const next: EditorSession = { ...session, content, dirty, requestVersion: session.requestVersion + 1, saveState: session.saveState === "conflict" ? "conflict" : session.saveState, error: null, notice: null };
      return { sessions: { ...s.sessions, [path]: next }, tabs: s.tabs.map((tab) => tab.path === path ? { ...tab, dirty } : tab) };
    }),
    save,
    reloadConflict: async (path) => {
      const session = get().sessions[path]; if (!session) return;
      const version = (openVersions.get(path) ?? session.requestVersion) + 1; openVersions.set(path, version);
      try {
        const result = await api.fetchVaultFile(path);
        const { content, encoding, error: decodeError } = decodeFileContent(result.content_base64, path);
        if (openVersions.get(path) !== version) return;
        set((s) => ({ sessions: { ...s.sessions, [path]: { ...s.sessions[path], content, baseSha256: result.sha256, baseContentBase64: result.content_base64, byteLength: result.byte_length, dirty: false, saveState: "saved", conflict: null, error: decodeError, notice: null, encoding, requestVersion: version } }, tabs: s.tabs.map((tab) => tab.path === path ? { ...tab, dirty: false, error: decodeError } : tab) }));
      } catch (error) {
        if (openVersions.get(path) !== version) return;
        set((s) => ({ sessions: { ...s.sessions, [path]: { ...s.sessions[path], error: apiError(error, path), saveState: "error", conflict: null, notice: null } } }));
      }
    },
    keepLocal: (path) => set((s) => s.sessions[path] ? { sessions: { ...s.sessions, [path]: { ...s.sessions[path], saveState: "conflict", conflict: null, notice: "Kept local changes - server not overwritten" } } } : s),
    loadRelations: async (path) => {
      const version = (relationVersions.get(path) ?? 0) + 1;
      relationVersions.set(path, version);
      set((s) => ({ relations: { ...s.relations, status: "loading", path, error: null } }));
      const linksFn = api.fetchLinks;
      const backlinksFn = api.fetchBacklinks;
      if (!linksFn || !backlinksFn) {
        set((s) => ({ relations: { ...s.relations, status: "error", path, error: { kind: "network", message: "Workspace API is not configured", path } } }));
        return;
      }
      try {
        const [links, backlinks] = await Promise.all([linksFn(path), backlinksFn(path)]);
        if (relationVersions.get(path) !== version) return;
        set({ relations: { status: "ready", path, outgoing: links.outgoing, backlinks: backlinks.backlinks, brokenCount: links.broken_count, error: null } });
      } catch (error) {
        if (relationVersions.get(path) !== version) return;
        const e = apiError(error, path);
        set((s) => ({ relations: { ...s.relations, status: "error", path, error: e } }));
      }
    },
    clearRelations: () => set({ relations: { status: "idle", path: null, outgoing: null, backlinks: null, brokenCount: 0, error: null } }),
    runSearch: async (query) => {
      const trimmed = query.trim();
      if (!trimmed) { set({ search: { status: "idle", query: "", response: null, error: null } }); return; }
      const version = searchVersion + 1;
      searchVersion = version;
      set({ search: { status: "loading", query: trimmed, response: null, error: null } });
      if (!api.searchNotes) {
        set({ search: { status: "error", query: trimmed, response: null, error: { kind: "network", message: "Workspace API is not configured" } } });
        return;
      }
      try {
        const response = await api.searchNotes(trimmed);
        if (searchVersion !== version) return;
        set({ search: { status: "ready", query: trimmed, response, error: null } });
      } catch (error) {
        if (searchVersion !== version) return;
        const e = apiError(error);
        set({ search: { status: "error", query: trimmed, response: null, error: e } });
      }
    },
    clearSearch: () => set({ search: { status: "idle", query: "", response: null, error: null } }),
    loadGraph: async (overrides) => {
      const current = get().graph;
      // A semantic change (scope/pivot/filters/page size) restarts pagination
      // at offset 0 unless the caller explicitly requested an offset (load-more).
      const changed = (key: "scope" | "note" | "tag" | "direction" | "depth" | "includeBroken" | "limit") =>
        overrides?.[key] !== undefined && overrides[key] !== current[key];
      const semanticKeys = ["scope", "note", "tag", "direction", "depth", "includeBroken", "limit"] as const;
      const anySemanticChange = semanticKeys.some(changed);
      const controls = {
        scope: overrides?.scope ?? current.scope,
        note: overrides?.note !== undefined ? overrides.note : current.note,
        depth: overrides?.depth ?? current.depth,
        direction: overrides?.direction ?? current.direction,
        tag: overrides?.tag !== undefined ? overrides.tag : current.tag,
        includeBroken: overrides?.includeBroken ?? current.includeBroken,
        limit: overrides?.limit ?? current.limit,
        offset: overrides?.offset ?? (anySemanticChange ? 0 : current.offset),
      };
      const version = ++graphVersion;
      const markError = (message: string) => {
        if (graphVersion !== version) return;
        set({ graph: { ...controls, status: "error", response: null, error: { kind: "api", message }, requestVersion: version } });
      };
      if (!api.fetchGraph || !api.fetchLocalGraph || !api.fetchTagGraph) {
        markError("Graph API is not configured");
        return;
      }
      set((s) => ({ graph: { ...s.graph, ...controls, status: "loading", response: null, error: null, requestVersion: version } }));
      const query = { limit: controls.limit, offset: controls.offset, include_broken: controls.includeBroken };
      try {
        let response;
        if (controls.scope === "local") {
          const note = controls.note ?? get().activePath;
          if (!note) { markError("Open a note first to explore its local graph."); return; }
          response = await api.fetchLocalGraph(note, { ...query, depth: controls.depth, direction: controls.direction, tag: controls.tag || undefined });
        } else if (controls.scope === "tag") {
          if (!controls.tag) { markError("Enter a tag to open the tag graph."); return; }
          response = await api.fetchTagGraph(controls.tag, query);
        } else {
          response = await api.fetchGraph({ ...query, tag: controls.tag || undefined });
        }
        if (graphVersion !== version) return;
        set({ graph: { ...controls, status: response.page.total_nodes === 0 ? "empty" : "ready", response, error: null, requestVersion: version } });
      } catch (error) {
        if (graphVersion !== version) return;
        const e = apiError(error);
        set((s) => ({ graph: { ...s.graph, status: e.code === "index_unavailable" || e.status === 503 ? "unavailable" : "error", response: null, error: e, requestVersion: version } }));
      }
    },
    clearGraph: () => set({ graph: { status: "idle", scope: "global", note: null, depth: 1, direction: "both", tag: null, includeBroken: true, limit: 500, offset: 0, response: null, error: null, requestVersion: 0 } }),
    runAI: async (action, args) => {
      const version = ++aiVersion;
      const path = typeof args.note_path === "string" ? args.note_path : get().activePath;
      set({ ai: { status: "loading", action, notePath: path ?? null, response: null, error: null, requestVersion: version } });
      const fn = action === "ask" ? api.aiChat : action === "summarize" ? api.aiSummarize : action === "tags" ? api.aiTags : action === "related" ? api.aiRelated : action === "extract_todos" ? api.aiExtractTodos : api.aiClassify;
      if (!fn) { set({ ai: { status: "error", action, notePath: path ?? null, response: null, error: { kind: "network", message: "AI API is not configured", path: path ?? undefined }, requestVersion: version } }); return; }
      try { const response = await fn(args); if (aiVersion === version) set({ ai: { status: "ready", action, notePath: path ?? null, response, error: null, requestVersion: version } }); }
      catch (error) { if (aiVersion === version) set({ ai: { status: error && typeof error === "object" && "status" in error && Number((error as {status:number}).status) === 503 ? "offline" : "error", action, notePath: path ?? null, response: null, error: apiError(error, path ?? undefined), requestVersion: version } }); }
    },
    clearAI: () => { ++aiVersion; set({ ai: { status: "idle", action: null, notePath: null, response: null, error: null, requestVersion: aiVersion } }); },
    setTheme: (theme) => set({ theme }),
    setSplitRatio: (splitRatio) => set({ splitRatio: Math.max(20, Math.min(80, splitRatio)) }),
  };
});
export { toBase64, fromBase64 };
