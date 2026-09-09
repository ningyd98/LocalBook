import { useMemo } from "react";
import type { DragEvent, MouseEvent } from "react";
import { resolveVaultRelativePath } from "@localnote/protocol";
import { renderMarkdown } from "./render";

export interface PreviewProps {
  source: string;
  className?: string;
  /**
   * Vault-root-relative path of the note being previewed. Relative image/link
   * references are resolved against its directory before rewriting.
   */
  notePath?: string | null;
  /**
   * Build the read-only URL for a Vault-root-relative resource path
   * (normally `/api/v1/vault/resource?path=...`). When omitted, relative
   * references are left exactly as authored.
   */
  resolveResourceUrl?: (path: string) => string;
  /** Drag/drop upload support for the preview area. */
  onDropFiles?: (files: File[]) => void;
  onDragOver?: (event: DragEvent<HTMLElement>) => void;
  onDragLeave?: (event: DragEvent<HTMLElement>) => void;
  busy?: boolean;
  /** True when a `[[wikilink]]` target already exists in the Vault. */
  wikilinkExists?: (target: string) => boolean;
  /** Click on a `[[wikilink]]`: open the note, or create it when missing. */
  onOpenWikilink?: (target: string) => void;
}

export function Preview({ source, className, notePath, resolveResourceUrl, onDropFiles, onDragOver, onDragLeave, busy, wikilinkExists, onOpenWikilink }: PreviewProps) {
  const html = useMemo(() => {
    if (!resolveResourceUrl) return renderMarkdown(source);
    return renderMarkdown(source, {
      resolveUrl: (url) => {
        // ``resolveVaultRelativePath`` decodes authored percent-escapes, so
        // the resource URL is encoded exactly once.
        const path = resolveVaultRelativePath(notePath, url);
        return path ? resolveResourceUrl(path) : null;
      },
    });
  }, [source, notePath, resolveResourceUrl]);

  const click = onOpenWikilink
    ? (event: MouseEvent<HTMLElement>) => {
        const anchor = (event.target as HTMLElement | null)?.closest?.("a.wikilink");
        if (!anchor) return;
        event.preventDefault();
        const target = anchor.getAttribute("data-wikilink");
        if (target) onOpenWikilink(target);
      }
    : undefined;

  // A missing target is styled as create-able; the class is added here (not in
  // the renderer) because only the workspace knows what the Vault contains.
  const decorated = useMemo(() => {
    if (!wikilinkExists || typeof document === "undefined") return html;
    const host = document.createElement("div");
    host.innerHTML = html;
    for (const anchor of Array.from(host.querySelectorAll("a.wikilink"))) {
      const target = anchor.getAttribute("data-wikilink");
      if (target && !wikilinkExists(target)) anchor.classList.add("wikilink-missing");
    }
    return host.innerHTML;
  }, [html, wikilinkExists]);

  return <article
    className={[className, busy ? "attachment-busy" : ""].filter(Boolean).join(" ") || undefined}
    aria-label="Markdown preview"
    aria-busy={busy ? true : undefined}
    onClick={click}
    onDragOver={onDragOver}
    onDragLeave={onDragLeave}
    onDrop={onDropFiles ? (event) => {
      const files = Array.from(event.dataTransfer?.files ?? []);
      if (!files.length) return;
      event.preventDefault();
      onDropFiles(files);
    } : undefined}
    dangerouslySetInnerHTML={{ __html: decorated }} />;
}
