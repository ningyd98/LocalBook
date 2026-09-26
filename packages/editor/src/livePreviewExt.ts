/**
 * Live Preview (single-column WYSIWYG) for the Markdown source.
 *
 * Markdown syntax stays in the document — this layer only *decorates* it:
 * heading lines render larger, emphasis/links/code are styled, images are
 * replaced by inline widgets and list/quote markers get their rendered look.
 * The line the caret is on keeps its raw source, so editing a construct is
 * always possible without leaving the view.
 *
 * The document bytes are never touched; the byte-faithful save path is intact.
 */
import { EditorView, Decoration, WidgetType } from "@codemirror/view";
import type { DecorationSet } from "@codemirror/view";
import { syntaxTree } from "@codemirror/language";
import type { EditorState, Extension, Range } from "@codemirror/state";


/** URL resolver for relative image targets (Vault resource endpoint). */
export type LivePreviewResolver = (path: string) => string | null;

export interface LivePreviewOptions {
  /** Build a loadable URL for a Vault-relative image target. */
  resolveResourceUrl?: LivePreviewResolver;
  /** Build a PDF preview URL for a Vault-relative Office/PDF target. */
  resolveDocumentUrl?: LivePreviewResolver;
  /** Vault-relative path of the note being edited. */
  notePath?: string | null;
  /** Click on a rendered link/wikilink. */
  onOpenLink?: (target: string, kind: "link" | "wikilink") => void;
  /** Click on a task checkbox (index is the document-order ordinal). */
  onToggleTask?: (index: number) => void;
  /** Start local transcription for a relative audio target. */
  onTranscribeAudio?: (target: string) => void;
}

const hide = Decoration.replace({});

const HEADING_CLASS: Record<string, string> = {
  ATXHeading1: "cm-lp-h1",
  ATXHeading2: "cm-lp-h2",
  ATXHeading3: "cm-lp-h3",
  ATXHeading4: "cm-lp-h4",
  ATXHeading5: "cm-lp-h5",
  ATXHeading6: "cm-lp-h6",
};

class ImageWidget extends WidgetType {
  constructor(readonly src: string, readonly alt: string) { super(); }
  eq(other: ImageWidget) { return other.src === this.src && other.alt === this.alt; }
  toDOM() {
    const figure = document.createElement("figure");
    figure.className = "cm-lp-image";
    const img = document.createElement("img");
    img.src = this.src;
    img.alt = this.alt;
    img.loading = "lazy";
    figure.appendChild(img);
    if (this.alt) {
      const caption = document.createElement("figcaption");
      caption.textContent = this.alt;
      figure.appendChild(caption);
    }
    return figure;
  }
  ignoreEvent() { return false; }
}

function isAudioReference(value: string): boolean {
  const path = (value.split("#", 1)[0] ?? value).split("?", 1)[0] ?? value;
  return /\.(mp3|wav|m4a|aac|flac|ogg|oga|opus|webm|amr|caf|aiff?|wma)$/i.test(path);
}

function isDocumentReference(value: string): boolean {
  const path = (value.split("#", 1)[0] ?? value).split("?", 1)[0] ?? value;
  return /\.(pdf|doc|docx|docm|dotx|dotm|wps|wpt|ppt|pptx|pptm|pps|ppsx|potx|potm|dps|dpt|xls|xlsx|xlsm|et|ett|odt|ods|odp|ott|otp|ots|rtf|csv)$/i.test(path);
}

class AudioWidget extends WidgetType {
  constructor(readonly src: string, readonly target: string, readonly onTranscribe?: (target: string) => void) { super(); }
  eq(other: AudioWidget) { return other.src === this.src && other.target === this.target; }
  toDOM() {
    const figure = document.createElement("figure");
    figure.className = "cm-lp-audio";
    const audio = document.createElement("audio");
    audio.controls = true;
    audio.preload = "metadata";
    audio.src = this.src;
    figure.appendChild(audio);
    if (this.onTranscribe) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "cm-lp-audio-transcribe";
      button.textContent = "转文字 / Transcribe";
      button.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        this.onTranscribe?.(this.target);
      });
      figure.appendChild(button);
    }
    return figure;
  }
  ignoreEvent() { return false; }
}

