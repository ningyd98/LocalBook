import { useEffect, useMemo, useRef, useState } from "react";
import type { DragEvent } from "react";
import { Icon } from "@localnote/ui";
import type { VaultFileEntry } from "@localnote/protocol";
import { buildTreeHierarchy, foldedPaths, isEditableMarkdown, isPathExpanded, visibleTreeRows } from "@localnote/workspace";
import { useI18n } from "../i18n";
import { ContextMenu } from "./ContextMenu";
import type { ContextMenuItem } from "./ContextMenu";

/** True when ``path`` is ``ancestor`` itself or lives inside it. */
function isInside(path: string, ancestor: string): boolean {
  return path === ancestor || path.startsWith(`${ancestor}/`);
}

export interface FileTreeProps {
  entries: VaultFileEntry[];
  expanded: string[];
  /** Paths the user collapsed explicitly; everything else shows its children. */
  collapsed?: string[];
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
  /** Context menu → create a document nested inside that row's container. */
  onNewChildNote?: (path: string) => void;
  /** Context menu → create a document beside that note. */
  onNewSiblingNote?: (path: string) => void;
  /** Context menu on empty tree space → create a document at the Vault root. */
  onNewRootNote?: () => void;
  /** Context menu → ask to move that file or folder to the recycle bin. */
  onDelete?: (path: string) => void;
}

