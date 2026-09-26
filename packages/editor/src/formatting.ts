/**
 * Inline formatting commands for the Markdown editor.
 *
 * Every command rewrites the *source* (the document stays plain Markdown), the
 * same rule the rest of the app follows: the file on disk is always the
 * authored bytes, and rendering is a separate, purely visual layer.
 *
 * Conventions:
 *
 *  - bold / italic / highlight are delimiter pairs (`**`, `*`, `==`); wrapping
 *    a selection that already carries the pair removes it again (a toggle);
 *  - colours are inline HTML (`<span style="color:#rrggbb">…</span>`), the one
 *    form Markdown has no syntax for. The preview sanitizer only lets a bare
 *    colour declaration through, so the produced markup is safe by construction;
 *  - tables are GFM pipe tables, inserted as their own block.
 */
import { EditorSelection } from "@codemirror/state";
import type { SelectionRange, Text } from "@codemirror/state";
import type { EditorView } from "@codemirror/view";

/** One entry of the colour palette offered by the toolbar and context menu. */
export interface TextColorOption {
  /** Stable id (also the i18n key suffix). */
  id: string;
  /** Colour written into the document. */
  hex: string;
}

/**
 * Palette shared by the toolbar picker and the right-click menu. The hues are
 * readable on both the light and the dark surface.
 */
export const TEXT_COLORS: TextColorOption[] = [
  { id: "red", hex: "#e5484d" },
  { id: "orange", hex: "#e8890c" },
  { id: "yellow", hex: "#d4a017" },
  { id: "green", hex: "#2f9e63" },
  { id: "blue", hex: "#3b7dd8" },
  { id: "purple", hex: "#8e5bd9" },
  { id: "gray", hex: "#8a8a99" },
];

/** `<span style="color:#rrggbb">` with a tightly constrained, sanitizer-safe body. */
const COLOR_OPEN = /^<span\s+style="color:\s*(#[0-9a-fA-F]{3,8})"\s*>$/;
const COLOR_OPEN_PREFIX = /<span\s+style="color:\s*(#[0-9a-fA-F]{3,8})"\s*>$/;
/** A complete colour span, captured so "clear formatting" can unwrap it. */
const COLOR_SPAN_WRAPPER = /<span\s+style="color:\s*#[0-9a-fA-F]{3,8}"\s*>([\s\S]*?)<\/span>/g;
const COLOR_SPAN = /^<span\s+style="color:\s*#[0-9a-fA-F]{3,8}"\s*>[\s\S]*<\/span>$/;
const CLOSE_TAG = "</span>";

/**
 * Wrap the selection in `open`/`close`, or unwrap it when it is already
 * wrapped. An empty selection inserts the pair and leaves the caret between
 * the two halves, ready for typing.
 */
