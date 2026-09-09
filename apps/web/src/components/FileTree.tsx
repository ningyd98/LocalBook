import { useEffect, useMemo, useRef, useState } from "react";
import type { DragEvent } from "react";
import { Icon } from "@localnote/ui";
import type { VaultFileEntry } from "@localnote/protocol";
import { isEditableMarkdown } from "@localnote/workspace";
import { useI18n } from "../i18n";

/** True when ``path`` is ``ancestor`` itself or lives inside it. */
function isInside(path: string, ancestor: string): boolean {
  return path === ancestor || path.startsWith(`${ancestor}/`);
}

export interface FileTreeProps {
  entries: VaultFileEntry[];
  expanded: string[];
  activePath?: string | null;
  onToggle: (p: string) => void;
  onOpen: (p: string) => void;
  onMove?: (source: string, destinationDirectory: string) => void;
  /** Non-Markdown rows open a read-only attachment viewer, not a tab. */
  onOpenAttachment?: (p: string) => void;
  /** Directory context menu → upload into that exact Vault directory. */
  onUploadToDirectory?: (directory: string) => void;
  /** Rename a file in place (same-directory move). Rejects invalid names. */
  onRename?: (path: string, newName: string) => Promise<string>;
  activeAttachmentPath?: string | null;
}

