import { useCallback, useEffect, useMemo, useRef } from "react";
import { CodeMirrorEditor } from "@localnote/editor";
import type { CodeMirrorEditorHandle } from "@localnote/editor";
import type { LivePreviewOptions } from "@localnote/editor";
import type { EditorSession } from "@localnote/workspace";
import { pastedImageName } from "@localnote/workspace";
import { AttachmentToolbar } from "./AttachmentToolbar";
import { FormatToolbar } from "./FormatToolbar";
import type { EditorFormatCommands } from "./FormatToolbar";
import { HeadingToolbar } from "./HeadingToolbar";
import { useI18n } from "../i18n";

export interface EditorPaneProps {
  session: EditorSession;
  theme: "light" | "dark";
  onChange: (v: string) => void;
  onSave: () => void;
  readOnly?: boolean;
  /**
   * Attachment entry point shared by the toolbar picker, editor drop and
   * clipboard paste. The caller decides the target directory from the current
   * note and performs the size-based channel split.
   */
  onUploadFiles?: (files: File[], source: "toolbar" | "editor-drop" | "paste") => void;
  attachmentBusy?: boolean;
  attachmentHint?: string;
  /** Store bridge so uploads insert at the live caret (see registerCaretInsert). */
  onRegisterCaretInsert?: (handler: ((markdown: string) => boolean) | null) => void;
  /** Outline bridge: reveal a 0-based source line. Registered per note path. */
  onRegisterRevealLine?: (path: string, handler: ((line: number) => boolean) | null) => void;
  /** Caret line (0-based) of this note, so the outline can mark the position. */
  onCursorLine?: (path: string, line: number) => void;
  /**
   * Inline-formatting bridge: the workspace's context menu drives the editor
   * through these commands, registered per note path like the reveal handler.
   */
  onRegisterCommands?: (path: string, commands: EditorFormatCommands | null) => void;
  /** Non-null enables the single-column WYSIWYG rendering. */
  livePreview?: LivePreviewOptions | null;
  /** Right-click on the editing surface (pointer position for a context menu). */
  onContextMenuAt?: (path: string, x: number, y: number) => void;
}

/**
 * Editor surface with the attachment entry points (ATT-12): toolbar file
 * picker, drag & drop onto the editor DOM and clipboard image paste.
 */
export function EditorPane({ session, theme, onChange, onSave, readOnly = false, onUploadFiles, attachmentBusy = false, attachmentHint, onRegisterCaretInsert, onRegisterRevealLine, onCursorLine, onRegisterCommands, livePreview, onContextMenuAt }: EditorPaneProps) {
  const { t, tr } = useI18n();
  const handle = useRef<CodeMirrorEditorHandle>(null);
  const path = session.path;
  // A stable per-note wrapper keeps the editor's registration effect from
  // tearing down and re-registering on every parent render.
  const register = useCallback(
    (handler: ((markdown: string) => boolean) | null) => onRegisterCaretInsert?.(handler),
    [onRegisterCaretInsert],
  );
  // Every open note keeps its editor mounted (hidden), so each registers under
  // its own path and the workspace looks the active note up by path.
  const reveal = useCallback((line: number) => handle.current?.revealLine(line) ?? false, []);
  useEffect(() => {
    if (!onRegisterRevealLine) return;
    onRegisterRevealLine(path, reveal);
    return () => onRegisterRevealLine(path, null);
  }, [onRegisterRevealLine, reveal, path]);
  // The commands read `handle.current` lazily, so one object serves the whole
  // mounted lifetime of this note (the toolbar and the context menu share it).
  const commands = useMemo<EditorFormatCommands>(() => ({
    toggleBold: () => handle.current?.toggleBold() ?? false,
    toggleItalic: () => handle.current?.toggleItalic() ?? false,
    toggleHighlight: () => handle.current?.toggleHighlight() ?? false,
    applyTextColor: color => handle.current?.applyTextColor(color) ?? false,
    clearFormatting: () => handle.current?.clearFormatting() ?? false,
    insertTable: (rows, columns) => handle.current?.insertTable(rows, columns) ?? false,
  }), []);
  useEffect(() => {
    if (!onRegisterCommands) return;
    onRegisterCommands(path, commands);
    return () => onRegisterCommands(path, null);
  }, [onRegisterCommands, commands, path]);
  if (session.encoding !== "utf8") return <div className="editor-fallback">{t.editor.readOnly}</div>;
  const disabled = readOnly || !onUploadFiles;
  return <CodeMirrorEditor
    key={path}
    value={session.content}
    theme={theme}
    readOnly={readOnly}
    onChange={onChange}
    onSave={onSave}
    handleRef={handle}
    onRegisterCaretInsert={onRegisterCaretInsert ? register : undefined}
    onCursorLine={onCursorLine ? line => onCursorLine(path, line) : undefined}
    livePreview={livePreview}
    ariaLabel={tr(`源码编辑器 ${session.path}`, `Source editor for ${session.path}`)}
    onDropFiles={disabled ? undefined : (files) => onUploadFiles?.(files, "editor-drop")}
    onPasteImages={disabled ? undefined : (files) => onUploadFiles?.(files.map(renamePasted), "paste")}
    onContextMenuAt={onContextMenuAt ? (x, y) => onContextMenuAt(path, x, y) : undefined}
    toolbar={<>
      {/* The heading and inline-format controls share one row; the attachment
          bar keeps its own row below them. */}
      <div className="editor-format-bar">
        <HeadingToolbar onHeading={(level) => handle.current?.setHeading(level)} disabled={readOnly}/>
        <FormatToolbar commands={commands} disabled={readOnly}/>
      </div>
      <AttachmentToolbar onFiles={(files) => onUploadFiles?.(files, "toolbar")} busy={attachmentBusy} disabled={disabled} hint={attachmentHint}/>
    </>}
  />;
}

/**
 * A clipboard screenshot has no meaningful filename, so give it a
 * deterministic `pasted-image-<timestamp>.<ext>` display name.
 */
function renamePasted(file: File): File {
  if (file.name && file.name.trim() && !/^image\.(png|jpe?g|gif|webp|bmp)$/i.test(file.name)) return file;
  const type = file.type || "image/png";
  try {
    return new File([file], pastedImageName(type), { type });
  } catch {
    return file;
  }
}
