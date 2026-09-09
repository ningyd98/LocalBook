import { useEffect, useState } from "react";
import { Button, Dialog, Icon } from "@localnote/ui";
import { useWorkspaceStore } from "@localnote/workspace";
import { useI18n } from "../i18n";

/** Unique, human-readable default name for a brand new note. */
export function suggestName(existing: string[], base: string): string {
  const taken = new Set(existing);
  for (let index = 1; index < 1000; index++) {
    const candidate = index === 1 ? `${base}.md` : `${base} ${index}.md`;
    if (!taken.has(candidate)) return candidate;
  }
  return `${base} ${Date.now()}.md`;
}

export function NewNoteDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t, tr, errorText } = useI18n();
  const entries = useWorkspaceStore(s => s.tree.entries);
  const createNote = useWorkspaceStore(s => s.createNote);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Seed a non-colliding default name each time the dialog opens.
  useEffect(() => {
    if (!open) return;
    setName(suggestName(entries.map(entry => entry.path), tr("未命名笔记", "Untitled note")));
    setError(null);
    setBusy(false);
  }, [open]);

  if (!open) return null;

  const close = () => { setError(null); setBusy(false); onClose(); };
  const submit = async () => {
    const typed = name.trim();
    if (!typed || busy) return;
    const path = /\.[a-z0-9]+$/i.test(typed) ? typed : `${typed}.md`;
    setBusy(true); setError(null);
    try {
      await createNote(path);
      setBusy(false);
      onClose();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };

  return <Dialog label={tr("新建笔记", "New note")} onClose={close} busy={busy} className="confirm-dialog">
    <h2>{tr("新建笔记", "New note")}</h2>
    <form onSubmit={event => { event.preventDefault(); void submit(); }}>
      <label className="field" htmlFor="new-note-name"><span>{tr("文件名", "File name")}</span>
        <input id="new-note-name" autoFocus value={name} disabled={busy} placeholder={tr("我的笔记.md", "my-note.md")}
          onChange={event => { setName(event.target.value); setError(null); }} />
        <small>{tr("可以用 / 建子目录（目录必须已存在），不写扩展名会自动补 .md。", "Use / for a subfolder (it must already exist). .md is appended when no extension is given.")}</small>
      </label>
      {error && <div className="feedback error" role="alert">{error}</div>}
      <div className="confirm-actions">
        <Button type="button" disabled={busy} onClick={close}>{t.buttons.cancel}</Button>
        <Button type="submit" className="primary" disabled={busy || !name.trim()}>
          <Icon name="note" size={15}/>{busy ? tr("正在创建…", "Creating…") : tr("创建并打开", "Create & open")}
        </Button>
      </div>
    </form>
  </Dialog>;
}