export function toggleWrap(view: EditorView, open: string, close: string): boolean {
  const { state } = view;
  const range = state.selection.main;
  const selected = state.doc.sliceString(range.from, range.to);

  // (0) A selection that spans lines is formatted line by line: neither
  // `==highlight==` nor emphasis is rendered across a line break, so a single
  // block-wide pair would simply stop rendering.
  if (selected.includes("\n")) return toggleWrapLines(view, open, close, range);

  // (1) The selection itself carries the delimiters: strip them.
  if (selected.length >= open.length + close.length && selected.startsWith(open) && selected.endsWith(close)) {
    const inner = selected.slice(open.length, selected.length - close.length);
    view.dispatch({
      changes: { from: range.from, to: range.to, insert: inner },
      selection: EditorSelection.range(range.from, range.from + inner.length),
    });
    return true;
  }

  const before = state.doc.sliceString(Math.max(0, range.from - open.length), range.from);
  const after = state.doc.sliceString(range.to, Math.min(state.doc.length, range.to + close.length));

  // (2) The delimiters sit just outside the selection: remove both.
  if (before === open && after === close) {
    view.dispatch({
      changes: [
        { from: range.from - open.length, to: range.from, insert: "" },
        { from: range.to, to: range.to + close.length, insert: "" },
      ],
      selection: range.empty
        ? EditorSelection.cursor(range.from - open.length)
        : EditorSelection.range(range.from - open.length, range.to - open.length),
    });
    return true;
  }

  // (3) No selection, but the caret sits inside a pair: remove that pair.
  // This is what makes a second Ctrl-B inside `**bold**` un-bold the word
  // instead of nesting a new pair.
  if (range.empty && open === close) {
    const line = state.doc.lineAt(range.from);
    const pair = enclosingPair(line.text, range.from - line.from, open);
    if (pair) {
      const from = line.from + pair.open;
      const to = line.from + pair.close + open.length;
      const inner = state.doc.sliceString(from + open.length, line.from + pair.close);
      view.dispatch({
        changes: { from, to, insert: inner },
        selection: EditorSelection.cursor(Math.max(from, range.from - open.length)),
      });
      return true;
    }
  }

  // (4) Plain wrap. With no selection the caret lands inside the pair. A
  // selection that is exactly one colour span keeps the delimiters *inside*
  // the span, so stacked formats stay readable for every scanner.
  const insert = toggleSlice(selected, open, close).insert;
  view.dispatch({
    changes: { from: range.from, to: range.to, insert },
    selection: range.empty
      ? EditorSelection.cursor(range.from + open.length)
      : EditorSelection.range(range.from, range.from + insert.length),
  });
  return true;
}

/**
 * Line-by-line counterpart of {@link toggleWrap} for a selection that spans
 * lines: every non-empty line slice gets its own pair. When *all* slices
 * already carry the pair the command unwraps them again — the same toggle the
 * single-line case has.
 */
function toggleWrapLines(view: EditorView, open: string, close: string, range: SelectionRange): boolean {
  const { state } = view;
  const segments = lineSegments(state.doc, range.from, range.to);
  if (!segments.length) return false;
  const slices = segments.map((segment) => toggleSlice(state.doc.sliceString(segment.from, segment.to), open, close));
  const unwrapAll = slices.every((slice) => slice.carries);
  const changes: Array<{ from: number; to: number; insert: string }> = [];
  segments.forEach((segment, index) => {
    const slice = slices[index]!;
    if (unwrapAll || !slice.carries) changes.push({ from: segment.from, to: segment.to, insert: slice.insert });
  });
  if (!changes.length) return false;
  view.dispatch({ changes, selection: selectionOfChanges(changes), scrollIntoView: true });
  return true;
}

/**
 * Toggle one single-line slice, tolerant of a colour span around the
 * delimiters (`<span style="color:#…">**a**</span>`): that nesting is what the
 * commands themselves write, so re-applying the format has to strip the pair
 * from inside the span instead of nesting a second one.
 */
function toggleSlice(text: string, open: string, close: string): { insert: string; carries: boolean } {
  if (carriesPair(text, open, close)) {
    return { insert: text.slice(open.length, text.length - close.length), carries: true };
  }
  if (COLOR_SPAN.test(text)) {
    const openEnd = text.indexOf(">") + 1;
    const inner = text.slice(openEnd, text.length - CLOSE_TAG.length);
    if (carriesPair(inner, open, close)) {
      return {
        insert: `${text.slice(0, openEnd)}${inner.slice(open.length, inner.length - close.length)}${CLOSE_TAG}`,
        carries: true,
      };
    }
    return { insert: `${text.slice(0, openEnd)}${open}${inner}${close}${CLOSE_TAG}`, carries: false };
  }
  return { insert: `${open}${bodyOf(text, open, close)}${close}`, carries: false };
}

/**
 * Drop a stray delimiter half left by a selection that used to span lines
 * (`==alpha` … `beta==`), so re-applying the format repairs it into
 * `==alpha==` … `==beta==` instead of stacking up an empty `====` pair.
 * A delimiter followed (or preceded) by whitespace is kept: `* item` is a list
 * marker, not an orphaned italic marker.
 */
