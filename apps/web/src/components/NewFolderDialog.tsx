import { useEffect, useState } from "react";
import { Button, Dialog, Icon } from "@localnote/ui";
import { useWorkspaceStore } from "@localnote/workspace";
import { useI18n } from "../i18n";

/** Unique default folder name based on the current tree. */
export function suggestFolderName(existing: string[], base: string, parent = ""): string {
  const prefix = parent ? `${parent.replace(/\/+$/, "")}/` : "";
  const taken = new Set(existing);
  for (let index = 1; index < 1000; index++) {
    const leaf = index === 1 ? base : `${base} ${index}`;
    const candidate = `${prefix}${leaf}`;
    if (!taken.has(candidate)) return candidate;
  }
  return `${prefix}${base} ${Date.now()}`;
}

export function NewFolderDialog({ open, onClose, parent = "" }: { open: boolean; onClose: () => void; parent?: string }) {
  const { t, tr, errorText } = useI18n();
  const entries = useWorkspaceStore(s => s.tree.entries);
  const createFolder = useWorkspaceStore(s => s.createFolder);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setName(suggestFolderName(entries.map(entry => entry.path), tr("新建文件夹", "New folder"), parent));
    setError(null);
    setBusy(false);
  }, [open, parent]);

  if (!open) return null;

  const close = () => { setError(null); setBusy(false); onClose(); };
  const submit = async () => {
    const typed = name.trim();
    if (!typed || busy) return;
    setBusy(true); setError(null);
    try {
      await createFolder(typed);
      setBusy(false);
      onClose();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };

  return <Dialog label={tr("新建文件夹", "New folder")} onClose={close} busy={busy} className="confirm-dialog">
    <h2>{tr("新建文件夹", "New folder")}</h2>
    <form onSubmit={event => { event.preventDefault(); void submit(); }}>
      <label className="field" htmlFor="new-folder-name"><span>{tr("文件夹名", "Folder name")}</span>
        <input id="new-folder-name" autoFocus value={name} disabled={busy} placeholder={tr("我的文件夹", "my-folder")}
          onChange={event => { setName(event.target.value); setError(null); }} />
        <small>{tr("可以用 / 建子目录（上级目录必须已存在）。", "Use / for a nested folder (the parent must already exist).")}</small>
      </label>
      {error && <div className="feedback error" role="alert">{error}</div>}
      <div className="confirm-actions">
        <Button type="button" disabled={busy} onClick={close}>{t.buttons.cancel}</Button>
        <Button type="submit" className="primary" disabled={busy || !name.trim()}>
          <Icon name="folder" size={15}/>{busy ? tr("正在创建…", "Creating…") : tr("创建文件夹", "Create folder")}
        </Button>
      </div>
    </form>
  </Dialog>;
}
