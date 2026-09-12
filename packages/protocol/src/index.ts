export type HealthResponse = { status: "ok" };
export type AIStatus = "not_configured" | "offline" | "connected";
export interface AICapabilities { chat: boolean; embedding: boolean; rerank: boolean; }
export interface DiscoveredModel { id: string; owned_by: string | null; capabilities: AICapabilities; }
export type AIErrorCode = "not_configured" | "connection_refused" | "timeout" | "http_error" | "invalid_response" | "no_matching_model" | "unknown";
export interface AIStatusResponse { status: AIStatus; provider: "omlx"; endpoint: string | null; qwen_model: string | null; selected_model?: string | null; models: DiscoveredModel[]; capabilities: AICapabilities; error_code: AIErrorCode | null; message: string | null; checked_at: string | null; }
export type VaultFileKind = "file" | "directory";
export interface VaultFileEntry { path: string; kind: VaultFileKind; size: number | null; sha256: string | null; }
export interface VaultFileTreeResponse { root: string; entries: VaultFileEntry[]; generated_at: string; }
export interface FileReadResponse { path: string; content_base64: string; byte_length: number; sha256: string; content_type: string | null; }
export interface FileMutationResponse { path: string; sha256: string | null; byte_length: number | null; operation: "created" | "updated" | "deleted" | "moved"; }
// ---------------------------------------------------------------------------
// Attachment uploads (server source of truth: server/vault/schemas.py)
// ---------------------------------------------------------------------------
/**
 * Actual landing point and content metadata for one uploaded attachment.
 * `path` is the Vault-root-relative landing path (`attachments/` is not a
 * required prefix). `target_directory` is always decided by the front end.
 */
export interface AttachmentUploadResponse { path: string; sha256: string; byte_length: number; content_type: string; operation: "created"; original_name: string; }
/** JSON/base64 request body for `POST /vault/attachments`. */
export interface AttachmentUploadRequest { original_name: string; target_directory: string; content_base64: string; }
/** Read-only metadata for the raw resource endpoint (preview/download). */
export interface AttachmentResource { path: string; byte_length: number; content_type: string; }
/** Which entry point produced an upload (used for target-directory rules). */
export type AttachmentUploadSource = "toolbar" | "editor-drop" | "preview-drop" | "paste" | "tree-context";
/** Stable attachment error codes surfaced by `errorText`. */
export type AttachmentErrorCode = "invalid_request" | "invalid_attachment_name" | "not_found" | "already_exists" | "file_too_large" | "path_traversal" | "symlink_escape" | "vault_unavailable" | "vault_not_configured" | string;
export interface AttachmentUploadError { error: { code: AttachmentErrorCode; message: string; path: string | null }; }

/**
 * Resolve a Markdown-relative link target against the directory of
 * `notePath` and return a Vault-root-relative POSIX path.
 *
 * Returns `null` for anything that is not a safe Vault-relative reference:
 * absolute URLs/protocols (`http:`, `data:`, `javascript:`…), absolute paths,
 * NUL, backslashes and references that climb above the Vault root. `../`
 * segments are normalised deterministically, so a reference written against
 * the current note directory always maps to the same root-relative path.
 */
export function resolveVaultRelativePath(notePath: string | null | undefined, target: string): string | null {
  if (typeof target !== "string" || !target) return null;
  if (target.includes("\x00") || target.includes("\\")) return null;
  // A URI scheme (http:, data:, blob:, javascript:, C:) is never a Vault path.
  if (/^[A-Za-z][A-Za-z0-9+.-]*:/.test(target)) return null;
  if (target.startsWith("//") || target.startsWith("?")) return null;
  // A root-relative reference (``/notes/x.png``) is already Vault-root-relative.
  const base = target.startsWith("/") || !notePath ? [] : notePath.split("/").slice(0, -1);
  const parts: string[] = [];
  // Authored references are percent-encoded; decode each segment once so the
  // returned path is the canonical Vault path (encoding happens exactly once,
  // later, when the resource URL is built).
  for (const segment of [...base, ...target.split("/").map(decodeReferenceSegment)]) {
    if (!segment || segment === ".") continue;
    if (segment === "..") { if (!parts.length) return null; parts.pop(); continue; }
    parts.push(segment);
  }
  if (!parts.length) return null;
  return parts.join("/");
}