function bodyOf(text: string, open: string, close: string): string {
  if (text.startsWith(open) && !text.endsWith(close)) {
    const rest = text.slice(open.length);
    if (rest && !/^\s/.test(rest)) return rest;
  }
  if (text.endsWith(close) && !text.startsWith(open)) {
    const head = text.slice(0, text.length - close.length);
    if (head && !/\s$/.test(head)) return head;
  }
  return text;
}

/** True when `text` is exactly `open … close`. */
function carriesPair(text: string, open: string, close: string): boolean {
  return text.length >= open.length + close.length && text.startsWith(open) && text.endsWith(close);
}

/** Non-empty per-line slices of `[from, to)`: markup never spans a line break. */
function lineSegments(doc: Text, from: number, to: number): Array<{ from: number; to: number }> {
  const segments: Array<{ from: number; to: number }> = [];
  let pos = from;
  while (pos < to) {
    const line = doc.lineAt(pos);
    const end = Math.min(to, line.to);
    if (end > pos) segments.push({ from: pos, to: end });
    if (line.to >= to) break;
    pos = line.to + 1;
  }
  return segments;
}

/** Selection covering every edit, in post-change coordinates. */
function selectionOfChanges(changes: Array<{ from: number; to: number; insert: string }>): { anchor: number; head: number } {
  let delta = 0;
  let from = changes[0]!.from;
  let to = changes[0]!.from + changes[0]!.insert.length;
  for (const change of changes) {
    const start = change.from + delta;
    const end = start + change.insert.length;
    from = Math.min(from, start);
    to = Math.max(to, end);
    delta += change.insert.length - (change.to - change.from);
  }
  return { anchor: from, head: to };
}

/**
 * The `marker … marker` pair the caret sits inside on its own line.
 *
 * A line holds an odd number of markers before the caret exactly when the
 * caret is inside an unclosed pair, which is what makes this safe on a line
 * like `**a** and **b**` (two markers before the caret ⇒ not inside one).
 */
function enclosingPair(lineText: string, offset: number, marker: string): { open: number; close: number } | null {
  let open = -1;
  let count = 0;
  let index = lineText.indexOf(marker);
  while (index !== -1 && index < offset) {
    count += 1;
    open = index;
    index = lineText.indexOf(marker, index + marker.length);
  }
  if (count % 2 === 0 || open < 0 || index === -1) return null;
  return { open, close: index };
}

/** `**bold**`. */
export function toggleBold(view: EditorView): boolean {
  return toggleWrap(view, "**", "**");
}

/** `*italic*`. */
export function toggleItalic(view: EditorView): boolean {
  return toggleWrap(view, "*", "*");
}

/** `==highlight==` (rendered as `<mark>` by the preview). */
export function toggleHighlight(view: EditorView): boolean {
  return toggleWrap(view, "==", "==");
}

/**
 * Apply a text colour to the selection.
 *
 * The enclosing span is *recoloured* rather than nested when the selection is
 * already coloured, and choosing the colour it already has removes the span
 * (the same toggle behaviour as bold/highlight).
 */
