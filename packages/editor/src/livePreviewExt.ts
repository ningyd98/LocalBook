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
import { RangeSetBuilder } from "@codemirror/state";
import type { EditorState, Extension, Range } from "@codemirror/state";


/** URL resolver for relative image targets (Vault resource endpoint). */
export type LivePreviewResolver = (path: string) => string | null;

export interface LivePreviewOptions {
  /** Build a loadable URL for a Vault-relative image target. */
  resolveResourceUrl?: LivePreviewResolver;
  /** Vault-relative path of the note being edited. */
  notePath?: string | null;
  /** Click on a rendered link/wikilink. */
  onOpenLink?: (target: string, kind: "link" | "wikilink") => void;
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

/** True when the caret sits on the same line as `pos`. */
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

export function buildDecorations(state: EditorState, options: LivePreviewOptions): DecorationSet {
  const pending: Range<Decoration>[] = [];
  const push = (from: number, to: number, decoration: Decoration) => {
    if (to > from) pending.push(decoration.range(from, to));
  };

  syntaxTree(state).iterate({
    enter: (node) => {
      const { name, from, to } = node;
      // The caret's line stays raw so the syntax can be edited in place.
      const active = lineIsActive(state, from);

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
        push(from, to, Decoration.replace({ widget: new ImageWidget(resolved, parts.alt) }));
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

  pending.sort((a, b) => a.from - b.from || a.to - b.to);
  const builder = new RangeSetBuilder<Decoration>();
  for (const range of pending) builder.add(range.from, range.to, range.value);
  return builder.finish();
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
      ".cm-lp-link": { color: "var(--accent)", cursor: "pointer" },
      ".cm-lp-wikilink": { borderBottom: "1px dashed currentColor" },
      ".cm-lp-listmark": { color: "var(--accent)", fontWeight: "600" },
      ".cm-lp-quote": { borderLeft: "3px solid var(--accent)", paddingLeft: "12px", color: "var(--muted)" },
      ".cm-lp-hr": { borderBottom: "1px solid var(--border)" },
      ".cm-lp-image": { margin: "6px 0", display: "flex", flexDirection: "column", gap: "4px" },
      ".cm-lp-image img": { maxWidth: "100%", borderRadius: "7px", border: "1px solid var(--border)" },
      ".cm-lp-image figcaption": { fontSize: "11px", color: "var(--subtle)" },
    }),
    clickHandler(options()),
    EditorView.decorations.compute(["doc", "selection"], (state) => buildDecorations(state, options())),
  ];
}
