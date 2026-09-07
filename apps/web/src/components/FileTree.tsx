import { useMemo } from "react";
import { Button, TreeRow } from "@localnote/ui";
import type { VaultFileEntry } from "@localnote/protocol";
import { isEditableMarkdown } from "@localnote/workspace";

export function FileTree({ entries, expanded, onToggle, onOpen }: { entries: VaultFileEntry[]; expanded: string[]; onToggle: (p: string) => void; onOpen: (p: string) => void }) {
  const visible = useMemo(() => {
    const expandedSet = new Set(expanded);
    return entries.filter((entry) => {
      const parts = entry.path.split("/");
      if (parts.some((part) => part.startsWith("."))) return false;
      for (let i = 1; i < parts.length; i++) if (!expandedSet.has(parts.slice(0, i).join("/"))) return false;
      return true;
    }).sort((a, b) => {
      if (a.kind !== b.kind) return a.kind === "directory" ? -1 : 1;
      return a.path.localeCompare(b.path);
    });
  }, [entries, expanded]);
  return <div role="tree" className="file-tree">{visible.map((entry) => { const depth = entry.path.split("/").length - 1; const open = expanded.includes(entry.path); return <TreeRow key={entry.path} style={{ paddingLeft: depth * 14 }} className={!isEditableMarkdown(entry.path) && entry.kind === "file" ? "unsupported" : ""}><Button disabled={entry.kind === "file" && !isEditableMarkdown(entry.path)} onClick={() => entry.kind === "directory" ? onToggle(entry.path) : onOpen(entry.path)} aria-expanded={entry.kind === "directory" ? open : undefined}>{entry.kind === "directory" ? (open ? "▾ " : "▸ ") : "📄 "}{entry.path.split("/").at(-1)}</Button></TreeRow>; })}</div>;
}
