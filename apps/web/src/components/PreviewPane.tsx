import { Preview } from "@localnote/markdown";
import type { DragEvent } from "react";
import { useI18n } from "../i18n";

/** Present frontmatter separately; the editor remains the sole owner of source bytes. */
export function PreviewPane({ source, notePath, resolveResourceUrl, onDropFiles, attachmentBusy = false, disabled = false, wikilinkExists, onOpenWikilink }: { source: string; notePath?: string | null; resolveResourceUrl?: (path: string) => string; onDropFiles?: (files: File[]) => void; attachmentBusy?: boolean; disabled?: boolean; wikilinkExists?: (target: string) => boolean; onOpenWikilink?: (target: string) => void }) {
  const { tr } = useI18n();
  const frontmatter = /^\uFEFF?---\r?\n([\s\S]*?)\r?\n(?:---|\.\.\.)(?:\r?\n|$)/.exec(source);
  const acceptDrop = Boolean(onDropFiles) && !disabled;
  return <div className="preview-pane">
    {frontmatter && <details className="note-properties"><summary>{tr("笔记属性", "Note properties")}</summary><pre>{frontmatter[1]}</pre></details>}
    <Preview
      source={frontmatter ? source.slice(frontmatter[0].length) : source}
      notePath={notePath}
      resolveResourceUrl={resolveResourceUrl}
      busy={attachmentBusy}
      wikilinkExists={wikilinkExists}
      onOpenWikilink={onOpenWikilink}
      onDropFiles={acceptDrop ? onDropFiles : undefined}
      onDragOver={acceptDrop ? (event: DragEvent<HTMLElement>) => { event.preventDefault(); if (event.dataTransfer) event.dataTransfer.dropEffect = "copy"; } : undefined}
    />
  </div>;
}