function decodeReferenceSegment(segment: string): string {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}

// ---------------------------------------------------------------------------
// Note hierarchy (nested documents)
// ---------------------------------------------------------------------------
/**
 * Basename of `path` without a Markdown extension (`notes/A.md` → `A`,
 * `assets/pic.png` → `pic.png`, `A` → `A`).
 */
export function noteStem(path: string): string {
  const name = path.split("/").at(-1) ?? path;
  return name.replace(/\.(md|markdown)$/i, "");
}

/**
 * Vault directory that holds the **child documents** of the note at
 * `notePath`: a sibling directory sharing the note's basename
 * (`notes/A.md` → `notes/A`, root `A.md` → `A`).
 *
 * The nesting model is deliberately folder-based, so the hierarchy stays plain
 * Markdown plus directories and every other file tool can see it. Non-note
 * paths (attachments) use their literal basename.
 */
export function noteChildDirectory(notePath: string): string {
  const segments = notePath.split("/");
  const last = segments.length - 1;
  const name = segments[last] ?? notePath;
  const stem = name.endsWith(".markdown") ? name.slice(0, -9) : name.endsWith(".md") ? name.slice(0, -3) : name;
  const folder = segments.slice(0, last);
  folder.push(stem);
  return folder.join("/");
}

/**
 * Ancestor notes of `notePath`, outermost first, derived from the sibling
 * directory convention: for `A/B/C.md` a directory `A/B/` means the note `A/B.md`
 * (when that note exists in `known`) is its parent, and a directory `A/` means
 * `A.md` (when it exists) is the grandparent. "Does it exist" is answered by
 * `known` because only the tree view holds the file listing; a gap in the
 * chain simply ends the ancestry.
 */
export function noteAncestorPaths(notePath: string, known: ReadonlySet<string>): string[] {
  const chain: string[] = [];
  const segments = notePath.split("/");
  for (let depth = segments.length - 1; depth >= 1; depth -= 1) {
    const directory = segments.slice(0, depth);
    const candidate = `${directory.join("/")}.md`;
    if (!known.has(candidate)) break;
    chain.unshift(candidate);
    // Walk one level up: `A/B.md` reads its own parent from the directory `A/`.
    segments.length = depth;
  }
  return chain;
}

/**
 * A non-colliding `base` name (`unfiled.md`, `unfiled 2.md`, …) for `existing`.
 * Paths are compared by basename, which is the scope a name has to be unique
 * in, so callers can pass whole Vault paths and still get a usable suggestion.
 */
export function suggestNoteName(existing: readonly string[], base: string): string {
  const taken = new Set(existing.map(path => path.split("/").at(-1) ?? path));
  for (let index = 1; index < 1000; index += 1) {
    const candidate = index === 1 ? `${base}.md` : `${base} ${index}.md`;
    if (!taken.has(candidate)) return candidate;
  }
  return `${base} ${Date.now()}.md`;
}

/**
 * Vault-root-relative directory of `notePath` (`""` for a root-level note).
 * This is the value a toolbar/drop/paste upload must send as
 * `target_directory`.
 */
export function noteDirectory(notePath: string | null | undefined): string {
  if (!notePath) return "";
  return notePath.split("/").slice(0, -1).join("/");
}

/**
 * POSIX Markdown reference from the directory of `notePath` to the
 * Vault-root-relative `targetPath` (deterministic `../` segments).
 */
export function relativeMarkdownReference(notePath: string | null | undefined, targetPath: string): string {
  const from = notePath ? notePath.split("/").slice(0, -1) : [];
  const to = targetPath.split("/");
  let shared = 0;
  while (shared < from.length && shared < to.length - 1 && from[shared] === to[shared]) shared += 1;
  const up = from.length - shared;
  return [...Array(up).fill(".."), ...to.slice(shared)].join("/");
}

/** Parsed parts of one `[[...]]` occurrence. */
export interface WikilinkTarget {
  /** Note/attachment name as authored, without section, block or alias. */
  target: string;
  /** Visible label (alias when present, otherwise the target). */
  label: string;
  /** `#Heading` part, without the `#`. */
  section: string | null;
  /** `^block` part, without the `^`. */
  block: string | null;
  /** `![[...]]` embed. */
  embed: boolean;
  /** `[[https://…]]` external link. */
  web: boolean;
}

