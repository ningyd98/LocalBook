import { useEffect, useState } from "react";
import type { TranscriptionResponse } from "@localnote/protocol";
import { Icon, IconButton } from "@localnote/ui";
import { useI18n } from "../i18n";

/** True when a Vault path should be shown as an inline image preview. */
export function isImagePath(path: string): boolean {
  return /\.(png|jpe?g|gif|webp|avif|bmp|svg|ico)$/i.test(path.split(/[?#]/, 1)[0] ?? path);
}

/** True when a Vault path is a recording that browsers can play. */
export function isAudioPath(path: string): boolean {
  return /\.(mp3|wav|m4a|aac|flac|ogg|oga|opus|webm|amr|caf|aiff?|wma)$/i.test(path.split(/[?#]/, 1)[0] ?? path);
}

/** True when a Vault path is a PDF or common Office/OpenDocument file. */
export function isDocumentPath(path: string): boolean {
  return /\.(pdf|doc|docx|docm|dotx|dotm|wps|wpt|ppt|pptx|pptm|pps|ppsx|potx|potm|dps|dpt|xls|xlsx|xlsm|et|ett|odt|ods|odp|ott|otp|ots|rtf|csv)$/i.test(path.split(/[?#]/, 1)[0] ?? path);
}

/**
 * Read-only attachment viewer. Images, recordings and documents render through
 * the resource/preview endpoints; every other file offers a download link.
 * Transcription is deliberately injected by the workspace so this component
 * never chooses a command or writes note bytes itself.
 */
export function AttachmentPreview({ path, resolveResourceUrl, resolveDocumentPreviewUrl, onClose, onTranscribe }: {
  path: string;
  resolveResourceUrl: (path: string) => string;
  resolveDocumentPreviewUrl?: (path: string) => string;
  onClose?: () => void;
  onTranscribe?: (path: string) => Promise<TranscriptionResponse>;
}) {
  const { tr } = useI18n();
  const [failed, setFailed] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [transcript, setTranscript] = useState<TranscriptionResponse | null>(null);
  const [transcriptionError, setTranscriptionError] = useState(false);
  useEffect(() => {
    setFailed(false);
    setLoaded(false);
    setTranscribing(false);
    setTranscript(null);
    setTranscriptionError(false);
  }, [path]);
  const url = resolveResourceUrl(path);
  const name = path.split("/").at(-1) ?? path;
  const audio = isAudioPath(path);
  const document = isDocumentPath(path);
  const documentPreviewUrl = document
    ? resolveDocumentPreviewUrl?.(path) ?? (/\.pdf(?:[?#]|$)/i.test(path) ? url : null)
    : null;
  const runTranscription = async () => {
    if (!onTranscribe || transcribing) return;
    setTranscribing(true);
    setTranscriptionError(false);
    try {
      setTranscript(await onTranscribe(path));
    } catch {
      setTranscriptionError(true);
    } finally {
      setTranscribing(false);
    }
  };
  return <section className="attachment-preview" aria-label={tr("附件预览", "Attachment preview")}>
    <header className="attachment-preview-head">
      <span className="attachment-preview-name" title={path}><Icon name={audio ? "paperclip" : isImagePath(path) ? "image" : "files"} size={15}/>{name}</span>
      <a className="ui-button attachment-download" href={url} download={name}>{tr("下载", "Download")}</a>
      {onClose && <IconButton type="button" aria-label={tr("关闭附件预览", "Close attachment preview")} onClick={onClose}><Icon name="close" size={14}/></IconButton>}
    </header>
    {audio
      ? <div className="attachment-preview-body audio-preview-body">
          {failed
            ? <p role="alert" className="attachment-preview-error">{tr("预览加载失败", "Preview failed to load")}</p>
            : <audio controls preload="metadata" src={url} onError={() => setFailed(true)} />}
          {onTranscribe && <button type="button" className="ui-button primary attachment-transcribe" disabled={transcribing} onClick={() => void runTranscription()}>
            <Icon name="paperclip" size={15}/>{transcribing ? tr("转写中…", "Transcribing…") : tr("转文字并插入当前笔记", "Transcribe and insert into current note")}
          </button>}
          {transcriptionError && <p role="alert" className="attachment-preview-error">{tr("转写失败，请检查本地转写工具配置。", "Transcription failed; check the local transcription tool configuration.")}</p>}
          {transcript && <div className="attachment-transcript">
            <strong>{tr("转写结果", "Transcript")}{transcript.truncated ? tr("（已截断）", " (truncated)") : ""}</strong>
            <pre>{transcript.text}</pre>
          </div>}
          <p className="attachment-preview-path">{path}</p>
        </div>
      : document && documentPreviewUrl
        ? <div className="attachment-preview-body document-preview-body">
            {failed
              ? <p role="alert" className="attachment-preview-error">{tr("文档预览加载失败，请下载原文件查看。", "Document preview failed; download the original file to view it.")}</p>
              : <iframe title={name} src={documentPreviewUrl} loading="lazy" onError={() => setFailed(true)} />}
            <a className="ui-button primary" href={url} download={name}><Icon name="files" size={15}/>{tr("下载原文件", "Download original")}</a>
            <p className="attachment-preview-path">{path}</p>
          </div>
      : isImagePath(path)
        ? failed
          ? <p role="alert" className="attachment-preview-error">{tr("预览加载失败", "Preview failed to load")}</p>
          : <div className="attachment-preview-body"><img src={url} alt={name} onError={() => setFailed(true)} onLoad={() => setLoaded(true)} hidden={!loaded}/>{!loaded && <span className="attachment-preview-loading" role="status">{tr("正在加载…", "Loading…")}</span>}</div>
        : <div className="attachment-preview-body"><a className="ui-button primary" href={url} download={name}><Icon name="files" size={15}/>{tr("打开 / 下载附件", "Open / download attachment")}</a><p className="attachment-preview-path">{path}</p></div>}
  </section>;
}
