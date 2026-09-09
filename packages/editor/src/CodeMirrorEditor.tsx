import { useEffect, useImperativeHandle, useRef } from "react";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { markdown } from "@codemirror/lang-markdown";
import { Compartment, EditorState } from "@codemirror/state";
import { oneDark } from "@codemirror/theme-one-dark";
import { EditorView, keymap } from "@codemirror/view";
import type { ForwardedRef, ReactNode } from "react";
import { livePreview } from "./livePreviewExt";
import type { LivePreviewOptions } from "./livePreviewExt";

export interface CodeMirrorEditorHandle {
  /** Insert text at the current selection and place the caret after it. */
  insertAtSelection: (text: string) => boolean;
  /** Toggle a heading level (0 = paragraph) on the selected lines. */
  setHeading: (level: number) => boolean;
  focus: () => void;
}

/**
 * Toggle an ATX heading on every line the selection touches.
 *
 * ``level === 0`` removes the heading (paragraph). Applying the level a line
 * already has toggles it off, so Ctrl-1 twice returns to plain text. Only
 * leading ``#`` markers are rewritten; the line text itself is never altered.
 */
export function setHeadingLevel(level: number) {
  return (view: EditorView): boolean => {
    const { state } = view;
    const changes: { from: number; to: number; insert: string }[] = [];
    const firstLine = state.doc.lineAt(state.selection.main.from).number;
    const lastLine = state.doc.lineAt(state.selection.main.to).number;
    for (let number = firstLine; number <= lastLine; number += 1) {
      const line = state.doc.line(number);
      const match = /^(#{1,6})\s+/.exec(line.text);
      const current = match ? match[1]!.length : 0;
      const next = level === current ? 0 : level;
      const stripped = match ? line.text.slice(match[0].length) : line.text;
      const insert = next === 0 ? stripped : `${"#".repeat(next)} ${stripped}`;
      if (insert !== line.text) changes.push({ from: line.from, to: line.to, insert });
    }
    if (!changes.length) return false;
    view.dispatch({ changes });
    return true;
  };
}

/**
 * Ctrl-1…6 set a heading, Ctrl-0 clears it.
 *
 * Both `Ctrl-` and `Mod-` are bound on purpose: CodeMirror maps `Mod-` to Cmd
 * on macOS, and the browser reserves Cmd-1…9 for tab switching, so `Ctrl-` is
 * the shortcut that actually reaches the editor there.
 */
const headingKeymap = keymap.of(
  Array.from({ length: 6 }, (_, index) => index + 1).flatMap((level) => [
    { key: `Ctrl-${level}`, run: setHeadingLevel(level) },
    { key: `Mod-${level}`, run: setHeadingLevel(level) },
  ]).concat([
    { key: "Ctrl-0", run: setHeadingLevel(0) },
    { key: "Mod-0", run: setHeadingLevel(0) },
  ]),
);

export interface CodeMirrorEditorProps {
  value: string;
  onChange: (value: string) => void;
  onSave?: () => void;
  theme: "light" | "dark";
  readOnly?: boolean;
  ariaLabel?: string;
  className?: string;
  /** Files dropped onto the editor surface (already filtered to non-empty). */
  onDropFiles?: (files: File[]) => void;
  /** Image blobs pasted from the clipboard. Text paste is never intercepted. */
  onPasteImages?: (files: File[]) => void;
  onDragOver?: (event: DragEvent) => void;
  onDragLeave?: (event: DragEvent) => void;
  toolbar?: ReactNode;
  handleRef?: ForwardedRef<CodeMirrorEditorHandle>;
  /** Registers a caret-insert handler for this note while the editor is mounted. */
  onRegisterCaretInsert?: (handler: ((markdown: string) => boolean) | null) => void;
  /** Single-column WYSIWYG rendering (Obsidian-style live preview). */
  livePreview?: LivePreviewOptions | null;
}

const lightTheme = EditorView.theme({
  "&": { height: "100%", fontSize: "14px", backgroundColor: "transparent" },
  ".cm-scroller": { overflow: "auto", fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" },
  ".cm-content": { padding: "16px", caretColor: "currentColor" },
  ".cm-focused": { outline: "2px solid #6366f1", outlineOffset: "-2px" },
});

/**
 * Smallest single-span edit turning `doc` into `value` (common prefix/suffix).
 * Used to apply an external value without clobbering the editor selection.
 */
function minimalChange(doc: string, value: string): { from: number; to: number; insert: string } {
  const max = Math.min(doc.length, value.length);
  let from = 0;
  while (from < max && doc[from] === value[from]) from += 1;
  let end = 0;
  while (end < max - from && doc[doc.length - 1 - end] === value[value.length - 1 - end]) end += 1;
  return { from, to: doc.length - end, insert: value.slice(from, value.length - end) };
}

/** Clipboard/drop payloads expose files only through these two channels. */function filesFromDataTransfer(data: DataTransfer | null, filter?: (file: File) => boolean): File[] {
  if (!data) return [];
  const files = Array.from(data.files ?? []);
  const resolved = files.length
    ? files
    : Array.from(data.items ?? [])
        .filter((item) => item.kind === "file")
        .map((item) => item.getAsFile())
        .filter((file): file is File => file !== null);
  return filter ? resolved.filter(filter) : resolved;
}

export function CodeMirrorEditor({
  value,
  onChange,
  onSave,
  theme,
  readOnly = false,
  ariaLabel = "Markdown source",
  className,
  onDropFiles,
  onPasteImages,
  onDragOver,
  onDragLeave,
  toolbar,
  handleRef,
  onRegisterCaretInsert,
  livePreview: livePreviewOptions,
}: CodeMirrorEditorProps) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView>();
  const themeComp = useRef(new Compartment());
  const readOnlyComp = useRef(new Compartment());
  const liveComp = useRef(new Compartment());
  // The extension reads this ref on every recomputation, so option changes
  // (resolver, note path, link handler) never need to rebuild the editor.
  const liveOptions = useRef<LivePreviewOptions | null>(livePreviewOptions ?? null);
  liveOptions.current = livePreviewOptions ?? null;
  const onChangeRef = useRef(onChange);
  const onSaveRef = useRef(onSave);
  const onDropRef = useRef(onDropFiles);
  const onPasteRef = useRef(onPasteImages);
  const onDragOverRef = useRef(onDragOver);
  const onDragLeaveRef = useRef(onDragLeave);
  onChangeRef.current = onChange;
  onSaveRef.current = onSave;
  onDropRef.current = onDropFiles;
  onPasteRef.current = onPasteImages;
  onDragOverRef.current = onDragOver;
  onDragLeaveRef.current = onDragLeave;

  useImperativeHandle(
    handleRef,
    () => ({
      insertAtSelection: (text: string) => {
        const current = view.current;
        if (!current || !text) return false;
        const { from, to } = current.state.selection.main;
        const line = current.state.doc.lineAt(to);
        // Insert as its own paragraph: a reference must never merge into the
        // line the caret happens to sit on. The caret stays after the snippet.
        const lead = line.from === 0 && line.length === 0 ? "" : to === line.from ? "" : "\n";
        const snippet = `${lead}${text}${text.endsWith("\n") ? "" : "\n"}`;
        current.dispatch({
          changes: { from, to, insert: snippet },
          selection: { anchor: from + snippet.length },
          scrollIntoView: true,
        });
        current.focus();
        return true;
      },
      setHeading: (level: number) => {
        const current = view.current;
        if (!current) return false;
        const changed = setHeadingLevel(level)(current);
        current.focus();
        return changed;
      },
      focus: () => view.current?.focus(),
    }),
    [],
  );

  // Let the workspace store insert at the live caret while this editor is
  // mounted (attachment references). Unregisters on unmount/path change.
  useEffect(() => {
    if (!onRegisterCaretInsert) return;
    const handler = (text: string) => {
      const current = view.current;
      if (!current || !text) return false;
      const { from, to } = current.state.selection.main;
      const line = current.state.doc.lineAt(to);
      const lead = line.from === 0 && line.length === 0 ? "" : to === line.from ? "" : "\n";
      const snippet = `${lead}${text}${text.endsWith("\n") ? "" : "\n"}`;
      current.dispatch({
        changes: { from, to, insert: snippet },
        selection: { anchor: from + snippet.length },
        scrollIntoView: true,
      });
      current.focus();
      return true;
    };
    onRegisterCaretInsert(handler);
    return () => onRegisterCaretInsert(null);
  }, [onRegisterCaretInsert]);

  useEffect(() => {
    if (!host.current) return;
    const state = EditorState.create({
      doc: value,
      extensions: [
        markdown(),
        // Standard editing keys. Without this the editor has no keymap at all:
        // Enter/Backspace/arrows/undo all silently do nothing (defaultKeymap
        // ships in @codemirror/commands; `markdown()` provides none).
        history(),
        keymap.of([...defaultKeymap, ...historyKeymap, indentWithTab]),
        headingKeymap,
        liveComp.current.of(livePreviewOptions ? livePreview(() => liveOptions.current ?? {}) : []),
        EditorView.lineWrapping,
        themeComp.current.of(theme === "dark" ? oneDark : lightTheme),
        readOnlyComp.current.of([EditorState.readOnly.of(readOnly), EditorView.editable.of(!readOnly)]),
        EditorView.domEventHandlers({
          keydown: (event: KeyboardEvent) => {
            if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
              event.preventDefault();
              onSaveRef.current?.();
              return true;
            }
            return false;
          },
        }),
        EditorView.updateListener.of((update) => {
          if (update.docChanged) onChangeRef.current(update.state.doc.toString());
        }),
      ],
    });
    const created = new EditorView({ state, parent: host.current });
    created.dom.setAttribute("aria-label", ariaLabel);
    created.dom.setAttribute("role", "textbox");
    view.current = created;

    // Native listeners on the content DOM: CodeMirror's ``domEventHandlers``
    // do not reliably receive drag/drop or clipboard events, and these must
    // work in the browser as well as under jsdom.
    const content = created.contentDOM;
    const onDrop = (event: DragEvent) => {
      const files = filesFromDataTransfer(event.dataTransfer);
      if (!files.length || !onDropRef.current) return;
      event.preventDefault();
      event.stopPropagation();
      onDropRef.current(files);
    };
    const onDragOver = (event: DragEvent) => {
      const files = filesFromDataTransfer(event.dataTransfer);
      if (!files.length || !onDropRef.current) return;
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
      onDragOverRef.current?.(event);
    };
    const onDragLeave = (event: DragEvent) => onDragLeaveRef.current?.(event);
    const onPaste = (event: ClipboardEvent) => {
      const images = filesFromDataTransfer(event.clipboardData, (file) => file.type.startsWith("image/"));
      // Only an image payload is intercepted; a plain text paste keeps the
      // browser/CodeMirror default behaviour.
      if (!images.length || !onPasteRef.current) return;
      event.preventDefault();
      onPasteRef.current(images);
    };
    content.addEventListener("drop", onDrop);
    content.addEventListener("dragover", onDragOver);
    content.addEventListener("dragleave", onDragLeave);
    content.addEventListener("paste", onPaste);

    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(() => created.requestMeasure()) : null;
    observer?.observe(host.current);
    return () => {
      content.removeEventListener("drop", onDrop);
      content.removeEventListener("dragover", onDragOver);
      content.removeEventListener("dragleave", onDragLeave);
      content.removeEventListener("paste", onPaste);
      observer?.disconnect();
      created.destroy();
      view.current = undefined;
    };
  }, []);

  useEffect(() => {
    const current = view.current;
    if (!current) return;
    const doc = current.state.doc.toString();
    if (doc === value) return;
    // Apply only the changed span instead of replacing the whole document:
    // a wholesale replace resets the selection, which loses the caret that a
    // just-inserted attachment reference was placed at. When the external
    // value extends/edits the current doc (the normal echo case) the shared
    // prefix/suffix are kept and only the middle is rewritten.
    const { from, to, insert } = minimalChange(doc, value);
    const selection = current.state.selection.main;
    const caretShift = insert.length - (to - from);
    const mapCaret = (position: number) => {
      if (position <= from) return position;
      if (position >= to) return Math.max(from, position + caretShift);
      return from + insert.length;
    };
    current.dispatch({
      changes: { from, to, insert },
      selection: {
        anchor: Math.min(mapCaret(selection.anchor), value.length),
        head: Math.min(mapCaret(selection.head), value.length),
      },
    });
  }, [value]);
  useEffect(() => { view.current?.dispatch({ effects: themeComp.current.reconfigure(theme === "dark" ? oneDark : lightTheme) }); }, [theme]);
  useEffect(() => { view.current?.dispatch({ effects: readOnlyComp.current.reconfigure([EditorState.readOnly.of(readOnly), EditorView.editable.of(!readOnly)]) }); }, [readOnly]);
  useEffect(() => {
    view.current?.dispatch({
      effects: liveComp.current.reconfigure(livePreviewOptions ? livePreview(() => liveOptions.current ?? {}) : []),
    });
  }, [Boolean(livePreviewOptions)]);
  useEffect(() => { view.current?.dom.setAttribute("aria-label", ariaLabel); }, [ariaLabel]);

  return <div className={`code-mirror-shell ${className ?? ""}`.trim()} style={{ height: "100%", display: "flex", flexDirection: "column" }}>
    {toolbar}
    <div ref={host} style={{ flex: 1, minHeight: 0 }} />
  </div>;
}