export function FileTree({ entries, expanded, collapsed = [], activePath, onToggle, onOpen, onMove, onOpenAttachment, onUploadToDirectory, onRename, activeAttachmentPath, onNewChildNote, onNewSiblingNote, onNewRootNote, onDelete }: FileTreeProps) {
  const { tr } = useI18n();
  const [dragging, setDragging] = useState<string | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const [menu, setMenu] = useState<{ path: string; x: number; y: number } | null>(null);
  const [blankMenu, setBlankMenu] = useState<{ x: number; y: number } | null>(null);
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
  /**
   * The hierarchy is derived from the listing plus the user's collapses: a note
   * that owns a child folder (`notes/A.md` + `notes/A/`) is rendered as the
   * parent of that folder's contents, so collapse/expand works per document
   * level rather than per folder only.
   */
  const expansion = useMemo(() => ({ expandedPaths: expanded, collapsedPaths: collapsed }), [expanded, collapsed]);
  const hierarchy = useMemo(() => ({ nodes: buildTreeHierarchy(entries), owner: foldedPaths(entries) }), [entries]);
  const rows = useMemo(() => visibleTreeRows(hierarchy.nodes, hierarchy.owner, expansion), [hierarchy, expansion]);
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
  const menuItems = (path: string, kind: "file" | "directory"): ContextMenuItem[] => {
    const items: ContextMenuItem[] = [];
    if (onNewChildNote) items.push({ id: "child", label: tr("新建子文档", "New nested document"), icon: "note", onSelect: () => onNewChildNote(path) });
    // A folder row is already a container, so the "beside it" action simply
    // means "the next document inside it"; a note row gets a real sibling.
    if (onNewSiblingNote) items.push({ id: "sibling", label: kind === "directory" ? tr("在此文件夹新建文档", "New document in this folder") : tr("新建同级文档", "New document at this level"), icon: "note", onSelect: () => onNewSiblingNote(path) });
    if (onRename && kind === "file") items.push({ id: "rename", label: tr("重命名", "Rename"), icon: "edit", separated: items.length > 0, onSelect: () => startRename(path) });
    if (onUploadToDirectory && kind === "directory") items.push({ id: "upload", label: tr("上传到该目录", "Upload to this folder"), icon: "paperclip", separated: items.length > 0, onSelect: () => onUploadToDirectory(path) });
    // A folder is trashed as a whole, so no row is pre-disabled: the caller
    // confirms and the server explains any refusal.
    if (onDelete) items.push({ id: "delete", label: tr("移到回收站", "Move to recycle bin"), icon: "trash", separated: items.length > 0, onSelect: () => onDelete(path) });
    return items;
  };
  return <div role="tree" className="file-tree"
    onContextMenu={onNewRootNote ? event => { if (event.target !== event.currentTarget) return; event.preventDefault(); setMenu(null); setBlankMenu({ x: event.clientX, y: event.clientY }); } : undefined}>
    {rows.map(row => {
    const folder = row.kind === "directory", open = isPathExpanded(row.path, expansion), droppable = folder && canDrop(row.path);
    const markdown = !folder && isEditableMarkdown(row.path);
    // Attachment rows are clickable (ATT-15); Markdown rows still call onOpen.
    const openable = folder || markdown || Boolean(onOpenAttachment);
    const uploadHere = folder && onUploadToDirectory;
    const canRename = !folder && Boolean(onRename);
    const canNest = Boolean(onNewChildNote) || Boolean(onNewSiblingNote);
    const rowMenu = Boolean(uploadHere || canRename || canNest || onDelete);
    const hasChildren = row.children.length > 0;
    const isRenaming = renaming === row.path;
    const selected = !folder && (row.path === activePath || row.path === activeAttachmentPath);
    return <div key={row.path} role="treeitem" aria-level={row.depth + 1} aria-selected={selected} aria-expanded={hasChildren ? open : undefined} className={`ui-tree-row ${selected ? "selected" : ""} ${dragging === row.path ? "dragging" : ""} ${over === row.path && droppable ? "drop-target" : ""}`}
    onDragOver={folder && onMove ? event => { if (!canDrop(row.path)) return; event.preventDefault(); event.dataTransfer.dropEffect = "move"; setOver(row.path); } : undefined}
    onDragLeave={folder && onMove ? () => setOver(current => current === row.path ? null : current) : undefined}
    onDrop={folder && onMove ? event => drop(event, row.path) : undefined}>
    <button type="button" style={{paddingLeft: 10 + row.depth*16}} disabled={!openable} title={row.path} aria-label={row.path}
      onClick={() => { if (folder) onToggle(row.path); else if (markdown) onOpen(row.path); else onOpenAttachment?.(row.path); }}
      onContextMenu={rowMenu ? event => { event.preventDefault(); event.stopPropagation(); setBlankMenu(null); setMenu({ path: row.path, x: event.clientX, y: event.clientY }); } : undefined}
      onDoubleClick={canRename ? event => { event.preventDefault(); startRename(row.path); } : undefined}
      aria-expanded={hasChildren ? open : undefined}
      draggable={Boolean(onMove)} onDragStart={onMove ? event => { setDragging(row.path); event.dataTransfer.effectAllowed = "move"; event.dataTransfer.setData("text/localnote-path", row.path); } : undefined} onDragEnd={onMove ? () => { setDragging(null); setOver(null); } : undefined}>
      {hasChildren
        ? <span className={`tree-toggle ${open ? "expanded" : ""}`} role="img" aria-label={open ? tr("收起", "Collapse") : tr("展开", "Expand")}
            onClick={event => { event.preventDefault(); event.stopPropagation(); onToggle(row.path); }}
            onDoubleClick={event => event.stopPropagation()}><Icon name="chevron" size={12}/></span>
        : <span className="tree-spacer"/>}
      <Icon name={folder ? "folder" : markdown ? "note" : "paperclip"} size={15}/><span>{row.name}</span></button>
    {isRenaming && <div className="tree-rename" style={{paddingLeft: 10 + row.depth*16}}>
      <input
        ref={renameInput}
        className="tree-rename-input"
        value={draft}
        aria-label={tr(`重命名 ${row.path}`, `Rename ${row.path}`)}
        onChange={event => { setDraft(event.target.value); setRenameError(null); }}
        onKeyDown={event => {
          if (event.key === "Enter") { event.preventDefault(); void submitRename(row.path); }
          else if (event.key === "Escape") { event.preventDefault(); cancelRename(); }
        }}
        onBlur={() => { if (renaming === row.path) cancelRename(); }}
      />
      {renameError && <span role="alert" className="tree-rename-error">{renameError}</span>}
    </div>}
  </div>; })}
    {menu && <ContextMenu x={menu.x} y={menu.y} label={tr("文件操作", "File actions")} items={menuItems(menu.path, entries.find(entry => entry.path === menu.path)?.kind ?? "file")} onClose={() => setMenu(null)}/>}
    {blankMenu && <ContextMenu x={blankMenu.x} y={blankMenu.y} label={tr("笔记库操作", "Vault actions")} onClose={() => setBlankMenu(null)}
      items={[{ id: "new-note", label: tr("新建文档", "New document"), icon: "note", onSelect: () => onNewRootNote?.() }]}/>}
  </div>;
}
