/**
 * Note export.
 *
 * Two layers: the pure helpers that turn a backend manifest into a printable
 * document, and the file-tree entries that expose the two export actions.
 *
 * The important invariant is the first one below — `data:` URIs must survive
 * the render pipeline when the *resolver* supplies them, because that is what
 * lets a printed note carry its images without a second, session-protected
 * request behind the reverse proxy.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "./render";
import { FileTree } from "../../apps/web/src/components/FileTree";
import { buildPrintDocument, escapeHtml, exportAttachmentMap, exportFilename, printHtmlDocument, renderExportBody, saveBlob } from "../../apps/web/src/export/noteExport";
import type { ExportNoteResponse } from "../../apps/web/src/api/types";
import type { VaultFileEntry } from "../../packages/protocol/src";

const DATA_URI = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==";

function manifest(overrides: Partial<ExportNoteResponse> = {}): ExportNoteResponse {
  return {
    path: "Notes/A.md",
    title: "A",
    download_name: "A.md",
    markdown: "# A\n\n![pic](<Notes/pic.png>)\n\n![missing](<Notes/gone.png>)\n",
    attachments: [
      { ref: "pic.png", url: "Notes/pic.png", path: "Notes/pic.png", mime: "image/png", size: 12, data_uri: DATA_URI, inlined: true, reason: null },
      { ref: "gone.png", url: "Notes/gone.png", path: "Notes/gone.png", mime: "image/png", size: 0, data_uri: null, inlined: false, reason: "attachment_not_found" },
    ],
    warnings: [{ code: "attachment_not_found", ref: "gone.png", message: "not found" }],
    inlined_bytes: 12,
    truncated: false,
    service_version: "export@1",
    generated_at: new Date().toISOString(),
    format: "manifest",
    ...overrides,
  };
}

describe("note export helpers", () => {
  it("keeps resolver-supplied data URIs while the sanitizer still strips authored ones", () => {
    const body = renderExportBody(manifest());

    // Injected by the resolver, after sanitizing: the image survives.
    expect(body).toContain(`src="${DATA_URI}"`);
    // An unresolvable reference is left as authored (no broken data URI).
    expect(body).toContain("Notes/gone.png");
    // Authored `data:` URLs are still removed by the sanitizer itself.
    expect(renderExportBody(manifest({ markdown: "![x](data:image/png;base64,AAAA)\n", attachments: [] }))).not.toContain("data:image/png");
  });

  it("maps only attachments that actually carry a payload", () => {
    const map = exportAttachmentMap(manifest());

    expect(map.get("Notes/pic.png")).toBe(DATA_URI);
    expect(map.has("Notes/gone.png")).toBe(false);
    expect(map.size).toBe(1);
  });

  it("escapes the title and embeds the body in a standalone document", () => {
    const html = buildPrintDocument({ title: '<A & "B">', bodyHtml: "<p>body</p>" });

    expect(html.startsWith("<!doctype html>")).toBe(true);
    expect(html).toContain("<title>&lt;A &amp; &quot;B&quot;&gt;</title>");
    expect(html).toContain('<article class="note"><p>body</p></article>');
    expect(html).toContain("@page");
    expect(escapeHtml("a<b>c")).toBe("a&lt;b&gt;c");
  });

  it("derives a .md filename from the note path", () => {
    expect(exportFilename("Notes/A.md")).toBe("A.md");
    expect(exportFilename("Notes/无标题")).toBe("无标题.md");
    expect(exportFilename("Notes/A.markdown")).toBe("A.markdown");
  });

  it("reports a blocked print window instead of failing silently", () => {
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    expect(printHtmlDocument("<html></html>")).toBe(false);
    open.mockRestore();
  });

  it("writes the document into the new window and prints it", async () => {
    const print = vi.fn();
    const fake = { document: { open: vi.fn(), write: vi.fn(), close: vi.fn(), images: [] }, focus: vi.fn(), print } as unknown as Window;
    const open = vi.spyOn(window, "open").mockReturnValue(fake);

    expect(printHtmlDocument("<html><body>hi</body></html>")).toBe(true);
    expect(fake.document.write).toHaveBeenCalledWith("<html><body>hi</body></html>");
    // The print call is deferred so the browser can lay the document out.
    await waitFor(() => expect(print).toHaveBeenCalled());
    open.mockRestore();
  });

  it("saves a blob through an object URL and revokes it", () => {
    vi.useFakeTimers();
    const create = vi.fn(() => "blob:localnote");
    const revoke = vi.fn();
    vi.stubGlobal("URL", { ...URL, createObjectURL: create, revokeObjectURL: revoke });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);

    saveBlob(new Blob(["x"]), "A.md");

    expect(create).toHaveBeenCalled();
    expect(click).toHaveBeenCalled();
    vi.advanceTimersByTime(5000);
    expect(revoke).toHaveBeenCalledWith("blob:localnote");
    click.mockRestore();
    vi.useRealTimers();
  });
});

describe("file tree export entries", () => {
  const entries: VaultFileEntry[] = [
    { path: "Notes.md", kind: "file", size: 4, sha256: "sha256:Notes.md" },
    { path: "pic.png", kind: "file", size: 12, sha256: "sha256:pic.png" },
  ];
  const onExportMarkdown = vi.fn();
  const onExportPdf = vi.fn();

  beforeEach(() => {
    onExportMarkdown.mockClear();
    onExportPdf.mockClear();
  });

  function renderTree() {
    render(<FileTree entries={entries} expanded={[]} onToggle={() => undefined} onOpen={() => undefined} onExportMarkdown={onExportMarkdown} onExportPdf={onExportPdf} />);
  }

  async function pick(row: string, item: string) {
    fireEvent.contextMenu(screen.getByRole("button", { name: row }));
    await userEvent.click(within(screen.getByRole("menu")).getByRole("menuitem", { name: item }));
  }

  it("offers both export actions on a note", async () => {
    renderTree();
    fireEvent.contextMenu(screen.getByRole("button", { name: "Notes.md" }));

    const menu = screen.getByRole("menu");
    expect(within(menu).getByRole("menuitem", { name: "Export Markdown" })).toBeInTheDocument();
    expect(within(menu).getByRole("menuitem", { name: "Export PDF (print)" })).toBeInTheDocument();

    await userEvent.click(within(menu).getByRole("menuitem", { name: "Export Markdown" }));
    expect(onExportMarkdown).toHaveBeenCalledWith("Notes.md");
  });

  it("routes the PDF action to its own handler", async () => {
    renderTree();

    await pick("Notes.md", "Export PDF (print)");

    expect(onExportPdf).toHaveBeenCalledWith("Notes.md");
    expect(onExportMarkdown).not.toHaveBeenCalled();
  });

  it("never offers export for a non-Markdown attachment", async () => {
    renderTree();
    fireEvent.contextMenu(screen.getByRole("button", { name: "pic.png" }));

    expect(screen.queryByRole("menuitem", { name: "Export Markdown" })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Export PDF (print)" })).not.toBeInTheDocument();
  });
});