export function applyTextColor(view: EditorView, color: string): boolean {
  const hex = normalizeColor(color);
  if (!hex) return false;
  const open = `<span style="color:${hex}">`;
  const { state } = view;
  const range = state.selection.main;
  const selected = state.doc.sliceString(range.from, range.to);

  // A multi-line selection is coloured line by line — the same rule the
  // delimiter commands follow, so the live preview can read the result back.
  if (selected.includes("\n")) return applyTextColorLines(view, hex, range);

  // (1) The selection is exactly one colour span: recolour or drop it.
  if (COLOR_SPAN.test(selected)) {
    const openEnd = selected.indexOf(">") + 1;
    const current = COLOR_OPEN.exec(selected.slice(0, openEnd))?.[1]?.toLowerCase();
    const inner = selected.slice(openEnd, selected.length - CLOSE_TAG.length);
    if (current === hex) {
      view.dispatch({
        changes: { from: range.from, to: range.to, insert: inner },
        selection: EditorSelection.range(range.from, range.from + inner.length),
      });
    } else {
      const insert = `${open}${inner}${CLOSE_TAG}`;
      view.dispatch({
        changes: { from: range.from, to: range.to, insert },
        selection: EditorSelection.range(range.from, range.from + insert.length),
      });
    }
    return true;
  }

  const before = state.doc.sliceString(Math.max(0, range.from - 64), range.from);
  const after = state.doc.sliceString(range.to, Math.min(state.doc.length, range.to + CLOSE_TAG.length));
  const enclosing = COLOR_OPEN_PREFIX.exec(before);

  // (2) The selection sits inside a colour span: swap the opening tag.
  if (enclosing && after === CLOSE_TAG) {
    const current = enclosing[1]!.toLowerCase();
    if (current === hex) {
      // Same colour ⇒ toggle the span off around the selection.
      view.dispatch({
        changes: [
          { from: range.from - enclosing[0].length, to: range.from, insert: "" },
          { from: range.to, to: range.to + CLOSE_TAG.length, insert: "" },
        ],
        selection: range.empty
          ? EditorSelection.cursor(range.from - enclosing[0].length)
          : EditorSelection.range(range.from - enclosing[0].length, range.to - enclosing[0].length),
      });
      return true;
    }
    const delta = open.length - enclosing[0].length;
    view.dispatch({
      changes: { from: range.from - enclosing[0].length, to: range.from, insert: open },
      selection: EditorSelection.range(range.from + delta, range.to + delta),
    });
    return true;
  }

  // (3) Plain wrap; an empty selection leaves the caret inside the span.
  const insert = `${open}${selected}${CLOSE_TAG}`;
  view.dispatch({
    changes: { from: range.from, to: range.to, insert },
    selection: range.empty
      ? EditorSelection.cursor(range.from + open.length)
      : EditorSelection.range(range.from, range.from + insert.length),
  });
  return true;
}

/** Line-by-line counterpart of {@link applyTextColor} for a multi-line selection. */
function applyTextColorLines(view: EditorView, hex: string, range: SelectionRange): boolean {
  const { state } = view;
  const segments = lineSegments(state.doc, range.from, range.to);
  if (!segments.length) return false;
  const slices = segments.map((segment) => colorSlice(state.doc.sliceString(segment.from, segment.to), hex));
  // Every slice already carries this colour ⇒ the command removes it again.
  const unwrapAll = slices.every((slice) => slice.kind === "same");
  const changes: Array<{ from: number; to: number; insert: string }> = [];
  segments.forEach((segment, index) => {
    const slice = slices[index]!;
    if (unwrapAll || slice.kind !== "same") changes.push({ from: segment.from, to: segment.to, insert: slice.insert });
  });
  if (!changes.length) return false;
  view.dispatch({ changes, selection: selectionOfChanges(changes), scrollIntoView: true });
  return true;
}

/** How one single-line slice relates to the colour being applied. */
function colorSlice(text: string, hex: string): { kind: "same" | "other" | "plain"; insert: string } {
  if (COLOR_SPAN.test(text)) {
    const openEnd = text.indexOf(">") + 1;
    const current = COLOR_OPEN.exec(text.slice(0, openEnd))?.[1]?.toLowerCase();
    const inner = text.slice(openEnd, text.length - CLOSE_TAG.length);
    if (current === hex) return { kind: "same", insert: inner };
    return { kind: "other", insert: `<span style="color:${hex}">${inner}</span>` };
  }
  return { kind: "plain", insert: `<span style="color:${hex}">${text}</span>` };
}

/**
 * Strip the inline formatting this module creates from the selection. With an
 * empty selection, a pair that immediately surrounds the caret is removed
 * instead, so the command also works right after applying a format.
 */
export function clearFormatting(view: EditorView): boolean {
  const { state } = view;
  const range = state.selection.main;

  if (range.empty) {
    return removeEnclosing(state.doc, view, range.from);
  }

  const selected = state.doc.sliceString(range.from, range.to);
  // Unwrap only *complete* colour spans: a stray closing tag that belongs to
  // some other construct the user authored is left exactly as it is.
  const stripped = selected.replace(COLOR_SPAN_WRAPPER, "$1").replace(/\*\*/g, "").replace(/(?<!\*)\*(?!\*)/g, "").replace(/==/g, "");
  if (stripped === selected) return false;
  view.dispatch({
    changes: { from: range.from, to: range.to, insert: stripped },
    selection: EditorSelection.range(range.from, range.from + stripped.length),
  });
  return true;
}

