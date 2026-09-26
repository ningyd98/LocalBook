import { useRef } from "react";
import type { ChangeEvent } from "react";
import { Icon, IconButton } from "@localnote/ui";
import { useI18n } from "../i18n";

/**
 * Editor toolbar entry point for attachments and recording files (ATT-12).
 *
 * Both pickers share the same upload callback; the caller decides the target
 * directory from the current note and performs the size-based channel split.
 */
export function AttachmentToolbar({ onFiles, busy = false, disabled = false, hint }: { onFiles: (files: File[]) => void; busy?: boolean; disabled?: boolean; hint?: string }) {
  const { tr } = useI18n();
  const input = useRef<HTMLInputElement>(null);
  const audioInput = useRef<HTMLInputElement>(null);
  const documentInput = useRef<HTMLInputElement>(null);
  const label = tr("插入附件", "Insert attachment");
  const audioLabel = tr("插入录音", "Insert recording");
  const documentLabel = tr("插入文档", "Insert document");
  const consume = (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    // Reset first so selecting the same file twice still fires a change.
    event.target.value = "";
    if (files.length) onFiles(files);
  };
  return <div className="editor-attachment-bar">
    <input
      ref={input}
      type="file"
      multiple
      hidden
      data-testid="attachment-input"
      aria-hidden="true"
      tabIndex={-1}
      onChange={consume}
    />
    <input
      ref={audioInput}
      type="file"
      accept="audio/*,.m4a,.mp3,.wav,.ogg,.opus,.flac,.aac,.amr,.caf"
      multiple
      hidden
      data-testid="audio-input"
      aria-hidden="true"
      tabIndex={-1}
      onChange={consume}
    />
    <input
      ref={documentInput}
      type="file"
      accept=".pdf,.doc,.docx,.docm,.dotx,.dotm,.ppt,.pptx,.pptm,.pps,.ppsx,.potx,.potm,.xls,.xlsx,.xlsm,.odt,.ods,.odp,.ott,.otp,.ots,.wps,.wpt,.dps,.dpt,.et,.ett,.rtf,.csv,application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.openxmlformats-officedocument.presentationml.presentation,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
      multiple
      hidden
      data-testid="document-input"
      aria-hidden="true"
      tabIndex={-1}
      onChange={consume}
    />
    <IconButton
      type="button"
      className="attachment-insert"
      aria-label={label}
      title={label}
      disabled={disabled || busy}
      onClick={() => input.current?.click()}
    >
      <Icon name="paperclip" size={15}/>
      <span className="attachment-insert-label">{busy ? tr("上传中…", "Uploading…") : label}</span>
    </IconButton>
    <IconButton
      type="button"
      className="attachment-insert recording-insert"
      aria-label={audioLabel}
      title={audioLabel}
      disabled={disabled || busy}
      onClick={() => audioInput.current?.click()}
    >
      <Icon name="paperclip" size={15}/>
      <span className="attachment-insert-label">{busy ? tr("上传中…", "Uploading…") : audioLabel}</span>
    </IconButton>
    <IconButton
      type="button"
      className="attachment-insert document-insert"
      aria-label={documentLabel}
      title={documentLabel}
      disabled={disabled || busy}
      onClick={() => documentInput.current?.click()}
    >
      <Icon name="files" size={15}/>
      <span className="attachment-insert-label">{busy ? tr("上传中…", "Uploading…") : documentLabel}</span>
    </IconButton>
    <span className="attachment-hint">{hint ?? tr("拖到此处或粘贴图片", "Drop here or paste an image")}</span>
  </div>;
}
