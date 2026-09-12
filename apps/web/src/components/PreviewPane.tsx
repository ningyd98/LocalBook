import { Preview } from "@localnote/markdown";
import type { DragEvent } from "react";
import { useI18n } from "../i18n";

/** Present frontmatter separately; the editor remains the sole owner of source bytes. */
export function PreviewPane({ source, notePath, resolveResourceUrl, onDropFiles, attachmentBusy = false, disabled = false, wikilinkExists, onOpenWikilink, onToggleTask, onContextMenuAt }: { source: string; notePath?: string | null; resolveResourceUrl?: (path: string) => string; onDropFiles?: (files: File[]) => void; attachmentBusy?: boolean; disabled?: boolean; wikilinkExists?: (target: string) => boolean; onOpenWikilink?: (target: string) => void; onToggleTask?: (index: number) => void; /** Right-click on the preview surface (nested-document menu). */ onContextMenuAt?: (path: string, x: number, y: number) => void }) {
  const { tr } = useI18n();
  const frontmatter = /^\uFEFF?---\r?\n([\s\S]*?)\r?\n(?:---|\.\.\.)(?:\r?\n|$)/.exec(source);
  const acceptDrop = Boolean(onDropFiles) && !disabled;
  // The preview never owns the bytes, so a right-click only opens the document
  // menu; links and other inline widgets keep their own affordances.
  const documentMenu = onContextMenuAt && notePath
    ? (event: { preventDefault: () => void; clientX: number; clientY: number }) => { event.preventDefault(); onContextMenuAt(notePath, event.clientX, event.clientY); }
    : undefined;
  return <div className="preview-pane" onContextMenu={documentMenu}>
    {frontmatter && <details className="note-properties"><summary>{tr("笔记属性", "Note properties")}</summary><pre>{frontmatter[1]}</pre></details>}
    <Preview
      source={frontmatter ? source.slice(frontmatter[0].length) : source}
      notePath={notePath}
      resolveResourceUrl={resolveResourceUrl}
      busy={attachmentBusy}
      wikilinkExists={wikilinkExists}
      onOpenWikilink={onOpenWikilink}
      onToggleTask={onToggleTask}
      onDropFiles={acceptDrop ? onDropFiles : undefined}
      onDragOver={acceptDrop ? (event: DragEvent<HTMLElement>) => { event.preventDefault(); if (event.dataTransfer) event.dataTransfer.dropEffect = "copy"; } : undefined}
    />
  </div>;
}
