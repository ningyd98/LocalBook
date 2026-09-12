import { readPreferences, persistPreferences, validatePreferences } from "./preferences";
import { create } from "zustand";
import { noteChildDirectory, noteDirectory, noteStem, relativeMarkdownReference, suggestNoteName, toggleTaskInSource, wikilinkCreatePath } from "@localnote/protocol";
import { expandablePaths, foldedPaths } from "./hierarchy";
import { isEditableMarkdown } from "./selectors";
import { rewriteWikilinkTarget } from "./wikilinks";
import type { WorkspaceApi, WorkspaceError, WorkspaceState, EditorSession, CaretInsertHandler, CreateChildNoteOptions, FileTreeState } from "./types";

/** JSON/base64 upload channel split (mirrors server `ATTACHMENT_JSON_MAX_BYTES`). */
export const ATTACHMENT_JSON_MAX_BYTES = 10 * 1024 * 1024;

/**
 * Chunked binary → base64. `String.fromCharCode(...bytes)` / `btoa(...spread)`
 * would blow the call stack on a multi-megabyte file, so encode 32 KiB at a
 * time and concatenate the partial base64 strings.
 */
export function bytesToBase64Chunked(bytes: Uint8Array, chunkSize = 32 * 1024): string {
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    const slice = bytes.subarray(offset, offset + chunkSize);
    let part = "";
    for (let index = 0; index < slice.length; index += 1) part += String.fromCharCode(slice[index]!);
    binary += part;
  }
  return btoa(binary);
}

/** Read a File as base64 without ever spreading the whole byte array. */
export async function fileToBase64(file: File): Promise<string> {
  const buffer = await file.arrayBuffer();
  return bytesToBase64Chunked(new Uint8Array(buffer));
}

/** Deterministic display name for a clipboard screenshot. */
export function pastedImageName(mimeType: string, now = new Date()): string {
  const extension = (mimeType.split("/")[1] ?? "png").split("+")[0]!.replace(/[^a-z0-9]/gi, "") || "png";
  const stamp = now.toISOString().replace(/[-:]/g, "").replace(/\..+$/, "").replace("T", "-");
  return `pasted-image-${stamp}.${extension}`;
}

/** True when the attachment should be embedded as an image. */
export function isImageAttachment(name: string, contentType?: string | null): boolean {
  if (contentType && contentType.toLowerCase().startsWith("image/")) return true;
  return /\.(png|jpe?g|gif|webp|avif|bmp|svg|ico)$/i.test(name);
}

const encoder = new TextEncoder();
const toBase64 = (value: string, lineSeparator: "CRLF" | "LF" | "CR" = "LF", hasBOM: boolean = false) => {
  // Restore original line separators
  let content = value;
  if (lineSeparator === "CRLF") {
    content = value.replace(/\n/g, '\r\n');
  } else if (lineSeparator === "CR") {
    content = value.replace(/\n/g, '\r');
  }
  // Add BOM if original file had it
  if (hasBOM) {
    content = '\ufeff' + content;
  }
  let binary = "";
  for (const byte of encoder.encode(content)) binary += String.fromCharCode(byte);
  return btoa(binary);
};
function detectLineSeparator(content: string): "CRLF" | "LF" | "CR" {
  if (content.includes('\r\n')) return 'CRLF';
  if (content.includes('\r')) return 'CR';
  return 'LF';
}
function fromBase64(value: string) {
  const binary = atob(value);
  const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
  const hasBom = bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf;
  const decoded = new TextDecoder("utf-8", { fatal: true }).decode(hasBom ? bytes.slice(3) : bytes);
  return hasBom ? `\ufeff${decoded}` : decoded;
}
function decodeFileContent(contentBase64: string, path: string): Pick<EditorSession, "content" | "encoding" | "lineSeparator" | "hasBOM" | "error"> {
  try {
    const content = fromBase64(contentBase64);
    const hasBOM = content.startsWith('\ufeff');
    const cleanContent = hasBOM ? content.slice(1) : content;
    const lineSeparator = detectLineSeparator(cleanContent);
    // Normalize to LF for editor (CodeMirror always uses LF internally)
    const normalized = cleanContent.replace(/\r\n|\r/g, '\n');
    return { content: normalized, encoding: "utf8", lineSeparator, hasBOM, error: null };
  } catch {
    return { content: "", encoding: "invalid_utf8", lineSeparator: "LF", hasBOM: false, error: { kind: "decode", code: "invalid_utf8", message: "File is not valid UTF-8 and is read-only", path } };
  }
}
/**
 * Encode a Vault-relative path for a Markdown link target. Spaces and the
 * characters that would terminate a link destination are percent-encoded;
 * `/` stays literal so the reference keeps its directory semantics.
 */