export function FileTree({ entries, expanded, activePath, onToggle, onOpen, onMove, onOpenAttachment, onUploadToDirectory, onRename, activeAttachmentPath }: FileTreeProps) {
  const { tr } = useI18n();
  const [dragging, setDragging] = useState<string | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const [menu, setMenu] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [renameError, setRenameError] = useState<string | null>(null);
  const renameInput = useRef<HTMLInputElement>(null);
  useEffect(() => { if (renaming) { renameInput.current?.focus(); renameInput.current?.select(); } }, [renaming]);
  const startRename = (path: string) => { setMenu(null); setRenameError(null); setDraft(path.split("/").at(-1) ?? path); setRenaming(path); };
  const cancelRename = () => { setRenaming(null); setRenameError(null); };
  const submitRename = async (path: string) => {
    if (!onRename) return;
    const name = draft.trim();
    const current = path.split("/").at(-1) ?? path;
    if (!name || name === current) { cancelRename(); return; }
    if (name.includes("/") || name.includes("\\") || name === "." || name === "..") {
      setRenameError(tr("名称不能包含 / 或 \\", "A name cannot contain / or \\"));
      return;
    }
    try {
      await onRename(path, name);
      cancelRename();
    } catch (error) {
      // Stay in edit mode so the user can correct the name.
      const value = error as { code?: string; message?: string } | null;
      setRenameError(value?.code === "already_exists" ? tr("同名文件已存在", "A file with that name already exists") : tr("重命名失败，请检查名称后重试", "Rename failed. Check the name and retry."));
    }
  };
  const visible = useMemo(() => {
    const open = new Set(expanded);
    return entries.filter(entry => { const parts = entry.path.split("/"); return !parts.some(p => p.startsWith(".")) && parts.slice(0, -1).every((_, i) => open.has(parts.slice(0, i + 1).join("/"))); }).sort((a,b) => {
      // Sort siblings hierarchically, so a directory's children stay beneath it.
      const aa = a.path.split("/"), bb = b.path.split("/");
      for (let i = 0; i < Math.min(aa.length, bb.length); i++) { if (aa[i] === bb[i]) continue; const ad = i < aa.length - 1 || a.kind === "directory", bd = i < bb.length - 1 || b.kind === "directory"; return ad !== bd ? (ad ? -1 : 1) : aa[i]!.localeCompare(bb[i]!); }
      return aa.length - bb.length;
    });
  }, [entries, expanded]);
  /** A drop is legal only on a folder that is not the source or its descendant. */
  const canDrop = (folder: string) => Boolean(onMove && dragging) && !isInside(folder, dragging!);
  const drop = (event: DragEvent, folder: string) => {
    event.preventDefault(); event.stopPropagation();
    const source = dragging ?? event.dataTransfer.getData("text/localnote-path");
    setDragging(null); setOver(null);
    if (!onMove || !source || isInside(folder, source)) return;
    const parent = source.split("/").slice(0, -1).join("/");
    if (parent === folder) return; // already there
    onMove(source, folder);
  };
  return <div role="tree" className="file-tree">{visible.map(entry => {
    const folder = entry.kind === "directory", open = expanded.includes(entry.path), droppable = folder && canDrop(entry.path);
    const markdown = !folder && isEditableMarkdown(entry.path);
    // Attachment rows are clickable (ATT-15); Markdown rows still call onOpen.
    const openable = folder || markdown || Boolean(onOpenAttachment);
    const uploadHere = folder && onUploadToDirectory;
    const canRename = !folder && Boolean(onRename);
    const rowMenu = Boolean(uploadHere || canRename);
    const isRenaming = renaming === entry.path;
    const selected = !folder && (entry.path === activePath || entry.path === activeAttachmentPath);
    return <div key={entry.path} role="treeitem" aria-level={entry.path.split("/").length} aria-selected={selected} aria-expanded={folder ? open : undefined} className={`ui-tree-row ${selected ? "selected" : ""} ${dragging === entry.path ? "dragging" : ""} ${over === entry.path && droppable ? "drop-target" : ""}`}
    onDragOver={folder && onMove ? event => { if (!canDrop(entry.path)) return; event.preventDefault(); event.dataTransfer.dropEffect = "move"; setOver(entry.path); } : undefined}
    onDragLeave={folder && onMove ? () => setOver(current => current === entry.path ? null : current) : undefined}
    onDrop={folder && onMove ? event => drop(event, entry.path) : undefined}>
    <button type="button" style={{paddingLeft: 10 + (entry.path.split("/").length-1)*16}} disabled={!openable} title={entry.path}
      onClick={() => { if (folder) onToggle(entry.path); else if (markdown) onOpen(entry.path); else onOpenAttachment?.(entry.path); }}
      onContextMenu={rowMenu ? event => { event.preventDefault(); setMenu(entry.path); } : undefined}
      onDoubleClick={canRename ? event => { event.preventDefault(); startRename(entry.path); } : undefined}
      aria-expanded={folder ? open : undefined}
      draggable={Boolean(onMove)} onDragStart={onMove ? event => { setDragging(entry.path); event.dataTransfer.effectAllowed = "move"; event.dataTransfer.setData("text/localnote-path", entry.path); } : undefined} onDragEnd={onMove ? () => { setDragging(null); setOver(null); } : undefined}>{folder ? <Icon name="chevron" size={12} className={open ? "expanded" : ""}/> : <span className="tree-spacer"/>}<Icon name={folder ? "folder" : markdown ? "note" : "paperclip"} size={15}/><span>{entry.path.split("/").at(-1)}</span></button>
    {isRenaming && <div className="tree-rename" style={{paddingLeft: 10 + (entry.path.split("/").length-1)*16}}>
      <input
        ref={renameInput}
        className="tree-rename-input"
        value={draft}
        aria-label={tr(`重命名 ${entry.path}`, `Rename ${entry.path}`)}
        onChange={event => { setDraft(event.target.value); setRenameError(null); }}
        onKeyDown={event => {
          if (event.key === "Enter") { event.preventDefault(); void submitRename(entry.path); }
          else if (event.key === "Escape") { event.preventDefault(); cancelRename(); }
        }}
        onBlur={() => { if (renaming === entry.path) cancelRename(); }}
      />
      {renameError && <span role="alert" className="tree-rename-error">{renameError}</span>}
    </div>}
    {menu === entry.path && <div role="menu" className="tree-menu">
      {canRename && <button type="button" role="menuitem" autoFocus onClick={() => startRename(entry.path)}>{tr("重命名", "Rename")}</button>}
      {uploadHere && <button type="button" role="menuitem" autoFocus={!canRename} onClick={() => { setMenu(null); onUploadToDirectory?.(entry.path); }}>{tr("上传到该目录", "Upload to this folder")}</button>}
      <button type="button" role="menuitem" onClick={() => setMenu(null)}>{tr("取消", "Cancel")}</button>
    </div>}
  </div>; })}</div>;
}