/** Split an authored `[[target|alias]]` body into its parts. */
export function parseWikilink(body: string): WikilinkTarget {
  const embed = body.startsWith("!");
  let rest = embed ? body.slice(1) : body;
  if (rest.startsWith("[[") && rest.endsWith("]]")) rest = rest.slice(2, -2);
  let alias: string | null = null;
  const pipe = rest.indexOf("|");
  if (pipe >= 0) { alias = rest.slice(pipe + 1); rest = rest.slice(0, pipe); }
  let section: string | null = null;
  let block: string | null = null;
  const hash = rest.indexOf("#");
  if (hash >= 0) { section = rest.slice(hash + 1); rest = rest.slice(0, hash); }
  const caret = rest.indexOf("^");
  if (caret >= 0) { block = rest.slice(caret + 1); rest = rest.slice(0, caret); }
  const target = rest.trim();
  const web = /^[A-Za-z][A-Za-z0-9+.-]*:\/\//.test(target);
  return { target, label: (alias ?? target).trim() || target, section, block, embed, web };
}

/**
 * Vault-relative path a missing `[[target]]` note should be created at:
 * the source note's own directory for a bare name, or the authored relative
 * sub-path when the target contains `/`. Returns `null` when the target is not
 * a safe single note reference (empty, web, attachment suffix, `..`, NUL…).
 */
export function wikilinkCreatePath(notePath: string | null | undefined, target: string): string | null {
  const name = target.trim();
  if (!name || name.includes("\x00") || name.includes("\\")) return null;
  if (/^[A-Za-z][A-Za-z0-9+.-]*:/.test(name)) return null;
  if (name.startsWith("/") || name.endsWith("/")) return null;
  const segments = name.split("/");
  if (segments.some((segment) => !segment || segment === "." || segment === "..")) return null;
  // Keep the authored suffix when it is markdown; append `.md` otherwise.
  const last = segments.at(-1)!;
  const withExtension = /\.(md|markdown)$/i.test(last) ? name : `${name}.md`;
  const base = noteDirectory(notePath);
  return base ? `${base}/${withExtension}` : withExtension;
}

/**
 * Replace `[[…]]` / `![[…]]` with links the preview can turn into clickable
 * elements. Code spans and fenced blocks are left untouched, and the produced
 * `href` carries an opaque `wikilink:` scheme that only the renderer's own
 * resolver understands (the sanitizer only lets it through for this class).
 */
export function preprocessWikilinks(source: string): string {
  const parts: string[] = [];
  let index = 0;
  let fence: string | null = null;
  while (index < source.length) {
    const lineEnd = source.indexOf("\n", index);
    const end = lineEnd === -1 ? source.length : lineEnd;
    const line = source.slice(index, end);
    const fenceMatch = /^\s{0,3}(`{3,}|~{3,})/.exec(line);
    if (fenceMatch) {
      const marker = fenceMatch[1]![0]!;
      fence = fence && fence === marker ? null : fence === null ? marker : fence;
      parts.push(line);
      index = end + 1;
      continue;
    }
    parts.push(fence ? line : replaceInlineWikilinks(line));
    index = end + 1;
  }
  return parts.join("\n");
}

function replaceInlineWikilinks(line: string): string {
  const parts: string[] = [];
  let cursor = 0;
  while (cursor < line.length) {
    const open = line.indexOf("[[", cursor);
    if (open === -1) { parts.push(line.slice(cursor)); break; }
    const close = line.indexOf("]]", open + 2);
    if (close === -1) { parts.push(line.slice(cursor)); break; }
    // Inline code: copy it verbatim so `[[x]]` inside backticks stays literal.
    const before = line.slice(0, open);
    const ticks = (before.match(/(?<!`)`(?!`)/g) ?? []).length;
    parts.push(line.slice(cursor, open));
    // ``![[embed]]``: keep the leading bang so the embed form is recognised.
    const embedStart = open > 0 && line[open - 1] === "!";
    const raw = line.slice(embedStart ? open - 1 : open, close + 2);
    if (embedStart) parts[parts.length - 1] = parts[parts.length - 1]!.slice(0, -1);
    parts.push(ticks % 2 === 0 ? wikilinkToMarkdown(raw) : raw);
    cursor = close + 2;
  }
  return parts.join("");
}