export function encodeReference(path: string): string {
  return path
    .split("/")
    .map((segment) => encodeURIComponent(segment).replace(/[!'()*]/g, (char) => `%${char.charCodeAt(0).toString(16).toUpperCase()}`))
    .join("/");
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

let vaultGeneration = 0;
class StaleWorkspaceRequest extends Error {}

/**
 * Reveal `path` and every one of its ancestors without disturbing the rows the
 * user collapsed elsewhere: the path and its parent directories leave
 * `collapsedPaths`, and nothing is added to `expandedPaths` (a path that is
 * not collapsed is expanded by default).
 */
function revealedTree(tree: FileTreeState, path: string): FileTreeState {
  const parts = path.split("/");
  const hidden = new Set([path, ...parts.slice(0, -1).map((_, index) => parts.slice(0, index + 1).join("/"))]);
  const collapsedPaths = tree.collapsedPaths.filter(value => !hidden.has(value));
  if (collapsedPaths.length === tree.collapsedPaths.length) return tree;
  return { ...tree, collapsedPaths };
}
/**
 * Directory that receives the children of `notePath`. A folder note
 * (`notes/A/index.md`) already owns its own directory, so it never nests one
 * level deeper; every other note uses the sibling-directory convention.
 */
function childDirectoryOf(notePath: string): string {
  const name = notePath.split("/").at(-1) ?? notePath;
  const directory = noteDirectory(notePath);
  if (name.toLowerCase() === "index.md" && directory) return directory;
  return noteChildDirectory(notePath);
}
/** Normalise a user-facing note path and reject the unusable ones. */
function requireNote(path: string, message: string): string {
  const clean = path.trim().replace(/^\/+/, "").replace(/\/+$/, "");
  if (!clean) throw Object.assign(new Error(message), { code: "invalid_request" });
  if (clean.split("/").some((part) => part === "..")) throw Object.assign(new Error("Unsafe path"), { code: "invalid_request" });
  return /\.(md|markdown)$/i.test(clean) ? clean : `${clean}.md`;
}
/** Close every tab/session/expansion pointing at a path that left the tree. */
function closeTreePaths(set: (updater: (current: WorkspaceState) => Partial<WorkspaceState>) => void, removed: string): void {
  set((current) => {
    const gone = (value: string) => value === removed || value.startsWith(`${removed}/`);
    const tabs = current.tabs.filter((tab) => !gone(tab.path));
    const index = current.tabs.findIndex((tab) => tab.path === current.activePath);
    const sessions = { ...current.sessions };
    for (const key of Object.keys(sessions)) if (gone(key)) delete sessions[key];
    return {
      tabs,
      sessions,
      activePath: current.activePath && gone(current.activePath) ? tabs[Math.min(index, tabs.length - 1)]?.path ?? null : current.activePath,
      tree: { ...current.tree, expandedPaths: current.tree.expandedPaths.filter((value) => !gone(value)), collapsedPaths: current.tree.collapsedPaths.filter((value) => !gone(value)) },
    };
  });
}

/**
 * Create one note inside an existing directory with a non-colliding default
 * name, then reveal it. `directory` must already exist.
 */
/**
 * Reference a freshly created document from `ownerPath` with a `[[wikilink]]`,
 * so a nested document is reachable from the note that owns it instead of only
 * through the file tree.
 *
 * The owner is only touched while it is open and clean: an unopened note (or one
 * with unsaved edits, a conflict or a save error) keeps its bytes untouched, so
 * creating a document can never clobber work in progress. The owner is not
 * brought to the front — the new document stays the active tab — and the insert
 * marks it dirty for the normal auto-save channel.
 */
function linkOwnedDocument(state: WorkspaceState, ownerPath: string | null | undefined, path: string): void {
  if (!ownerPath || ownerPath === path) return;
  const session = state.sessions[ownerPath];
  if (!session || session.encoding !== "utf8" || session.dirty || session.conflict || session.saveState === "saving" || session.saveState === "error") return;
  if (session.content.includes(`[[${noteStem(path)}]]`)) return;
  state.insertMarkdownAtSelection(ownerPath, `[[${noteStem(path)}]]`);
  void state.save(ownerPath, "auto");
}

async function createIn(state: WorkspaceState, directory: string, options: CreateChildNoteOptions, fallbackName: string, set: (updater: (current: WorkspaceState) => Partial<WorkspaceState>) => void, ownerPath?: string | null): Promise<string> {
  // Only the target directory decides whether a name is taken. Notes created
  // moments ago are already open tabs while the tree listing is still
  // refreshing, so both sources are consulted.
  const prefix = directory ? `${directory}/` : "";
  const taken = [...state.tree.entries.map((entry) => entry.path), ...state.tabs.map((tab) => tab.path)]
    .filter((path) => path.startsWith(prefix))
    .map((path) => path.slice(prefix.length))
    .filter((name) => !name.includes("/"));
  const name = options.name?.trim() || fallbackName;
  const path = `${directory}/${suggestNoteName(taken, name)}`;
  const created = await state.createNote(path, options.content ?? "");
  set((current) => ({ tree: revealedTree(current.tree, created) }));
  if (options.linkFromOwner !== false) linkOwnedDocument(state, ownerPath, created);
  return created;
}
/** Set by the mounted editor (see WorkspaceState.registerCaretInsert). */
let caretInsert: CaretInsertHandler | null = null;
let api: WorkspaceApi = {
  fetchVaultFiles: async () => ({ entries: [] }),
  fetchVaultFile: async () => { throw new Error("Workspace API is not configured"); },
  patchVaultFile: async () => { throw new Error("Workspace API is not configured"); },
  createVaultFile: async () => { throw new Error("Workspace API is not configured"); },
  createVaultDirectory: async () => { throw new Error("Workspace API is not configured"); },
  moveVaultFile: async () => { throw new Error("Workspace API is not configured"); },
  fetchLinks: async () => { throw new Error("Workspace API is not configured"); },
  fetchBacklinks: async () => { throw new Error("Workspace API is not configured"); },
  searchNotes: async () => { throw new Error("Workspace API is not configured"); },
  fetchGraph: async () => { throw new Error("Workspace API is not configured"); },
  fetchLocalGraph: async () => { throw new Error("Workspace API is not configured"); },
  fetchTagGraph: async () => { throw new Error("Workspace API is not configured"); },
};
/** M2 test doubles configure only the vault trio; merge keeps M3 defaults. */
export function configureWorkspaceApi(next: WorkspaceApi) {
  const guarded = Object.fromEntries(Object.entries(next).map(([key, value]) => [key, typeof value !== "function" ? value : (...args: unknown[]) => {
    const generation = vaultGeneration;
    return Promise.resolve((value as (...inputs: unknown[]) => unknown)(...args)).then((result) => {
      if (generation !== vaultGeneration) throw new StaleWorkspaceRequest();
      return result;
    }, (error) => { if (generation !== vaultGeneration) throw new StaleWorkspaceRequest(); throw error; });
  }]));
  api = { ...api, ...guarded };
}

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
    if (!path || get().vaultStale || ((get().workspaceFrozen || !get().autoSave) && reason !== "manual")) return Promise.resolve();
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
      const sentLineSeparator = initial.lineSeparator;
      const sentHasBOM = initial.hasBOM;
      const sentBase64 = toBase64(sentContent, sentLineSeparator, sentHasBOM);
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
            byteLength: result.byte_length ?? latest.byteLength,
            dirty: unchanged ? false : latest.dirty,
            saveState: unchanged ? "saved" : latest.saveState,
            error: unchanged ? null : latest.error,
          };
          return { sessions: { ...state.sessions, [path]: next }, tabs: state.tabs.map((tab) => tab.path === path ? { ...tab, dirty: next.dirty } : tab) };
        });
        if (get().sessions[path]?.requestVersion !== sentVersion) queue.pending = true;
      } catch (error) { if (error instanceof StaleWorkspaceRequest) return ;
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
    tree: { entries: [], expandedPaths: [], collapsedPaths: [], status: "idle", error: null }, tabs: [], activePath: null, sessions: {},
    attachment: { busy: false, error: null, lastPath: null, source: null },
    trash: { status: "idle", entries: [], retentionDays: 30, totalBytes: 0, error: null, busy: false },
    relations: { status: "idle", path: null, outgoing: null, backlinks: null, brokenCount: 0, error: null },
    search: { status: "idle", query: "", response: null, error: null },
    graph: { status: "idle", scope: "global", note: null, depth: 1, direction: "both", tag: null, includeBroken: true, limit: 500, offset: 0, response: null, error: null, requestVersion: 0 }, ai: { status: "idle", action: null, notePath: null, response: null, error: null, requestVersion: 0 },
    ...readPreferences(), workspaceFrozen: false, vaultStale: false,
    setPreferences: (value) => { const next = validatePreferences({ ...get(), ...value }); persistPreferences(next); set(next); },
    saveAll: async () => {
      if (get().vaultStale) return false;
      for (const path of Object.keys(get().sessions)) {
        await save(path, "manual");
        while (saveQueues.get(path)?.running) await saveQueues.get(path)!.running;
      }
      return !Object.values(get().sessions).some((session) => session.dirty || session.saveState === "conflict" || session.saveState === "error");
    },
    resetVault: () => {
      vaultGeneration++;
      for (const queue of saveQueues.values()) queue.pending = false;
      saveQueues.clear(); openVersions.clear(); openRequests.clear(); relationVersions.clear();
      searchVersion++; graphVersion++; aiVersion++; historyVersion++;
      set({ ...useWorkspaceStore.getInitialState(), ...validatePreferences(get()), workspaceFrozen: false, vaultStale: false });
    },
    history: { status: "idle", page: null, selected: null, error: null, requestVersion: 0, busy: false },
    loadHistory: async () => {
      const version = ++historyVersion;
      set((s) => ({ history: { ...s.history, status: "loading", error: null, requestVersion: version } }));
      if (!api.listHistory) { set((s) => ({ history: { ...s.history, status: "error", error: { kind: "network", message: "History API is not configured" } } })); return; }
      try { const page = await api.listHistory(); if (historyVersion === version) set((s) => ({ history: { ...s.history, status: "ready", page, error: null } })); }
      catch (error) { if (error instanceof StaleWorkspaceRequest) return ; if (historyVersion === version) set((s) => ({ history: { ...s.history, status: "error", error: apiError(error) } })); }
    },
    selectHistory: async (id) => {
      if (!api.getHistory) return;
      set((s) => ({ history: { ...s.history, busy: true, error: null } }));
      try { const selected = await api.getHistory(id); set((s) => ({ history: { ...s.history, selected, busy: false } })); }
      catch (error) { if (error instanceof StaleWorkspaceRequest) return ; set((s) => ({ history: { ...s.history, busy: false, error: apiError(error) } })); }
    },
    createJob: async (request) => { if (!api.createJob) return null; const generation = vaultGeneration; set(s => ({history: {...s.history, busy: true, error: null}})); try { const job = await api.createJob(request); await get().loadHistory(); return job; } catch (error) { if (error instanceof StaleWorkspaceRequest) return null; set((s) => ({ history: { ...s.history, error: apiError(error) } })); return null; } finally {if (generation === vaultGeneration) set(s => ({history: {...s.history, busy: false}}));} },
    acceptJob: async (id, request = { confirm: true }) => { if (!api.acceptJob) return null; const generation = vaultGeneration; set(s => ({history: {...s.history, busy: true, error: null}})); try { const job = await api.acceptJob(id, request); await get().loadHistory(); await get().selectHistory(id); return job; } catch (error) { if (error instanceof StaleWorkspaceRequest) return null; set((s) => ({ history: { ...s.history, error: apiError(error) } })); return null; } finally {if (generation === vaultGeneration) set(s => ({history: {...s.history, busy: false}}));} },
    rejectJob: async (id) => { if (!api.rejectJob) return null; const generation = vaultGeneration; set(s => ({history: {...s.history, busy: true, error: null}})); try { const job = await api.rejectJob(id); await get().loadHistory(); await get().selectHistory(id); return job; } catch (error) { if (error instanceof StaleWorkspaceRequest) return null; set((s) => ({ history: { ...s.history, error: apiError(error) } })); return null; } finally {if (generation === vaultGeneration) set(s => ({history: {...s.history, busy: false}}));} },
    undoHistory: async (id) => { if (!api.undoHistory) return null; const generation = vaultGeneration; set(s => ({history: {...s.history, busy: true, error: null}})); try { const result = await api.undoHistory(id); await get().loadHistory(); await get().selectHistory(id); return result; } catch (error) { if (error instanceof StaleWorkspaceRequest) return null; set((s) => ({ history: { ...s.history, error: apiError(error) } })); return null; } finally {if (generation === vaultGeneration) set(s => ({history: {...s.history, busy: false}}));} },
    loadTree: async () => {
      set((s) => ({ tree: { ...s.tree, status: "loading", error: null } }));
      try {
        const result = await api.fetchVaultFiles({ recursive: true });
        const entries = result.entries.filter((e) => !e.path.split("/").some((part) => part.startsWith("."))).sort((a, b) => a.path.localeCompare(b.path));
        // Every container starts open: `expandedPaths` lists them all, and the
        // user's collapses (`collapsedPaths`) are the exceptions on top of it.
        set((s) => ({ tree: { ...s.tree, entries, expandedPaths: expandablePaths(entries), status: "ready" } }));
      } catch (error) { if (error instanceof StaleWorkspaceRequest) return ;
        const e = apiError(error);
        set((s) => ({ tree: { ...s.tree, status: e.code === "vault_not_configured" ? "not_configured" : e.status === 503 ? "unavailable" : "error", error: e } }));
      }
    },
    toggleDirectory: (path) => set((s) => {
      // One toggle for both row kinds (a folder, or a note that owns a child
      // folder): collapsing remembers the path explicitly, expanding clears the
      // flag and keeps the path in the expanded set.
      const collapsedPaths = s.tree.collapsedPaths.includes(path)
        ? s.tree.collapsedPaths.filter((value) => value !== path)
        : [...new Set([...s.tree.collapsedPaths, path])];
      const expandedPaths = collapsedPaths.includes(path)
        ? s.tree.expandedPaths
        : [...new Set([...s.tree.expandedPaths, path])];
      return { tree: { ...s.tree, collapsedPaths, expandedPaths } };
    }),
    expandPath: (path) => set((s) => {
      if (!s.tree.collapsedPaths.length) return s;
      return { tree: revealedTree(s.tree, path) };
    }),
    createChildNote: async (parentPath, options = {}) => {
      const notePath = requireNote(parentPath, "A child note needs a parent note");
      const directory = childDirectoryOf(notePath);
      await get().ensureFolder(directory);
      return createIn(get(), directory, options, noteStem(notePath), set, notePath);
    },
    createSiblingNote: async (notePath, options = {}) => {
      const clean = requireNote(notePath, "A sibling note needs a note");
      // A folder row is already the container, so "the next note beside it"
      // simply means the next note inside it.
      const directory = noteDirectory(clean);
      await get().ensureFolder(directory);
      return createIn(get(), directory, options, noteStem(clean), set, clean);
    },
    createFolder: async (path) => {
      const clean = path.trim().replace(/^\/+/, "").replace(/\/+$/, "");
      if (!clean) throw new Error("Enter a folder name");
      if (clean.split("/").some(part => part === "..")) throw new Error("Unsafe path");
      const generation = vaultGeneration;
      const result = await api.createVaultDirectory!({ path: clean });
      if (generation === vaultGeneration) await get().loadTree();
      set(state => ({ tree: { ...state.tree, expandedPaths: [...new Set([...state.tree.expandedPaths, result.path])] } }));
      return result.path;
    },
    moveEntry: async (sourcePath, destinationDirectory) => {
      const clean = destinationDirectory.replace(/^\/+/, "").replace(/\/+$/, "");
      const name = sourcePath.split("/").at(-1) ?? sourcePath;
      const destinationPath = clean ? `${clean}/${name}` : name;
      if (destinationPath === sourcePath) return sourcePath;
      const generation = vaultGeneration;
      const session = get().sessions[sourcePath];
      // The move endpoint requires an expected digest (PLAN-M1 no-overwrite
      // contract). Use the open session's base hash when available, otherwise
      // read the file once so the drag is still confirmed against real bytes.
      const expectedSha256 = session && !session.dirty
        ? session.baseSha256
        : (await api.fetchVaultFile(sourcePath)).sha256;
      const result = await api.moveVaultFile!({ sourcePath, destinationPath, expectedSha256 });
      const movedTo = result.path;
      if (generation === vaultGeneration) {
        // Re-point the open tab/session so unsaved edits survive a drag.
        const state = get();
        if (state.sessions[sourcePath] || state.tabs.some(tab => tab.path === sourcePath)) {
          set(current => {
            const sessions = { ...current.sessions };
            const session = sessions[sourcePath];
            if (session) { delete sessions[sourcePath]; sessions[movedTo] = { ...session, path: movedTo }; }
            const tabs = current.tabs.map(tab => tab.path === sourcePath ? { ...tab, path: movedTo, title: movedTo.split("/").at(-1) ?? movedTo } : tab);
            return { sessions, tabs, activePath: current.activePath === sourcePath ? movedTo : current.activePath };
          });
        }
        await get().loadTree();
      }
      return movedTo;
    },
    /**
     * Move a file or a whole folder into the recycle bin.
     *
     * This is the user-facing "delete": nothing is lost immediately, the entry
     * can be restored for the retention window (30 days by default), and every
     * affected tab is closed because the path no longer exists in the Vault.
     * The digest of a file is taken from disk first, so an externally changed
     * file is a 409 instead of a silent removal.
     */
    moveToTrash: async (path) => {
      const clean = path.trim().replace(/^\/+/, "").replace(/\/+$/, "");
      if (!clean) throw Object.assign(new Error("Select a file to delete"), { code: "invalid_request" });
      const entry = get().tree.entries.find((item) => item.path === clean);
      if (!entry) throw Object.assign(new Error("That file is no longer in the Vault"), { code: "not_found" });
      if (!api.moveToTrash) throw Object.assign(new Error("Trash API is not configured"), { code: "invalid_request" });
      const generation = vaultGeneration;
      const expectedSha256 = entry.kind === "file" ? (await api.fetchVaultFile(clean)).sha256 : null;
      const moved = await api.moveToTrash({ path: clean, expectedSha256 });
      if (generation !== vaultGeneration) return moved;
      set((current) => ({ trash: { ...current.trash, entries: [moved, ...current.trash.entries.filter(item => item.id !== moved.id)], totalBytes: current.trash.totalBytes + moved.byte_length } }));
      closeTreePaths(set, clean);
      await get().loadTree();
      return moved;
    },
    loadTrash: async () => {
      set((state) => ({ trash: { ...state.trash, status: state.trash.status === "ready" ? "ready" : "loading", error: null } }));
      if (!api.fetchTrash) { set((state) => ({ trash: { ...state.trash, status: "error", error: { kind: "network", message: "Trash API is not configured" } } })); return; }
      try {
        const page = await api.fetchTrash();
        set((state) => ({ trash: { ...state.trash, status: "ready", entries: page.entries, retentionDays: page.retention_days, totalBytes: page.total_bytes, error: null } }));
      } catch (error) {
        if (error instanceof StaleWorkspaceRequest) return ;
        set((state) => ({ trash: { ...state.trash, status: "error", error: apiError(error) } }));
      }
    },
    restoreFromTrash: async (entry) => {
      if (!api.restoreTrashEntry) return null;
      set((state) => ({ trash: { ...state.trash, busy: true, error: null } }));
      try {
        // A name that is occupied again gets the server's "(restored)" suffix.
        const result = await api.restoreTrashEntry({ id: entry.id, renameIfOccupied: true });
        set((state) => ({ trash: { ...state.trash, busy: false, entries: state.trash.entries.filter(item => item.id !== entry.id), totalBytes: Math.max(0, state.trash.totalBytes - entry.byte_length) } }));
        await get().loadTree();
        return result.path;
      } catch (error) {
        if (error instanceof StaleWorkspaceRequest) return null;
        set((state) => ({ trash: { ...state.trash, busy: false, error: apiError(error) } }));
        return null;
      }
    },
    deleteFromTrash: async (entry) => {
      if (!api.deleteTrashEntry) return false;
      set((state) => ({ trash: { ...state.trash, busy: true, error: null } }));
      try {
        await api.deleteTrashEntry(entry.id);
        set((state) => ({ trash: { ...state.trash, busy: false, entries: state.trash.entries.filter(item => item.id !== entry.id), totalBytes: Math.max(0, state.trash.totalBytes - entry.byte_length) } }));
        return true;
      } catch (error) {
        if (error instanceof StaleWorkspaceRequest) return false;
        set((state) => ({ trash: { ...state.trash, busy: false, error: apiError(error) } }));
        return false;
      }
    },
    emptyTrash: async () => {
      if (!api.emptyTrash) return false;
      set((state) => ({ trash: { ...state.trash, busy: true, error: null } }));
      try {
        await api.emptyTrash();
        set((state) => ({ trash: { ...state.trash, busy: false, entries: [], totalBytes: 0 } }));
        return true;
      } catch (error) {
        if (error instanceof StaleWorkspaceRequest) return false;
        set((state) => ({ trash: { ...state.trash, busy: false, error: apiError(error) } }));
        return false;
      }
    },
    deleteEntry: async (path) => {
      const clean = path.trim().replace(/^\/+/, "").replace(/\/+$/, "");
      if (!clean) throw Object.assign(new Error("Select a file to delete"), { code: "invalid_request" });
      const entry = get().tree.entries.find((item) => item.path === clean);
      if (!entry) throw Object.assign(new Error("That file is no longer in the Vault"), { code: "not_found" });
      // `DELETE /vault/file` only removes regular files (a directory is
      // `400 not_a_file`), so a folder is refused here with a message the UI can
      // act on: an empty one can simply be kept, and a full one must lose its
      // files first.
      if (entry.kind === "directory") {
        const inside = get().tree.entries.filter((item) => item.path.startsWith(`${clean}/`)).length;
        throw Object.assign(new Error(inside ? "A folder with files inside cannot be deleted yet" : "Only files can be deleted"), { code: inside ? "folder_not_empty" : "not_a_file" });
      }
      const generation = vaultGeneration;
      // Deleting is irreversible, so it is confirmed against the bytes on disk
      // (the same no-surprise rule as rename): a file that changed outside the
      // app is a 409 rather than a silent removal of someone else's edit.
      const expectedSha256 = (await api.fetchVaultFile(clean)).sha256;
      const result = await api.deleteVaultFile!({ path: clean, expectedSha256 });
      const deleted = result.path;
      if (generation !== vaultGeneration) return deleted;
      // Close every tab of the deleted path and of a deleted folder's contents.
      closeTreePaths(set, deleted);
      await get().loadTree();
      return deleted;
    },
    toggleTask: (path, index) => {
      const session = get().sessions[path];
      if (!session || session.encoding !== "utf8" || get().workspaceFrozen || get().vaultStale) return false;
      const next = toggleTaskInSource(session.content, index);
      if (next === session.content) return false;
      get().updateContent(path, next);
      return true;
    },
    ensureFolder: async (directory) => {
      const clean = directory.trim().replace(/^\/+/, "").replace(/\/+$/, "");
      if (!clean) return "";
      if (clean.split("/").some((part) => !part || part === "." || part === "..")) {
        throw Object.assign(new Error("Unsafe folder path"), { code: "invalid_name" });
      }
      // The Vault API creates one level at a time (parents must exist), so walk
      // the path and create only the missing levels.
      const known = new Set(get().tree.entries.filter((entry) => entry.kind === "directory").map((entry) => entry.path));
      const segments = clean.split("/");
      for (let index = 0; index < segments.length; index += 1) {
        const partial = segments.slice(0, index + 1).join("/");
        if (known.has(partial)) continue;
        try {
          await get().createFolder(partial);
        } catch (error) {
          // A concurrent creator (or a folder created outside the app) is fine.
          if ((error as { code?: string } | null)?.code !== "already_exists") throw error;
        }
        known.add(partial);
      }
      return clean;
    },
    openOrCreateLinkedNote: async (fromPath, linkTarget) => {
      const target = linkTarget.trim();
      if (!target) throw Object.assign(new Error("Empty link target"), { code: "invalid_name" });
      // A link that already resolves (same basename anywhere in the Vault, the
      // Obsidian rule the index uses) just opens that note.
      const leaf = target.split("/").at(-1) ?? target;
      const stem = leaf.replace(/\.(md|markdown)$/i, "").toLowerCase();
      const existing = get().tree.entries.find((entry) => {
        if (entry.kind !== "file") return false;
        const name = entry.path.split("/").at(-1) ?? entry.path;
        return name.replace(/\.(md|markdown)$/i, "").toLowerCase() === stem;
      });
      if (existing) {
        await get().openFile(existing.path);
        return existing.path;
      }
      const destination = wikilinkCreatePath(fromPath, target);
      if (!destination) throw Object.assign(new Error("Unsafe link target"), { code: "invalid_name" });
      const directory = noteDirectory(destination);
      if (directory) await get().ensureFolder(directory);
      const created = await get().createNote(destination, "");
      return created;
    },
    renameEntry: async (path, newName) => {
      const trimmed = newName.trim();
      const parent = path.split("/").slice(0, -1).join("/");
      const current = path.split("/").at(-1) ?? path;
      if (!trimmed) throw Object.assign(new Error("Enter a file name"), { code: "invalid_name" });
      if (trimmed === current) return path;
      // A rename is a same-directory move; the name itself must stay a single
      // path segment so it can never escape the current directory.
      if (trimmed.includes("/") || trimmed.includes("\\") || trimmed === "." || trimmed === "..") {
        throw Object.assign(new Error("A file name cannot contain / or \\"), { code: "invalid_name" });
      }
      const destinationPath = parent ? `${parent}/${trimmed}` : trimmed;
      const generation = vaultGeneration;
      // Always confirm against the bytes on disk. Using the open session's
      // baseSha256 would 409 a file that has unsaved edits (the disk hash is
      // newer than the session base), and rename must keep unsaved edits.
      const expectedSha256 = (await api.fetchVaultFile(path)).sha256;
      const result = await api.moveVaultFile!({ sourcePath: path, destinationPath, expectedSha256 });
      const renamedTo = result.path;
      if (generation === vaultGeneration) {
        // Re-point the open tab/session so unsaved edits and the caret survive.
        const state = get();
        if (state.sessions[path] || state.tabs.some(tab => tab.path === path)) {
          set(current => {
            const sessions = { ...current.sessions };
            const open = sessions[path];
            if (open) { delete sessions[path]; sessions[renamedTo] = { ...open, path: renamedTo }; }
            const tabs = current.tabs.map(tab => tab.path === path ? { ...tab, path: renamedTo, title: renamedTo.split("/").at(-1) ?? renamedTo } : tab);
            return { sessions, tabs, activePath: current.activePath === path ? renamedTo : current.activePath };
          });
        }
        await get().loadTree();
        // A renamed nested document must stop being a dangling link in the note
        // that owns it. The tree (which resolved the owner before the move) and
        // the ambiguity check keep this from re-pointing an unrelated `[[name]]`.
        const ownerPath = foldedPaths(state.tree.entries).get(path);
        if (ownerPath && isEditableMarkdown(ownerPath) && isEditableMarkdown(renamedTo)) {
          const stem = noteStem(path);
          const nextStem = noteStem(renamedTo);
          const ambiguous = state.tree.entries.filter(entry => entry.kind === "file" && noteStem(entry.path).toLowerCase() === stem.toLowerCase()).length > 1;
          const owner = get().sessions[ownerPath];
          if (owner?.encoding === "utf8" && !owner.conflict) {
            const next = rewriteWikilinkTarget(owner.content, stem, nextStem, ambiguous);
            if (next !== owner.content) {
              get().updateContent(ownerPath, next);
              void get().save(ownerPath, "auto");
            }
          }
        }
      }
      return renamedTo;
    },
    createNote: async (path, content = "") => {
      const clean = path.trim().replace(/^\/+/, "");
      if (!clean) throw new Error("Enter a file name");
      if (clean.split("/").some(part => part === "..")) throw new Error("Unsafe path");
      const generation = vaultGeneration;
      const result = await api.createVaultFile!({ path: clean, contentBase64: toBase64(content) });
      if (generation === vaultGeneration) await get().loadTree();
      await get().openFile(result.path);
      return result.path;
    },
      openFile: async (path) => {
      // Opening a note reveals it: an ancestor the user collapsed must not keep
      // the active note hidden from the tree.
      set(state => ({tree: revealedTree(state.tree, path)}));
      const existing = get().sessions[path];
      if (existing) { set({ activePath: path }); return; }
      const prior = openRequests.get(path);
      if (prior) { set({ activePath: path }); return prior; }
      const generation = vaultGeneration;
      const version = (openVersions.get(path) ?? 0) + 1;
      openVersions.set(path, version);
      set((s) => ({ tabs: s.tabs.some((t) => t.path === path) ? s.tabs : [...s.tabs, { path, title: path.split("/").at(-1) ?? path, dirty: false, loading: true, error: null }], activePath: path }));
      const request = (async () => {
        try {
          const result = await api.fetchVaultFile(path);
          const { content, encoding, lineSeparator, hasBOM, error: decodeError } = decodeFileContent(result.content_base64, path);
          if (openVersions.get(path) !== version) return;
          const session: EditorSession = { path, content, baseSha256: result.sha256, baseContentBase64: result.content_base64, byteLength: result.byte_length, encoding, lineSeparator, hasBOM, dirty: false, saveState: "saved", error: decodeError, conflict: null, notice: null, requestVersion: version };
          set((s) => ({ sessions: { ...s.sessions, [path]: session }, tabs: s.tabs.map((tab) => tab.path === path ? { ...tab, loading: false, error: decodeError } : tab) }));
        } catch (error) { if (error instanceof StaleWorkspaceRequest) return ;
          if (openVersions.get(path) !== version) return;
          const e = apiError(error, path);
          set((s) => ({ tabs: s.tabs.map((tab) => tab.path === path ? { ...tab, loading: false, error: e } : tab) }));
        } finally { if (generation === vaultGeneration) openRequests.delete(path); }
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
        const { content, encoding, lineSeparator, hasBOM, error: decodeError } = decodeFileContent(result.content_base64, path);
        if (openVersions.get(path) !== version) return;
        set((s) => ({ sessions: { ...s.sessions, [path]: { ...s.sessions[path], content, baseSha256: result.sha256, baseContentBase64: result.content_base64, byteLength: result.byte_length, dirty: false, saveState: "saved", conflict: null, error: decodeError, notice: null, encoding, lineSeparator, hasBOM, requestVersion: version } }, tabs: s.tabs.map((tab) => tab.path === path ? { ...tab, dirty: false, error: decodeError } : tab) }));
      } catch (error) { if (error instanceof StaleWorkspaceRequest) return ;
        if (openVersions.get(path) !== version) return;
        set((s) => ({ sessions: { ...s.sessions, [path]: { ...s.sessions[path], error: apiError(error, path), saveState: "error", conflict: null, notice: null } } }));
      }
    },
    keepLocal: async (path) => {
      // P2 fix: "Keep local" must not leave the session permanently stuck.
      // We refresh only the server's current sha256/content snapshot (never
      // touching the user's in-memory content), then let the next save
      // proceed with that fresh baseSha256 as the expected hash. This is an
      // explicit, user-initiated override of the newer server version.
      const session = get().sessions[path]; if (!session) return;
      const version = (openVersions.get(path) ?? session.requestVersion) + 1; openVersions.set(path, version);
      try {
        const result = await api.fetchVaultFile(path);
        if (openVersions.get(path) !== version) return;
        set((s) => {
          const current = s.sessions[path];
          if (!current) return s;
          return { sessions: { ...s.sessions, [path]: { ...current, baseSha256: result.sha256, baseContentBase64: result.content_base64, dirty: true, saveState: "saved", conflict: null, error: null, notice: "Kept local changes - will overwrite the server version on next save", requestVersion: version } } };
        });
      } catch (error) { if (error instanceof StaleWorkspaceRequest) return ;
        if (openVersions.get(path) !== version) return;
        set((s) => {
          const current = s.sessions[path];
          if (!current) return s;
          return { sessions: { ...s.sessions, [path]: { ...current, error: apiError(error, path), saveState: "error", conflict: null, notice: null } } };
        });
      }
    },
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
      } catch (error) { if (error instanceof StaleWorkspaceRequest) return ;
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
      } catch (error) { if (error instanceof StaleWorkspaceRequest) return ;
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
      } catch (error) { if (error instanceof StaleWorkspaceRequest) return ;
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
      catch (error) { if (error instanceof StaleWorkspaceRequest) return ; if (aiVersion === version) set({ ai: { status: error && typeof error === "object" && "status" in error && Number((error as {status:number}).status) === 503 ? "offline" : "error", action, notePath: path ?? null, response: null, error: apiError(error, path ?? undefined), requestVersion: version } }); }
    },
    clearAI: () => { ++aiVersion; set({ ai: { status: "idle", action: null, notePath: null, response: null, error: null, requestVersion: aiVersion } }); },
    setTheme: (theme) => get().setPreferences({ theme }),
    setSplitRatio: (splitRatio) => get().setPreferences({ splitRatio }),
    clearAttachmentError: () => set((state) => ({ attachment: { ...state.attachment, error: null } })),
    insertMarkdownAtSelection: (path, markdown) => {
      const session = get().sessions[path];
      if (!session || session.encoding !== "utf8" || get().workspaceFrozen || get().vaultStale) return false;
      // Prefer the mounted editor so the reference lands at the live caret;
      // the fallback below appends at the end when no editor is mounted.
      if (caretInsert && caretInsert(path, markdown)) return true;
      const content = session.content;
      const snippet = markdown.endsWith("\n") ? markdown : `${markdown}\n`;
      // Insert as its own paragraph so a reference never merges into the line
      // the caret happens to sit on; the caret is preserved by the editor.
      const prefix = content && !content.endsWith("\n") ? "\n" : "";
      get().updateContent(path, `${content}${prefix}${snippet}`);
      return true;
    },
    registerCaretInsert: (handler) => { caretInsert = handler; },
    uploadAndInsertAttachment: async (file, options = {}) => {
      const state = get();
      if (state.attachment.busy) return null;
      const notePath = options.notePath ?? state.activePath ?? undefined;
      const session = notePath ? state.sessions[notePath] : undefined;
      // The front end is the only decision maker for the target directory:
      // a tree context menu uses the clicked directory, every other entry
      // point uses the current note's directory ("" for a root note).
      const targetDirectory = options.targetDirectory !== undefined
        ? options.targetDirectory
        : noteDirectory(notePath);
      const source = options.source ?? (options.targetDirectory !== undefined ? "tree-context" : "toolbar");
      if (!api.uploadAttachmentBase64 || !api.uploadAttachmentMultipart) {
        set((current) => ({ attachment: { ...current.attachment, error: { kind: "network", message: "Attachment API is not configured" } } }));
        return null;
      }
      if (!notePath || !session || session.encoding !== "utf8") {
        set((current) => ({ attachment: { ...current.attachment, busy: false, source, error: { kind: "api", code: "invalid_request", message: "Open an editable Markdown note first", path: notePath } } }));
        return null;
      }
      if (state.workspaceFrozen || state.vaultStale) return null;
      const generation = vaultGeneration;
      set((current) => ({ attachment: { ...current.attachment, busy: true, error: null, source } }));
      try {
        const originalName = file.name && file.name.trim() ? file.name : "attachment";
        const result = file.size <= ATTACHMENT_JSON_MAX_BYTES
          ? await api.uploadAttachmentBase64({ originalName, contentBase64: await fileToBase64(file), targetDirectory })
          : await api.uploadAttachmentMultipart(file, targetDirectory, originalName);
        if (generation !== vaultGeneration) return null;
        if (!result || typeof result.path !== "string") throw new Error("Invalid attachment response");
        const reference = relativeMarkdownReference(notePath, result.path);
        const label = (result.original_name || originalName).replace(/[\[\]]/g, "");
        const markdown = isImageAttachment(result.path, result.content_type)
          ? `![${label}](${encodeReference(reference)})`
          : `[${label}](${encodeReference(reference)})`;
        const inserted = get().insertMarkdownAtSelection(notePath, markdown);
        set((current) => ({ attachment: { ...current.attachment, busy: false, error: null, lastPath: result.path, source } }));
        if (!inserted) return null;
        void get().loadTree();
        return result;
      } catch (error) {
        if (error instanceof StaleWorkspaceRequest) return null;
        set((current) => ({ attachment: { ...current.attachment, busy: false, error: apiError(error, notePath) } }));
        return null;
      }
    },
  };
});
export { toBase64, fromBase64 };
export { bytesToBase64Chunked as toBase64Chunked };
