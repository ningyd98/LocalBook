import { useEffect, useState } from "react";
import { Icon, IconButton } from "@localnote/ui";
import { useWorkspaceStore } from "@localnote/workspace";
import type { TrashEntryDTO } from "@localnote/protocol";
import { useI18n } from "../i18n";
import { ConfirmDialog } from "./ConfirmDialog";

/** Human-readable size for the totals row. */
function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Local date only: the exact restore deadline is not useful in a list. */
function formatDate(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleDateString();
}

/**
 * The recycle bin: every soft-deleted file and folder, newest first, with the
 * days left before the retention window removes it for good.
 *
 * Deleting here is the *permanent* action, so it always confirms — with its own
 * dialog, because the surrounding tree's confirmation explains a different
 * thing (moving to the bin).
 */
export function TrashPanel({ open, onToggle, onOpen }: { open: boolean; onToggle: () => void; onOpen: (path: string) => void }) {
  const { tr, errorText } = useI18n();
  const trash = useWorkspaceStore(state => state.trash);
  const loadTrash = useWorkspaceStore(state => state.loadTrash);
  const restoreFromTrash = useWorkspaceStore(state => state.restoreFromTrash);
  const deleteFromTrash = useWorkspaceStore(state => state.deleteFromTrash);
  const emptyTrash = useWorkspaceStore(state => state.emptyTrash);
  const [confirming, setConfirming] = useState<TrashEntryDTO | null>(null);
  const [emptying, setEmptying] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  // The bin is only read when it is actually opened (or after a delete).
  useEffect(() => { if (open) void loadTrash(); }, [open, loadTrash]);

  const restore = async (entry: TrashEntryDTO) => {
    const restored = await restoreFromTrash(entry);
    if (restored) {
      setNotice(tr(`已恢复到 ${restored}`, `Restored to ${restored}`));
      if (restored.toLowerCase().endsWith(".md")) onOpen(restored);
    }
  };

  return <section className="trash-panel" aria-label={tr("回收站", "Recycle bin")}>
    <header className="trash-header">
      <button type="button" className="trash-toggle" aria-expanded={open} onClick={onToggle}>
        <Icon name="chevron" size={12} className={open ? "expanded" : ""}/>
        <Icon name="trash" size={14}/>
        <span>{tr("回收站", "Recycle bin")}</span>
        {trash.entries.length > 0 && <span className="trash-count">{trash.entries.length}</span>}
      </button>
      {open && trash.entries.length > 0 && <IconButton className="trash-empty" title={tr("清空回收站", "Empty the recycle bin")} aria-label={tr("清空回收站", "Empty the recycle bin")} onClick={() => setEmptying(true)}><Icon name="close" size={13}/></IconButton>}
    </header>
    {open && <div className="trash-body">
      <p className="trash-hint">{tr(`删除的文件和文件夹会保留 ${trash.retentionDays} 天，到期自动清理。`, `Deleted files and folders are kept for ${trash.retentionDays} days, then removed automatically.`)}</p>
      {trash.error && <p className="trash-error" role="alert">{errorText(trash.error)}</p>}
      {notice && <p className="trash-notice" role="status">{notice}</p>}
      {trash.status === "ready" && trash.entries.length === 0 && <p className="trash-empty-state">{tr("回收站是空的。", "The recycle bin is empty.")}</p>}
      {trash.entries.length > 0 && <>
        <ul className="trash-list">
          {trash.entries.map(entry => <li key={entry.id} className="trash-item">
            <div className="trash-meta">
              <span className="trash-name" title={entry.original_path}>
                <Icon name={entry.kind === "directory" ? "folder" : entry.name.toLowerCase().endsWith(".md") ? "note" : "paperclip"} size={13}/>
                {entry.name}
              </span>
              <small>{entry.original_path.includes("/") ? `${entry.original_path.slice(0, entry.original_path.lastIndexOf("/"))}/` : "./"} · {formatDate(entry.deleted_at)} · {tr(`剩余 ${entry.days_remaining} 天`, `${entry.days_remaining} day(s) left`)}</small>
            </div>
            <div className="trash-actions">
              <button type="button" onClick={() => void restore(entry)} disabled={trash.busy}>{tr("恢复", "Restore")}</button>
              <button type="button" className="danger" onClick={() => setConfirming(entry)} disabled={trash.busy}>{tr("彻底删除", "Delete forever")}</button>
            </div>
          </li>)}
        </ul>
        <p className="trash-total">{tr(`${trash.entries.length} 项 · ${formatBytes(trash.totalBytes)}`, `${trash.entries.length} item(s) · ${formatBytes(trash.totalBytes)}`)}</p>
      </>}
    </div>}
    <ConfirmDialog open={confirming !== null} busy={trash.busy} title={tr("彻底删除", "Delete forever")}
      message={confirming ? tr(`将永久删除「${confirming.original_path}」，无法恢复。`, `“${confirming.original_path}” will be deleted permanently and cannot be restored.`) : ""}
      onCancel={() => setConfirming(null)}
      onConfirm={() => { const target = confirming; setConfirming(null); if (target) void deleteFromTrash(target); }}/>
    <ConfirmDialog open={emptying} busy={trash.busy} title={tr("清空回收站", "Empty the recycle bin")}
      message={tr(`将永久删除回收站里的 ${trash.entries.length} 项，无法恢复。`, `All ${trash.entries.length} item(s) in the recycle bin will be deleted permanently.`)}
      onCancel={() => setEmptying(false)}
      onConfirm={() => { setEmptying(false); void emptyTrash(); }}/>
  </section>;
}