function wikilinkToMarkdown(raw: string): string {
  const parsed = parseWikilink(raw);
  if (parsed.web || parsed.embed) return raw;
  if (!parsed.target) return raw;
  const label = parsed.label.replace(/([\\[\]])/g, "\\$1");
  const suffix = parsed.section ? `#${parsed.section}` : parsed.block ? `^${parsed.block}` : "";
  return `[${label}${suffix}](wikilink:${encodeURIComponent(parsed.target)})`;
}
/** One GFM task-list item found in the source, in document order. */
export interface TaskItemRef {
  /** Ordinal among all task items in the document (0-based). */
  index: number;
  /** Offset of the `[` of the checkbox marker. */
  from: number;
  /** Offset just after the `]`. */
  to: number;
  /** True when the item is `[x]` / `[X]`. */
  checked: boolean;
}

const TASK_LINE_RE = /^([ \t]*(?:[-*+]|\d+[.)])[ \t]+)\[([ xX])\](?=[ \t]|$)/;

/**
 * Locate every GFM task marker (`- [ ]` / `- [x]`) outside fenced code blocks.
 * Ordinals are document order, which is the order remark-gfm renders its
 * checkboxes in, so the nth rendered checkbox maps to the nth item here.
 */
export function taskItems(source: string): TaskItemRef[] {
  const items: TaskItemRef[] = [];
  let offset = 0;
  let fence: string | null = null;
  for (const line of source.split("\n")) {
    const fenceMatch = /^\s{0,3}(`{3,}|~{3,})/.exec(line);
    if (fenceMatch) {
      const marker = fenceMatch[1]![0]!;
      fence = fence && fence === marker ? null : fence === null ? marker : fence;
      offset += line.length + 1;
      continue;
    }
    if (!fence) {
      const match = TASK_LINE_RE.exec(line);
      if (match) {
        const markerStart = offset + match[1]!.length;
        items.push({
          index: items.length,
          from: markerStart,
          to: markerStart + 3,
          checked: match[2]!.toLowerCase() === "x",
        });
      }
    }
    offset += line.length + 1;
  }
  return items;
}

/** Flip one task marker in `source`; returns the new text (or the input when unchanged). */
export function toggleTaskInSource(source: string, index: number): string {
  const item = taskItems(source)[index];
  if (!item) return source;
  const marker = item.checked ? "[ ]" : "[x]";
  return `${source.slice(0, item.from)}${marker}${source.slice(item.to)}`;
}

/**
 * Replace GFM task markers with inline spans carrying their ordinal, so the
 * rendered preview can map a click back to the exact source position. The
 * original `[ ]` / `[x]` text is kept inside the span (remark then adds its own
 * checkbox, which the preview hides in favour of the span).
 */
export function markTaskCheckboxes(source: string): string {
  const items = taskItems(source);
  if (!items.length) return source;
  let out = "";
  let cursor = 0;
  for (const item of items) {
    out += source.slice(cursor, item.from);
    out += `<span class="task-toggle" data-task-index="${item.index}" data-task-checked="${item.checked}">${source.slice(item.from, item.to)}</span>`;
    cursor = item.to;
  }
  return out + source.slice(cursor);
}

// ---------------------------------------------------------------------------
// Trash / recycle bin (server source of truth: server/vault/trash_schemas.py)
// ---------------------------------------------------------------------------
/** One item held by the trash until the retention window expires. */
export interface TrashEntryDTO {
  id: string;
  /** Where the item came from; restore puts it back exactly there. */
  original_path: string;
  name: string;
  kind: "file" | "directory";
  byte_length: number;
  file_count: number;
  deleted_at: string;
  expires_at: string;
  days_remaining: number;
}
export interface TrashListResponse {
  entries: TrashEntryDTO[];
  count: number;
  total_bytes: number;
  retention_days: number;
  generated_at: string;
}
/** ``expected_sha256`` is required for files and ignored for folders. */
export interface TrashRequest { path: string; expected_sha256?: string | null; }
export interface TrashRestoreRequest { rename_if_occupied?: boolean; }

export type VaultErrorCode = "vault_not_configured" | "vault_unavailable" | "path_traversal" | "symlink_escape" | "not_found" | "already_exists" | "file_conflict" | "expected_hash_required" | "invalid_request" | "file_too_large" | "not_a_file" | "not_a_directory" | "atomic_write_failed" | "watcher_unavailable" | "index_unavailable" | "internal_error" | string;
export interface VaultErrorBody { error: { code: VaultErrorCode; message: string; path: string | null }; }
export type FrontmatterStatus = "none" | "ok" | "parse_error" | "unreadable";
export type MetadataParseErrorKind = "yaml" | "unterminated" | "non_dict" | "decode" | "yaml_unavailable" | "other";
export interface MetadataParseError { kind: MetadataParseErrorKind; message: string; line: number | null; }
export interface NoteMetadataResponse { path: string; title: string; frontmatter_status: FrontmatterStatus; properties: Record<string, unknown>; tags: string[]; parse_error: MetadataParseError | null; available: boolean; }
export type LinkKind = "wikilink" | "embed" | "web";
export interface LinkRef { target: string; raw: string; kind: LinkKind; display: string | null; section: string | null; block: string | null; resolved_path: string | null; broken: boolean; ambiguous: boolean; candidates: string[]; }
export interface NoteLinksResponse { path: string; outgoing: LinkRef[]; broken_count: number; generated_at: string; }
export interface BacklinkRef { source_path: string; title: string; text: string | null; }
export interface BacklinksResponse { path: string; backlinks: BacklinkRef[]; count: number; generated_at: string; }
export interface SearchHit { path: string; title: string; snippet: string; matched_terms: string[]; score: number; }
export interface SearchResponse { query: string; hits: SearchHit[]; total: number; degraded: boolean; skipped_notes: number; generated_at: string; }
export interface IndexRebuildResponse { indexed: number; skipped: number; failed: number; duration_ms: number; ready: boolean; generated_at: string; }
export type GraphNodeType = "note" | "tag";
export type GraphEdgeType = "link" | "backlink" | "tag";
export type GraphScope = "global" | "local" | "tag";
export interface GraphNode { id: string; type: GraphNodeType; label: string; path: string | null; title: string | null; tag: string | null; tag_folded: string | null; }
export interface GraphEdge { id: string; source: string; target: string; type: GraphEdgeType; directed: boolean; raw: string | null; resolved_path: string | null; section: string | null; block: string | null; broken: boolean; ambiguous: boolean; candidates: string[]; context: string | null; }
export interface GraphPage { limit: number; offset: number; next_offset: number | null; total_nodes: number; total_edges: number; truncated: boolean; }
export interface GraphResponse { model: "note-tag-v1"; scope: GraphScope; root: string | null; nodes: GraphNode[]; edges: GraphEdge[]; page: GraphPage; generated_at: string; }
export interface GraphQuery { limit?: number; offset?: number; tag?: string | null; include_broken?: boolean; depth?: number; direction?: "both" | "outgoing" | "incoming"; }
export interface AIContextCitation { path: string; heading: string | null; quote: string; }
export interface AIChatRequest { note_path?: string | null; question: string; context_note_paths?: string[]; }
export interface AIChatResponse { answer: string; citations: AIContextCitation[]; prompt_version: string; model: string; degraded: boolean; }
export interface AISummarizeRequest { note_path: string; }
export interface AISummarizeResponse { note_path: string; summary: string; key_points: string[]; prompt_version: string; model: string; degraded: boolean; }
export interface AITagSuggestion { name: string; reason: string; }
export interface AITagsRequest { note_path: string; }
export interface AITagsResponse { note_path: string; tags: AITagSuggestion[]; prompt_version: string; model: string; degraded: boolean; }
export interface AIRelatedRequest { note_path: string; limit?: number; }
export interface AIRelatedItem { path: string; title: string; reason: string; score: number; }
export interface AIRelatedResponse { note_path: string; related: AIRelatedItem[]; candidates_considered: number; prompt_version: string; model: string | null; degraded: boolean; }
export interface AIExtractTodosRequest { note_path: string; }
export interface AITodoItem { text: string; source_heading: string | null; due_hint: string | null; }
export interface AIExtractTodosResponse { note_path: string; items: AITodoItem[]; prompt_version: string; model: string; degraded: boolean; }
export interface AIClassifyRequest { note_path: string; labels?: string[]; }
export interface AIClassifyResponse { note_path: string; label: string; confidence: number; alternatives: string[]; prompt_version: string; model: string; degraded: boolean; }
export interface AIErrorResponse { error: { code: string; message: string; path: string | null }; meta?: { prompt_version?: string; model?: string }; }

export type ActionType = "add_tags" | "remove_tags" | "add_link" | "create_note" | "patch_note" | "move_note";
export type PermissionLevel = 0 | 1 | 2;
export type JobStatus = "planned" | "preflighted" | "captured" | "awaiting_confirmation" | "executing" | "validating" | "committed" | "rejected" | "rolled_back" | "failed" | "conflict" | "undone" | "undo_unavailable" | "rollback_failed";
export type PolicyDecision = "allow" | "deny" | "confirm";
export interface ActionDTO { action_id: string; action: ActionType; permission_level: PermissionLevel; file: string; target_file?: string | null; tags?: string[]; link_target?: string | null; reason: string; expected_sha256?: string | null; }
export interface PolicyResultDTO { decision: PolicyDecision; level: number; action_type: string; matched_rules: string[]; reasons: string[]; budgets?: Record<string, number>; }
export interface DiffEntryDTO { path: string; action_id?: string; operation: string; before_hash: string | null; after_hash: string | null; before_size: number; after_size: number; unified_diff?: string | null; hunks?: Array<{ old_text?: string; new_text?: string; old_start?: number; new_start?: number }>; status?: "proposed" | "pending" | "accepted" | "rejected"; }
export interface JobSummaryDTO { job_id: string; task_type: string; status: JobStatus; start_time: string | null; end_time?: string | null; model?: string | null; action_count?: number; }
export interface JobErrorInfo { code: string; message: string; [key: string]: unknown; }
export interface JournalSummaryEntryDTO { seq: number; operation: "create" | "update" | "move" | "undo"; path: string; before_exists: boolean; after_exists: boolean; before_hash: string | null; after_hash: string | null; state: "pending" | "applied" | "rolled_back"; }
export interface JobDetailDTO extends JobSummaryDTO { prompt_version?: string | null; files_read: string[]; proposed_actions: ActionDTO[]; executed_actions: ActionDTO[]; diff: DiffEntryDTO[]; before_hash: Record<string, string | null>; after_hash: Record<string, string | null>; policy?: PolicyResultDTO | null; error?: string | JobErrorInfo | null; journal_summary?: JournalSummaryEntryDTO[]; }
export interface HistoryPageDTO { items: JobSummaryDTO[]; total: number; limit: number; offset: number; }
export interface JobCreateRequest { task_type: "daily_organizer" | "weekly_review" | "manual"; permission_level: PermissionLevel; scope: { paths: string[]; max_files?: number; max_chars?: number }; execute?: boolean; }
export interface JobActionRequest { action_ids?: string[]; confirm: boolean; }
export interface UndoResponseDTO { job_id: string; status: "undone"; restored: string[]; error?: string | null; }
// ---------------------------------------------------------------------------
// M8 scheduler DTO mirror (server source of truth: server/scheduler/*)
// ---------------------------------------------------------------------------
export type SchedulerTaskId = "daily_organizer" | "weekly_review" | "index_consistency";
export type SchedulerTrigger = "scheduled" | "manual" | "startup";
export type SchedulerRunStatus = "queued" | "running" | "previewed" | "committed" | "skipped_duplicate" | "failed" | "timed_out" | "recovery_required";
export type SchedulerErrorCode = "scheduler_disabled" | "scheduler_unavailable" | "unknown_task" | "invalid_schedule" | "duplicate_run" | "job_timeout" | "job_in_progress" | "recovery_required" | "recovery_not_safe" | "scheduler_config_invalid" | "history_cleanup_failed" | "index_check_failed" | "network_exposure_warning" | string;
export interface SchedulerJobStatus { id: SchedulerTaskId; enabled: boolean; trigger: "cron" | "interval"; next_run_at: string | null; last_status: SchedulerRunStatus | null; last_run_id?: string | null; last_message?: string | null; last_finished_at?: string | null; }
export interface SchedulerStatusResponse { enabled: boolean; running: boolean; backend: string; degraded: boolean; degraded_reason?: string | null; timezone: string; network_exposure_warning: boolean; network_exposure_advice?: string | null; jobs: SchedulerJobStatus[]; active_runs: number; recovery_required: number; history_retention_days: number; }
export interface SchedulerRunDTO { run_id: string; task: SchedulerTaskId; status: SchedulerRunStatus; trigger: SchedulerTrigger; agent_job_id?: string | null; scheduled_for?: string | null; started_at?: string | null; finished_at?: string | null; policy?: PolicyResultDTO | null; error_code?: string | null; message?: string | null; detail?: Record<string, unknown> | null; }
export interface SchedulerRunsPageDTO { items: SchedulerRunDTO[]; total: number; limit: number; offset: number; }
export interface SchedulerRunRequest { confirm?: boolean; auto_level2?: boolean; scope?: { paths: string[]; max_files?: number; max_chars?: number } | null; }
export type SchedulerRecoveryAction = "diagnose" | "rollback_if_safe" | "retry_preview";

// ---------------------------------------------------------------------------
// M14 RAG DTO mirror (server source of truth: server/rag/api_schemas.py)
// ---------------------------------------------------------------------------
export type RagIndexStatusKind = "empty" | "ready" | "pending" | "failed" | "outdated";
export interface RagSource { id: string; path: string; heading?: string | null; heading_path?: string | null; start_line: number; end_line: number; excerpt: string; score?: number | null; }
export interface RagSearchHit { /** Server-derived visual binding; optional for pre-visual responses. */ rank?: number; source_id?: string; chunk_id: string; path: string; heading?: string | null; heading_path?: string | null; excerpt: string; score: number; keyword_rank?: number | null; vector_rank?: number | null; link_rank?: number | null; rerank_score?: number | null; start_line: number; end_line: number; }
export interface RagRetrievalStats { fts_candidates: number; vector_candidates: number; /** Candidates contributed by optional graph/link retrieval. */ link_candidates?: number; fused_candidates: number; reranked: boolean; context_chunks: number; context_tokens: number; retrieval_ms: number; embedding_ms: number; rerank_ms: number; generation_ms: number; degraded: string[]; retrieval_debug?: Record<string, unknown> | null; }
export interface RagQueryRequest { query: string; top_k?: number | null; rerank?: boolean | null; debug?: boolean; }
export interface RagEvidenceSummary { source_count: number; paths?: string[]; context_tokens?: number; candidate_count?: number; truncated?: boolean; grounded?: boolean; degraded?: string[]; }
export interface RagQueryResponse { query: string; answer: string; sources: RagSource[]; /** Server-derived evidence metadata; absent on older servers. */ evidence?: RagEvidenceSummary; retrieval_stats: RagRetrievalStats; model: string; prompt_version: string; degraded: string[]; invalid_citations: string[]; generated_at: string; }
export interface RagSearchRequest { query: string; top_k?: number; }
export interface RagSearchResponse { query: string; results: RagSearchHit[]; stats: RagRetrievalStats; degraded: string[]; generated_at: string; }
export interface RagIndexStatusResponse { enabled: boolean; status: RagIndexStatusKind; embedding_provider: string; embedding_model: string; embedding_dimension: number; embedding_version: string; embedding_degraded: boolean; vector_store: string; vector_kernel: string; indexed_notes: number; chunks: number; embedded_chunks: number; pending: number; failed: number; last_indexed: string | null; chunk_target_tokens: number; chunk_max_tokens: number; message: string; /**
 * Optional roadmap-③ link/graph path state, relayed verbatim from the retriever:
 * `""` when no link path is wired, `"enabled"` when it is wired and reachable,
 * otherwise the degraded reason (e.g. `"link_unavailable"`). Optional so a
 * pre-③ server (which never sends it) still deserializes cleanly; an empty or
 * absent value renders as "nothing to report" — never as a fabricated state.
 */
link_retrieval?: string; }
export interface RagIndexRebuildResponse { indexed_documents: number; indexed_chunks: number; embedded_chunks: number; skipped_documents: number; failed_documents: number; duration_ms: number; ready: boolean; degraded: boolean; degraded_reason?: string | null; status: RagIndexStatusResponse; }