/** Remove a delimiter pair or colour span that directly surrounds `pos`. */
function removeEnclosing(doc: Text, view: EditorView, pos: number): boolean {
  for (const [open, close] of [["**", "**"], ["*", "*"], ["==", "=="]] as const) {
    const before = doc.sliceString(Math.max(0, pos - open.length), pos);
    const after = doc.sliceString(pos, Math.min(doc.length, pos + close.length));
    if (before === open && after === close) {
      view.dispatch({
        changes: [
          { from: pos - open.length, to: pos, insert: "" },
          { from: pos, to: pos + close.length, insert: "" },
        ],
        selection: EditorSelection.cursor(pos - open.length),
      });
      return true;
    }
  }
  const before = doc.sliceString(Math.max(0, pos - 64), pos);
  const after = doc.sliceString(pos, Math.min(doc.length, pos + CLOSE_TAG.length));
  const enclosing = COLOR_OPEN_PREFIX.exec(before);
  if (enclosing && after === CLOSE_TAG) {
    view.dispatch({
      changes: [
        { from: pos - enclosing[0].length, to: pos, insert: "" },
        { from: pos, to: pos + CLOSE_TAG.length, insert: "" },
      ],
      selection: EditorSelection.cursor(pos - enclosing[0].length),
    });
    return true;
  }
  return false;
}

/** Normalise an authored colour to `#rrggbb` (or `#rgb`); `null` when unusable. */
export function normalizeColor(color: string): string | null {
  const value = color.trim().toLowerCase();
  return /^#[0-9a-f]{6}$/.test(value) || /^#[0-9a-f]{3}$/.test(value) ? value : null;
}

/** Lower/upper bounds keep a menu mis-click from inserting an absurd table. */
export const TABLE_MAX_ROWS = 12;
export const TABLE_MAX_COLUMNS = 8;
/** `rows` includes the header row; `columns` is at least 1. */
export const DEFAULT_TABLE = { rows: 3, columns: 3 } as const;

/**
 * Build a GFM pipe table. The header cells are empty on purpose: the caret is
 * placed in the first one, so typing immediately fills the table instead of
 * leaving placeholder text behind.
 */
export function buildTableMarkdown(rows: number, columns: number): string {
  const rowCount = clamp(rows, 1, TABLE_MAX_ROWS);
  const columnCount = clamp(columns, 1, TABLE_MAX_COLUMNS);
  const cell = (value: string) => `| ${value} `;
  const lines: string[] = [];
  lines.push(`${Array.from({ length: columnCount }, () => cell("")).join("")}|`);
  lines.push(`${Array.from({ length: columnCount }, () => cell("---")).join("")}|`);
  for (let row = 1; row < rowCount; row += 1) {
    lines.push(`${Array.from({ length: columnCount }, () => cell("")).join("")}|`);
  }
  return lines.join("\n");
}

/**
 * Insert a table as its own block at the caret (replacing a selection), and
 * put the caret in the first header cell. `rows` counts the header row.
 */
export function insertTable(view: EditorView, rows: number, columns: number): boolean {
  const { state } = view;
  const range = state.selection.main;
  const line = state.doc.lineAt(range.to);
  // Never merge into surrounding prose: the same paragraph rule the attachment
  // insert uses.
  const lead = line.from === 0 && line.length === 0 ? "" : range.to === line.from ? "" : "\n";
  const table = buildTableMarkdown(rows, columns);
  const snippet = `${lead}${table}\n`;
  view.dispatch({
    changes: { from: range.from, to: range.to, insert: snippet },
    // "| " (2 chars) after the lead lands inside the first header cell.
    selection: EditorSelection.cursor(range.from + lead.length + 2),
    scrollIntoView: true,
  });
  return true;
}

function clamp(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  return Math.max(min, Math.min(max, Math.trunc(value)));
}
