import { useMemo, useRef } from "react";
import type { DragEvent, KeyboardEvent, MouseEvent } from "react";
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
  /** Build a local PDF preview URL for Office/PDF attachments. */
  resolveDocumentPreviewUrl?: (path: string) => string;
  /** Drag/drop upload support for the preview area. */
  onDropFiles?: (files: File[]) => void;
  onDragOver?: (event: DragEvent<HTMLElement>) => void;
  onDragLeave?: (event: DragEvent<HTMLElement>) => void;
  busy?: boolean;
  /** True when a `[[wikilink]]` target already exists in the Vault. */
  wikilinkExists?: (target: string) => boolean;
  /** Click on a `[[wikilink]]`: open the note, or create it when missing. */
  onOpenWikilink?: (target: string) => void;
  /** Click on a task checkbox: flip that item in the source. */
  onToggleTask?: (index: number) => void;
  /** Start transcription for an embedded audio reference. */
  onTranscribeAudio?: (path: string) => void;
}

function splitReference(url: string): { path: string; fragment: string } {
  const hash = url.indexOf("#");
  const query = url.indexOf("?");
  const cut = [hash, query].filter(index => index >= 0).sort((a, b) => a - b)[0] ?? url.length;
  const fragment = hash >= 0 && (query < 0 || hash < query) ? url.slice(hash) : "";
  return { path: url.slice(0, cut), fragment: /^#page=\d+$/i.test(fragment) ? fragment : "" };
}

export function Preview({ source, className, notePath, resolveResourceUrl, resolveDocumentPreviewUrl, onDropFiles, onDragOver, onDragLeave, busy, wikilinkExists, onOpenWikilink, onToggleTask, onTranscribeAudio }: PreviewProps) {
  const resolveNotePath = (url: string, resolver?: (path: string) => string) => {
    const reference = splitReference(url);
    const path = resolveVaultRelativePath(notePath, reference.path);
    return path && resolver ? `${resolver(path)}${reference.fragment}` : null;
  };
  const html = useMemo(() => renderMarkdown(source, {
    resolveUrl: (url) => {
      if (!resolveResourceUrl) return url;
      // ``resolveVaultRelativePath`` decodes authored percent-escapes, so
      // the resource URL is encoded exactly once.
      return resolveNotePath(url, resolveResourceUrl);
    },
    resolveDocumentUrl: resolveDocumentPreviewUrl
      ? (url) => resolveNotePath(url, resolveDocumentPreviewUrl)
      : undefined,
    enableAudioTranscription: Boolean(onTranscribeAudio),
  }), [source, notePath, resolveResourceUrl, resolveDocumentPreviewUrl, onTranscribeAudio]);

  const click = onOpenWikilink || onToggleTask || onTranscribeAudio
    ? (event: MouseEvent<HTMLElement>) => {
        const target = event.target as HTMLElement | null;
        const transcribe = target?.closest?.("button.audio-transcribe") as HTMLElement | null;
        if (transcribe && onTranscribeAudio) {
          event.preventDefault();
          const reference = transcribe.getAttribute("data-audio-reference");
          const path = reference ? resolveVaultRelativePath(notePath, splitReference(reference).path) : null;
          if (path) onTranscribeAudio(path);
          return;
        }
        const toggle = target?.closest?.("span.task-toggle") as HTMLElement | null;
        if (toggle && onToggleTask) {
          event.preventDefault();
          const index = Number(toggle.getAttribute("data-task-index"));
          if (Number.isInteger(index) && index >= 0) onToggleTask(index);
          return;
        }
        const anchor = target?.closest?.("a.wikilink");
        if (!anchor || !onOpenWikilink) return;
        event.preventDefault();
        const value = anchor.getAttribute("data-wikilink");
        if (value) onOpenWikilink(value);
      }
    : undefined;

  // A missing target is styled as create-able; the class is added here (not in
  // the renderer) because only the workspace knows what the Vault contains.
  const existsRef = useRef(wikilinkExists);
  existsRef.current = wikilinkExists;
  const decorated = useMemo(() => {
    if (!wikilinkExists || typeof document === "undefined") return html;
    const host = document.createElement("div");
    host.innerHTML = html;
    for (const anchor of Array.from(host.querySelectorAll("a.wikilink"))) {
      const target = anchor.getAttribute("data-wikilink");
      if (target && !existsRef.current?.(target)) anchor.classList.add("wikilink-missing");
    }
    return host.innerHTML;
  }, [html]);

  const keyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const toggle = (event.target as HTMLElement).closest?.("span.task-toggle") as HTMLElement | null;
    if (!toggle || !onToggleTask) return;
    event.preventDefault();
    const index = Number(toggle.getAttribute("data-task-index"));
    if (Number.isInteger(index) && index >= 0) onToggleTask(index);
  };

  return <article
    className={[className, busy ? "attachment-busy" : ""].filter(Boolean).join(" ") || undefined}
    aria-label="Markdown preview"
    aria-busy={busy ? true : undefined}
    onClick={click}
    onKeyDown={keyDown}
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