/** True when the caret sits on the same line as `pos`. */
/** Task markers in document order, ignoring fenced code blocks. */
function scanTasks(text: string): { from: number; to: number; checked: boolean }[] {
  const found: { from: number; to: number; checked: boolean }[] = [];
  let offset = 0;
  let fence: { marker: string; length: number } | null = null;
  for (const line of text.split("\n")) {
    const fenceMatch = /^\s{0,3}(`{3,}|~{3,})/.exec(line);
    if (fenceMatch) {
      const run = fenceMatch[1]!;
      const marker = run[0]!;
      if (fence === null) fence = { marker, length: run.length };
      else if (fence.marker === marker && run.length >= fence.length) fence = null;
      offset += line.length + 1;
      continue;
    }
    if (!fence) {
      const match = /^([ \t]*(?:[-*+]|\d+[.)])[ \t]+)\[([ xX])\](?=[ \t]|$)/.exec(line);
      if (match) {
        const start = offset + match[1]!.length;
        found.push({ from: start, to: start + 3, checked: match[2]!.toLowerCase() === "x" });
      }
    }
    offset += line.length + 1;
  }
  return found;
}

class DocumentWidget extends WidgetType {
  constructor(readonly src: string, readonly target: string, readonly downloadUrl: string) { super(); }
  eq(other: DocumentWidget) {
    return other.src === this.src && other.target === this.target && other.downloadUrl === this.downloadUrl;
  }
  toDOM() {
    const figure = document.createElement("figure");
    figure.className = "cm-lp-document";
    const frame = document.createElement("iframe");
    frame.src = this.src;
    frame.title = this.target;
    frame.loading = "lazy";
    figure.appendChild(frame);
    const link = document.createElement("a");
    link.href = this.downloadUrl;
    link.download = this.target.split("/").at(-1) ?? this.target;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = "打开 / 下载原文件 · Open / download";
    figure.appendChild(link);
    return figure;
  }
  ignoreEvent() { return false; }
}

class TaskCheckboxWidget extends WidgetType {
  constructor(readonly index: number, readonly checked: boolean) { super(); }
  eq(other: TaskCheckboxWidget) { return other.index === this.index && other.checked === this.checked; }
  toDOM() {
    const box = document.createElement("span");
    box.className = `cm-lp-task${this.checked ? " cm-lp-task-done" : ""}`;
    box.setAttribute("data-task-index", String(this.index));
    box.setAttribute("role", "checkbox");
    box.setAttribute("aria-checked", this.checked ? "true" : "false");
    box.setAttribute("tabindex", "0");
    // A checkbox click must not move the caret: moving it would activate the
    // line, which removes the widget mid-click and swallows the toggle.
    box.addEventListener("mousedown", (event) => { event.preventDefault(); event.stopPropagation(); });
    return box;
  }
  ignoreEvent() { return false; }
}

function lineIsActive(state: EditorState, pos: number): boolean {
  const line = state.doc.lineAt(pos);
  return state.selection.ranges.some((range) => range.from <= line.to && range.to >= line.from);
}

/** Strip the markdown delimiters of an image and return its target + alt. */
type TreeNode = ReturnType<ReturnType<typeof syntaxTree>["resolve"]>;

function imageParts(state: EditorState, node: TreeNode): { src: string; alt: string } | null {
  const text = state.doc.sliceString(node.from, node.to);
  const match = /^!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)$/.exec(text);
  if (!match) return null;
  return { alt: match[1] ?? "", src: match[2] ?? "" };
}

/** `[[target|alias]]` body for a link, when authored as a wikilink. */
function wikilinkTarget(text: string): string | null {
  const match = /^\[\[([^\]]+)\]\]$/.exec(text);
  return match ? match[1]!.split("|")[0]!.split("#")[0]!.trim() : null;
}

/** Syntax nodes whose text must never be interpreted as live-preview markup. */
const CODE_NODES = new Set(["InlineCode", "FencedCode", "CodeBlock", "CodeText", "CodeInfo"]);

/**
 * `<span style="color:#rrggbb">text</span>` — the colour form the editor
 * writes. Only a bare colour declaration is matched, mirroring the preview
 * sanitizer's allow-list, so a hand-written tag with extra CSS is left alone.
 * The body may carry `=`, inline markup and a soft line break (nested spans are
 * not matched: their closing tag would be ambiguous, and the commands never
 * nest them).
 */
const COLOR_SPAN_RE = /<span\s+style="color:\s*(#[0-9a-fA-F]{3,8})"\s*>((?:(?!<\/?span)[\s\S])*?)<\/span>/g;
const CLOSE_TAG = "</span>";

/** True when `at` sits between a `<` and its closing `>`: attribute text, not markup. */
function insideTag(text: string, at: number): boolean {
  return text.lastIndexOf("<", at) > text.lastIndexOf(">", at);
}

/**
 * `==highlight==` delimiter *pairs* in document order.
 *
 * Pairing the delimiters (instead of matching a `[^=]+` body line by line) lets
 * a highlighted run contain `=` and complete inline markup — which is what
 * stacking formats produces: highlighting a coloured run writes
 * `==<span style="color:#e5484d">x</span>==`, whose tag carries `=`.
 */
function scanHighlightPairs(text: string, skip: (from: number, to: number) => boolean): Array<{ open: number; close: number }> {
  const pairs: Array<{ open: number; close: number }> = [];
  const pattern = /==/g;
  let pending = -1;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(text))) {
    const at = match.index;
    if (skip(at, at + 2) || insideTag(text, at)) continue;
    if (pending < 0) { pending = at; continue; }
    // `====` stays an empty pair: the opener shifts to the later `==`.
    if (at === pending + 2) { pending = at; continue; }
    pairs.push({ open: pending, close: at });
    pending = -1;
  }
  return pairs;
}

export function buildDecorations(state: EditorState, options: LivePreviewOptions): DecorationSet {
  const pending: Range<Decoration>[] = [];
  const push = (from: number, to: number, decoration: Decoration) => {
    if (to > from) pending.push(decoration.range(from, to));
  };
  /** Spans the inline scanners must skip (inline code, fenced code). */
  const codeRanges: { from: number; to: number }[] = [];
  const inCode = (from: number, to: number) => codeRanges.some((range) => from < range.to && to > range.from);

  syntaxTree(state).iterate({
    enter: (node) => {
      const { name, from, to } = node;
      // The caret's line stays raw so the syntax can be edited in place.
      const active = lineIsActive(state, from);

      if (CODE_NODES.has(name)) codeRanges.push({ from, to });

      const heading = HEADING_CLASS[name];
      if (heading) {
        if (!active) {
          // A line decoration must cover exactly one line (zero-length range);
          // the mark carries the font size for the text itself.
          const line = state.doc.lineAt(from);
          push(line.from, line.from, Decoration.line({ class: heading }));
          push(from, to, Decoration.mark({ class: `${heading}-text` }));
        }
        return;
      }

      if (name === "HeaderMark" || name === "EmphasisMark" || name === "CodeMark" || name === "QuoteMark" || name === "StrikethroughMark") {
        if (!active) push(from, to, hide);
        return;
      }

      if (name === "StrongEmphasis") { push(from, to, Decoration.mark({ class: "cm-lp-strong" })); return; }
      if (name === "Emphasis") { push(from, to, Decoration.mark({ class: "cm-lp-em" })); return; }
      if (name === "InlineCode") { push(from, to, Decoration.mark({ class: "cm-lp-code" })); return; }
      if (name === "Strikethrough") { push(from, to, Decoration.mark({ class: "cm-lp-strike" })); return; }
      if (name === "HorizontalRule") {
        const line = state.doc.lineAt(from);
        push(line.from, line.from, Decoration.line({ class: "cm-lp-hr" }));
        return;
      }
      if (name === "Blockquote") {
        const line = state.doc.lineAt(from);
        push(line.from, line.from, Decoration.line({ class: "cm-lp-quote" }));
        return;
      }
      if (name === "ListMark") { push(from, to, Decoration.mark({ class: "cm-lp-listmark" })); return; }


      if (name === "Image") {
        if (active) return;
        const parts = imageParts(state, node.node);
        if (!parts) return;
        const resolved = options.resolveResourceUrl?.(parts.src) ?? null;
        if (!resolved) return;
        let documentUrl: string | null = null;
        if (isDocumentReference(parts.src) && options.resolveDocumentUrl) {
          try {
            documentUrl = options.resolveDocumentUrl(parts.src);
          } catch {
            documentUrl = null;
          }
        }
        const widget = isAudioReference(parts.src)
          ? new AudioWidget(resolved, parts.src, options.onTranscribeAudio)
          : documentUrl
            ? new DocumentWidget(documentUrl, parts.src, resolved)
            : new ImageWidget(resolved, parts.alt);
        push(from, to, Decoration.replace({ widget }));
        return;
      }

      if (name === "Link") {
        const raw = state.doc.sliceString(from, to);
        const wiki = wikilinkTarget(raw);
        if (wiki) {
          push(from, to, Decoration.mark({
            class: "cm-lp-link cm-lp-wikilink",
            attributes: { "data-href": `wikilink:${encodeURIComponent(wiki)}`, role: "link", tabindex: "0" },
          }));
          return;
        }
        const href = /\]\(([^)\s]+)/.exec(raw)?.[1];
        if (!active && href && isAudioReference(href)) {
          const resolved = options.resolveResourceUrl?.(href) ?? null;
          if (resolved) {
            push(from, to, Decoration.replace({ widget: new AudioWidget(resolved, href, options.onTranscribeAudio) }));
            return;
          }
        }
        if (!active && href && isDocumentReference(href)) {
          const resolved = options.resolveResourceUrl?.(href) ?? null;
          const preview = options.resolveDocumentUrl?.(href) ?? null;
          if (resolved && preview) {
            push(from, to, Decoration.replace({ widget: new DocumentWidget(preview, href, resolved) }));
            return;
          }
        }
        push(from, to, Decoration.mark({
          class: "cm-lp-link",
          attributes: href ? { "data-href": href, role: "link", tabindex: "0" } : {},
        }));
        return;
      }
      if (name === "URL") {
        // The URL text itself is hidden; the visible label is the link text.
        if (!active) push(from, to, hide);
        return;
      }
      if (name === "LinkMark") {
        if (!active) push(from, to, hide);
        return;
      }
    },
  });

  // Task markers come from the scanner (the base Markdown parser has no GFM
  // TaskList node): the marker text is replaced by a clickable checkbox while
  // the caret is elsewhere, and stays raw on the active line.
  for (const [index, task] of scanTasks(state.doc.toString()).entries()) {
    if (lineIsActive(state, task.from)) continue;
    push(task.from, task.to, Decoration.replace({ widget: new TaskCheckboxWidget(index, task.checked) }));
  }

  // Highlight and colour are not part of the Markdown grammar, so they are
  // scanned over the whole document: a pair may contain `=` and complete inline
  // markup (stacked formats), and a colour span may wrap a soft line break. A
  // construct the caret or selection touches stays raw so it remains editable.
  const text = state.doc.toString();
  for (const pair of scanHighlightPairs(text, inCode)) {
    if (lineIsActive(state, pair.open) || lineIsActive(state, pair.close)) continue;
    push(pair.open, pair.open + 2, hide);
    push(pair.close, pair.close + 2, hide);
    push(pair.open + 2, pair.close, Decoration.mark({ class: "cm-lp-highlight" }));
  }

  COLOR_SPAN_RE.lastIndex = 0;
  let colored: RegExpExecArray | null;
  while ((colored = COLOR_SPAN_RE.exec(text))) {
    const start = colored.index;
    const openEnd = start + colored[0].indexOf(">") + 1;
    const closeStart = start + colored[0].length - CLOSE_TAG.length;
    if (inCode(start, openEnd) || inCode(closeStart, closeStart + CLOSE_TAG.length)) continue;
    if (lineIsActive(state, start) || lineIsActive(state, closeStart)) continue;
    push(start, openEnd, hide);
    push(closeStart, closeStart + CLOSE_TAG.length, hide);
    push(openEnd, closeStart, Decoration.mark({ attributes: { style: `color:${colored[1]!.toLowerCase()}` } }));
  }

  // ``Decoration.set(..., true)`` sorts by the canonical range key, which
  // handles overlapping marks/replacements at the same offset correctly.
  return Decoration.set(pending, true);
}

/** Click handling for rendered links/images (delegated on the editor DOM). */
function clickHandler(options: LivePreviewOptions) {
  return EditorView.domEventHandlers({
    click: (event, view) => {
      const target = event.target as HTMLElement | null;
      const anchor = target?.closest?.(".cm-lp-link") as HTMLElement | null;
      if (anchor) {
        const href = anchor.getAttribute("data-href");
        if (!href) return false;
        event.preventDefault();
        if (href.startsWith("wikilink:")) options.onOpenLink?.(decodeURIComponent(href.slice("wikilink:".length)), "wikilink");
        else if (/^[a-z][a-z0-9+.-]*:\/\//i.test(href)) window.open(href, "_blank", "noreferrer");
        else options.onOpenLink?.(href, "link");
        return true;
      }
      const task = target?.closest?.(".cm-lp-task") as HTMLElement | null;
      if (task) {
        event.preventDefault();
        const index = Number(task.getAttribute("data-task-index"));
        if (Number.isInteger(index) && index >= 0) options.onToggleTask?.(index);
        return true;
      }
      const image = target?.closest?.("figure.cm-lp-image");
      if (image) {
        // A click on an image places the caret on its source line so the raw
        // syntax becomes editable again.
        const pos = view.posAtDOM(image);
        view.dispatch({ selection: { anchor: pos } });
        view.focus();
        return true;
      }
      return false;
    },
  });
}

/**
 * Live preview extension. `options` is re-read on every recomputation, so the
 * resolver/note path may change without rebuilding the editor.
 */
export function livePreview(options: () => LivePreviewOptions): Extension {
  return [
    EditorView.baseTheme({
      ".cm-lp-h1-text": { fontSize: "1.55em", fontWeight: "650", lineHeight: "1.5" },
      ".cm-lp-h2-text": { fontSize: "1.32em", fontWeight: "650", lineHeight: "1.5" },
      ".cm-lp-h3-text": { fontSize: "1.18em", fontWeight: "600" },
      ".cm-lp-h4-text": { fontSize: "1.08em", fontWeight: "600" },
      ".cm-lp-h5-text": { fontSize: "1.02em", fontWeight: "600" },
      ".cm-lp-h6-text": { fontSize: "0.98em", fontWeight: "600", color: "var(--muted)" },
      ".cm-lp-h1": { paddingTop: "10px" },
      ".cm-lp-h2": { paddingTop: "8px" },
      ".cm-lp-h3, .cm-lp-h4, .cm-lp-h5, .cm-lp-h6": { paddingTop: "6px" },
      ".cm-lp-strong": { fontWeight: "650" },
      ".cm-lp-em": { fontStyle: "italic" },
      ".cm-lp-strike": { textDecoration: "line-through", opacity: ".75" },
      ".cm-lp-code": { background: "var(--code)", borderRadius: "4px", padding: "1px 4px", fontSize: ".92em" },
      ".cm-lp-highlight": { background: "var(--highlight, #f2d675)", color: "var(--highlight-fg, inherit)", borderRadius: "3px", padding: "1px 2px" },
      ".cm-lp-link": { color: "var(--accent)", cursor: "pointer" },
      ".cm-lp-wikilink": { borderBottom: "1px dashed currentColor" },
      ".cm-lp-listmark": { color: "var(--accent)", fontWeight: "600" },
      ".cm-lp-quote": { borderLeft: "3px solid var(--accent)", paddingLeft: "12px", color: "var(--muted)" },
      ".cm-lp-hr": { borderBottom: "1px solid var(--border)" },
      ".cm-lp-task": { display: "inline-block", width: "13px", height: "13px", marginRight: "6px", verticalAlign: "middle", border: "1.5px solid var(--subtle, #888)", borderRadius: "4px", cursor: "pointer", position: "relative" },
      ".cm-lp-task-done": { background: "var(--accent, #7c6cff)", borderColor: "var(--accent, #7c6cff)" },
      ".cm-lp-task-done:after": { content: "''", position: "absolute", left: "3px", top: "0px", width: "5px", height: "9px", border: "solid #fff", borderWidth: "0 2px 2px 0", transform: "rotate(45deg)" },
      ".cm-lp-image": { margin: "6px 0", display: "flex", flexDirection: "column", gap: "4px" },
      ".cm-lp-image img": { maxWidth: "100%", borderRadius: "7px", border: "1px solid var(--border)" },
      ".cm-lp-image figcaption": { fontSize: "11px", color: "var(--subtle)" },
      ".cm-lp-audio": { margin: "6px 0", display: "flex", flexDirection: "column", gap: "6px" },
      ".cm-lp-audio audio": { maxWidth: "100%" },
      ".cm-lp-audio-transcribe": { alignSelf: "flex-start", cursor: "pointer" },
    }),
    clickHandler(options()),
    EditorView.decorations.compute(["doc", "selection"], (state) => buildDecorations(state, options())),
  ];
}
