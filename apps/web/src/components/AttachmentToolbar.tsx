import { useRef } from "react";
import { Icon, IconButton } from "@localnote/ui";
import { useI18n } from "../i18n";

/**
 * Editor toolbar entry point for attachments (ATT-12).
 *
 * The hidden `<input type=file>` accepts images and arbitrary attachments; the
 * caller (EditorPane → Workspace store) decides the target directory from the
 * current note and performs the size-based channel split.
 */
export function AttachmentToolbar({ onFiles, busy = false, disabled = false, hint }: { onFiles: (files: File[]) => void; busy?: boolean; disabled?: boolean; hint?: string }) {
  const { tr } = useI18n();
  const input = useRef<HTMLInputElement>(null);
  const label = tr("插入附件", "Insert attachment");
  return <div className="editor-attachment-bar">
    <input
      ref={input}
      type="file"
      multiple
      hidden
      data-testid="attachment-input"
      aria-hidden="true"
      tabIndex={-1}
      onChange={(event) => {
        const files = Array.from(event.target.files ?? []);
        // Reset first so selecting the same file twice still fires a change.
        event.target.value = "";
        if (files.length) onFiles(files);
      }}
    />
    <IconButton
      type="button"
      className="attachment-insert"
      aria-label={label}
      title={label}
      disabled={disabled || busy}
      onClick={() => input.current?.click()}
    >
      <Icon name="note" size={15}/>
      <span className="attachment-insert-label">{busy ? tr("上传中…", "Uploading…") : label}</span>
    </IconButton>
    <span className="attachment-hint">{hint ?? tr("拖到此处或粘贴图片", "Drop here or paste an image")}</span>
  </div>;
}
