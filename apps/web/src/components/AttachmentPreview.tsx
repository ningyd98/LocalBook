import { useState } from "react";
import { Icon, IconButton } from "@localnote/ui";
import { useI18n } from "../i18n";

/** True when a Vault path should be shown as an inline image preview. */
export function isImagePath(path: string): boolean {
  return /\.(png|jpe?g|gif|webp|avif|bmp|svg|ico)$/i.test(path);
}

/**
 * Read-only attachment viewer (ATT-15). Images render through the resource
 * endpoint; every other file offers a download link. The path is only ever
 * used as an already-encoded URL, never as HTML.
 */
export function AttachmentPreview({ path, resolveResourceUrl, onClose }: { path: string; resolveResourceUrl: (path: string) => string; onClose?: () => void }) {
  const { tr } = useI18n();
  const [failed, setFailed] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const url = resolveResourceUrl(path);
  const name = path.split("/").at(-1) ?? path;
  return <section className="attachment-preview" aria-label={tr("附件预览", "Attachment preview")}>
    <header className="attachment-preview-head">
      <span className="attachment-preview-name" title={path}><Icon name={isImagePath(path) ? "image" : "files"} size={15}/>{name}</span>
      <a className="ui-button attachment-download" href={url} download={name}>{tr("下载", "Download")}</a>
      {onClose && <IconButton type="button" aria-label={tr("关闭附件预览", "Close attachment preview")} onClick={onClose}><Icon name="close" size={14}/></IconButton>}
    </header>
    {isImagePath(path)
      ? failed
        ? <p role="alert" className="attachment-preview-error">{tr("预览加载失败", "Preview failed to load")}</p>
        : <div className="attachment-preview-body"><img src={url} alt={name} onError={() => setFailed(true)} onLoad={() => setLoaded(true)} hidden={!loaded}/>{!loaded && <span className="attachment-preview-loading" role="status">{tr("正在加载…", "Loading…")}</span>}</div>
      : <div className="attachment-preview-body"><a className="ui-button primary" href={url} download={name}><Icon name="files" size={15}/>{tr("打开 / 下载附件", "Open / download attachment")}</a><p className="attachment-preview-path">{path}</p></div>}
  </section>;
}
