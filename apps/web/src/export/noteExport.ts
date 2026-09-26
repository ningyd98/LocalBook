/**
 * Note export — the browser half.
 *
 * The backend resolves the note's references and hands back both a
 * Vault-relative `markdown` body and the `data:` URI of every attachment it
 * could embed. Rendering therefore needs no second round-trip per image: the
 * resolver below is a pure map lookup, and the printed document is fully
 * self-contained (no session-protected `/vault/resource` requests, which is
 * exactly what makes export work behind the public reverse proxy).
 */
import { renderMarkdown } from "@localnote/markdown";
import type { ExportNoteResponse } from "../api/types";

/** `url` (as written into the manifest Markdown) → `data:` URI. */
export function exportAttachmentMap(manifest: ExportNoteResponse): Map<string, string> {
  const map = new Map<string, string>();
  for (const item of manifest.attachments) if (item.data_uri) map.set(item.url, item.data_uri);
  return map;
}

/** Body HTML for the note, with every embeddable attachment inlined. */
export function renderExportBody(manifest: ExportNoteResponse): string {
  const attachments = exportAttachmentMap(manifest);
  return renderMarkdown(manifest.markdown, { resolveUrl: url => attachments.get(url) ?? null });
}

/**
 * Self-contained print stylesheet.
 *
 * The export window is a blank document, so it cannot rely on the app's
 * Tailwind build; everything is styled by tag here. `break-inside: avoid` keeps
 * images and tables from being sliced by a page boundary.
 */
export const PRINT_STYLES = `
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; padding: 32px 40px; background: #fff; color: #1b1b1f;
  font: 15px/1.75 -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Helvetica Neue", Arial, sans-serif; }
.note { max-width: 46rem; margin: 0 auto; }
h1, h2, h3, h4, h5, h6 { margin: 1.6em 0 .55em; line-height: 1.3; font-weight: 650; }
h1 { font-size: 1.9em; } h2 { font-size: 1.5em; } h3 { font-size: 1.25em; } h4 { font-size: 1.1em; }
h1:first-child, h2:first-child, h3:first-child { margin-top: 0; }
p, ul, ol { margin: .75em 0; }
img { max-width: 100%; height: auto; }
a { color: #1a5fb4; text-decoration: none; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .92em;
  background: #f2f2f5; padding: .1em .34em; border-radius: 4px; }
pre { background: #f6f6f8; padding: 12px 14px; border-radius: 8px; overflow-wrap: anywhere; white-space: pre-wrap; }
pre code { background: none; padding: 0; }
blockquote { margin: 1em 0; padding: .15em 0 .15em 1em; border-left: 3px solid #d0d0d8; color: #55555f; }
table { border-collapse: collapse; width: 100%; margin: 1em 0; }
th, td { border: 1px solid #dcdce4; padding: 6px 9px; text-align: left; vertical-align: top; }
th { background: #f7f7fa; }
hr { border: none; border-top: 1px solid #e5e5ec; margin: 2em 0; }
.task-toggle { display: inline-block; width: 1em; }
h1, h2, h3, h4, img, table, pre, blockquote, li { break-inside: avoid; }
@page { margin: 16mm 14mm; }
@media print { body { padding: 0; } .note { max-width: none; } }
`;

const ESCAPES: Record<string, string> = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

export function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, char => ESCAPES[char] ?? char);
}

/** Complete HTML document for one exported note. */
export function buildPrintDocument({ title, bodyHtml }: { title: string; bodyHtml: string }): string {
  return [
    "<!doctype html>",
    '<html lang="zh-CN">',
    "<head>",
    '<meta charset="utf-8">',
    '<meta name="viewport" content="width=device-width, initial-scale=1">',
    `<title>${escapeHtml(title)}</title>`,
    `<style>${PRINT_STYLES}</style>`,
    "</head>",
    "<body>",
    `<article class="note">${bodyHtml}</article>`,
    "</body>",
    "</html>",
  ].join("\n");
}

/**
 * Open `html` in a new window and print it (the browser's "Save as PDF" is the
 * PDF writer). Returns `false` when the window was blocked, so the caller can
 * explain instead of failing silently.
 */
export function printHtmlDocument(html: string): boolean {
  const target = typeof window === "undefined" ? null : window.open("", "_blank");
  if (!target) return false;
  target.document.open();
  target.document.write(html);
  target.document.close();
  const run = () => { try { target.focus(); target.print(); } catch { /* the user can still print manually */ } };
  const images = Array.from(target.document.images ?? []) as HTMLImageElement[];
  const pending = images.filter(image => !image.complete);
  if (!pending.length) { setTimeout(run, 60); return true; }
  let settled = 0;
  const done = () => { settled += 1; if (settled >= pending.length) setTimeout(run, 60); };
  for (const image of pending) { image.addEventListener("load", done); image.addEventListener("error", done); }
  // Never hang on an image that neither loads nor errors.
  setTimeout(() => { if (settled < pending.length) run(); }, 3000);
  return true;
}

/** Note path → the filename a browser download should use. */
export function exportFilename(path: string): string {
  const name = path.split("/").at(-1) || "note";
  return /\.(md|markdown)$/i.test(name) ? name : `${name}.md`;
}

/** Trigger a client-side download for an already-fetched Blob. */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.rel = "noopener";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}
