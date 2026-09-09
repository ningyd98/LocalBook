import { useCallback, useRef } from "react";
import { CodeMirrorEditor } from "@localnote/editor";
import type { CodeMirrorEditorHandle } from "@localnote/editor";
import type { LivePreviewOptions } from "@localnote/editor";
import type { EditorSession } from "@localnote/workspace";
import { pastedImageName } from "@localnote/workspace";
import { AttachmentToolbar } from "./AttachmentToolbar";
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
  /** Non-null enables the single-column WYSIWYG rendering. */
  livePreview?: LivePreviewOptions | null;
}

/**
 * Editor surface with the attachment entry points (ATT-12): toolbar file
 * picker, drag & drop onto the editor DOM and clipboard image paste.
 */
export function EditorPane({ session, theme, onChange, onSave, readOnly = false, onUploadFiles, attachmentBusy = false, attachmentHint, onRegisterCaretInsert, livePreview }: EditorPaneProps) {
  const { t, tr } = useI18n();
  const handle = useRef<CodeMirrorEditorHandle>(null);
  const path = session.path;
  // A stable per-note wrapper keeps the editor's registration effect from
  // tearing down and re-registering on every parent render.
  const register = useCallback(
    (handler: ((markdown: string) => boolean) | null) => onRegisterCaretInsert?.(handler),
    [onRegisterCaretInsert],
  );
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
    livePreview={livePreview}
    ariaLabel={tr(`源码编辑器 ${session.path}`, `Source editor for ${session.path}`)}
    onDropFiles={disabled ? undefined : (files) => onUploadFiles?.(files, "editor-drop")}
    onPasteImages={disabled ? undefined : (files) => onUploadFiles?.(files.map(renamePasted), "paste")}
    toolbar={<>
      <HeadingToolbar onHeading={(level) => handle.current?.setHeading(level)} disabled={readOnly}/>
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
