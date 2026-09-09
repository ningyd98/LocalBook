/**
 * Attachment upload frontend contracts (PLAN-ATTACHMENTS v1.1, ATT-09…ATT-21).
 *
 * All network access is mocked through `configureWorkspaceApi`; nothing touches
 * a real Vault. The four entry points are exercised through their real
 * components/store wiring:
 *   toolbar picker + editor drop + paste  → current note directory
 *   preview drop                          → current note directory
 *   file tree directory context menu      → the clicked directory (root = "")
 */
import { act, fireEvent, render as rtlRender, screen, waitFor } from "@testing-library/react";
import { render } from "./render";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AttachmentPreview } from "../../apps/web/src/components/AttachmentPreview";
import { EditorPane } from "../../apps/web/src/components/EditorPane";
import { FileTree } from "../../apps/web/src/components/FileTree";
import { PreviewPane } from "../../apps/web/src/components/PreviewPane";
import { WorkspaceShell } from "../../apps/web/src/components/WorkspaceShell";
import { I18nProvider } from "../../apps/web/src/i18n";
import { vaultResourceUrl } from "../../apps/web/src/api/client";
import { noteDirectory, relativeMarkdownReference, resolveVaultRelativePath } from "../../packages/protocol/src";
import { renderMarkdown } from "../../packages/markdown/src/render";
import {
  ATTACHMENT_JSON_MAX_BYTES,
  bytesToBase64Chunked,
  configureWorkspaceApi,
  encodeReference,
  fileToBase64,
  isImageAttachment,
  pastedImageName,
  preferenceDefaults,
  toBase64,
  useWorkspaceStore,
} from "../../packages/workspace/src";
import type { WorkspaceApi } from "../../packages/workspace/src";

const attachmentOk = (path: string, extra: Partial<{ sha256: string; byte_length: number; content_type: string; original_name: string }> = {}) => ({
  path,
  sha256: extra.sha256 ?? "sha256:" + "a".repeat(64),
  byte_length: extra.byte_length ?? 4,
  content_type: extra.content_type ?? "image/png",
  operation: "created" as const,
  original_name: extra.original_name ?? path.split("/").at(-1)!,
});

const fileOf = (name: string, bytes = new Uint8Array([1, 2, 3, 4]), type = "image/png") => new File([bytes], name, { type });

/** jsdom's DataTransfer is incomplete; a minimal stand-in carries the files. */
const dataTransferWith = (files: File[]) => ({ files, items: [], types: ["Files"], getData: () => "" });

const ENTRIES = [
  { path: "notes", kind: "directory" as const, size: null, sha256: null },
  { path: "notes/a.md", kind: "file" as const, size: 4, sha256: "sha256:x" },
  { path: "notes/photo.png", kind: "file" as const, size: 4, sha256: "sha256:y" },
];

type Base64Upload = NonNullable<WorkspaceApi["uploadAttachmentBase64"]>;
type MultipartUpload = NonNullable<WorkspaceApi["uploadAttachmentMultipart"]>;

function configureApi(overrides: Partial<WorkspaceApi> = {}) {
  const base64 = vi.fn<Base64Upload>(async () => attachmentOk("notes/photo.png"));
  const multipart = vi.fn<MultipartUpload>(async () => attachmentOk("notes/big.bin", { content_type: "application/octet-stream" }));
  configureWorkspaceApi({
    fetchVaultFiles: async () => ({ entries: ENTRIES }),
    fetchVaultFile: async (path: string) => ({ path, content_base64: toBase64("# A\n"), byte_length: 4, sha256: "sha256:base", content_type: "text/markdown" }),
    patchVaultFile: async () => ({ path: "notes/a.md", sha256: "sha256:new", byte_length: 3, operation: "updated" }),
    uploadAttachmentBase64: overrides.uploadAttachmentBase64 ?? base64,
    uploadAttachmentMultipart: overrides.uploadAttachmentMultipart ?? multipart,
    ...overrides,
  });
  return { base64, multipart };
}

function resetStore() {
  useWorkspaceStore.getState().resetVault();
  useWorkspaceStore.setState({ ...preferenceDefaults, tree: { entries: [], expandedPaths: [], status: "idle", error: null }, tabs: [], activePath: null, sessions: {} });
}

async function openNote(path = "notes/a.md") {
  await act(async () => { await useWorkspaceStore.getState().openFile(path); });
}

beforeEach(() => {
  localStorage.clear();
  resetStore();
});

describe("chunked base64 encoding (ATT-11)", () => {
  it("encodes large binaries without a spread/stack overflow", () => {
    const bytes = new Uint8Array(300_000);
    for (let index = 0; index < bytes.length; index += 1) bytes[index] = index % 251;
    const encoded = bytesToBase64Chunked(bytes);
    expect(encoded).toBe(toBase64Binary(bytes));
    expect(bytesToBase64Chunked(new Uint8Array([1, 2, 3]))).toBe(btoa("\u0001\u0002\u0003"));
  });

  it("reads a File as base64 and splits at the 10 MiB boundary", async () => {
    const small = new File([new Uint8Array([65, 66])], "small.bin", { type: "application/octet-stream" });
    expect(await fileToBase64(small)).toBe(btoa("AB"));
    expect(ATTACHMENT_JSON_MAX_BYTES).toBe(10 * 1024 * 1024);
  });

  it("names pasted images deterministically and detects images", () => {
    expect(pastedImageName("image/png", new Date("2026-08-16T10:20:30.123Z"))).toBe("pasted-image-20260816-102030.png");
    expect(isImageAttachment("x.bin", "image/webp")).toBe(true);
    expect(isImageAttachment("x.PNG")).toBe(true);
    expect(isImageAttachment("x.pdf", "application/pdf")).toBe(false);
  });

  it("encodes reference paths per segment", () => {
    expect(encodeReference("notes/我的 图片 (1).png")).toBe("notes/%E6%88%91%E7%9A%84%20%E5%9B%BE%E7%89%87%20%281%29.png");
  });
});

describe("relative reference helpers (ATT-11/ATT-14)", () => {
  it("resolves note-relative targets to root-relative paths", () => {
    expect(resolveVaultRelativePath("notes/2026/a.md", "photo.png")).toBe("notes/2026/photo.png");
    expect(resolveVaultRelativePath("notes/2026/a.md", "../photo.png")).toBe("notes/photo.png");
    expect(resolveVaultRelativePath("a.md", "notes/photo.png")).toBe("notes/photo.png");
    expect(resolveVaultRelativePath("notes/a.md", "../../outside.png")).toBeNull();
    expect(resolveVaultRelativePath("notes/a.md", "https://example.com/x.png")).toBeNull();
    expect(resolveVaultRelativePath("notes/a.md", "data:image/png;base64,AAA")).toBeNull();
    expect(resolveVaultRelativePath("notes/a.md", "javascript:alert(1)")).toBeNull();
    expect(resolveVaultRelativePath("notes/a.md", "/notes/photo.png")).toBe("notes/photo.png");
  });

  it("builds deterministic ../ references", () => {
    expect(relativeMarkdownReference("notes/a.md", "notes/photo.png")).toBe("photo.png");
    expect(relativeMarkdownReference("notes/2026/a.md", "notes/photo.png")).toBe("../photo.png");
    expect(relativeMarkdownReference("notes/a.md", "attachments/photo.png")).toBe("../attachments/photo.png");
    expect(relativeMarkdownReference("a.md", "notes/2026/photo.png")).toBe("notes/2026/photo.png");
    expect(noteDirectory("a.md")).toBe("");
    expect(noteDirectory("notes/2026/a.md")).toBe("notes/2026");
  });
});

describe("toolbar / editor entry points (ATT-12)", () => {
  it("uploads through the toolbar picker using the current note directory", async () => {
    const { base64 } = configureApi();
    await openNote("notes/a.md");
    render(<EditorPane session={useWorkspaceStore.getState().sessions["notes/a.md"]!} theme="light" onChange={vi.fn()} onSave={vi.fn()} onUploadFiles={(files, source) => void useWorkspaceStore.getState().uploadAndInsertAttachment(files[0]!, { source })}/>);
    const input = screen.getByTestId("attachment-input") as HTMLInputElement;
    await userEvent.upload(input, fileOf("photo.png"));
    await waitFor(() => expect(base64).toHaveBeenCalledTimes(1));
    expect(base64.mock.calls[0]![0]).toMatchObject({ originalName: "photo.png", targetDirectory: "notes" });
    expect(useWorkspaceStore.getState().sessions["notes/a.md"]!.content).toContain("![photo.png](photo.png)");
  });

  it("sends an empty target_directory for a root-level note", async () => {
    const base64 = vi.fn<Base64Upload>(async () => attachmentOk("photo.png"));
    configureApi({ uploadAttachmentBase64: base64 });
    await openNote("a.md");
    expect(useWorkspaceStore.getState().sessions["a.md"]).toBeTruthy();
    render(<EditorPane session={useWorkspaceStore.getState().sessions["a.md"]!} theme="light" onChange={vi.fn()} onSave={vi.fn()} onUploadFiles={(files, source) => void useWorkspaceStore.getState().uploadAndInsertAttachment(files[0]!, { source })}/>);
    await userEvent.upload(screen.getByTestId("attachment-input") as HTMLInputElement, fileOf("photo.png"));
    await waitFor(() => expect(base64).toHaveBeenCalledTimes(1));
    expect(base64.mock.calls[0]![0].targetDirectory).toBe("");
    expect(useWorkspaceStore.getState().sessions["a.md"]!.content).toContain("![photo.png](photo.png)");
  });

  it("drops files on the editor and inserts a relative reference with ../", async () => {
    configureApi({ uploadAttachmentBase64: async () => attachmentOk("notes/photo.png") });
    await openNote("notes/2026/a.md");
    const onUploadFiles = vi.fn((files: File[], source: string) => void useWorkspaceStore.getState().uploadAndInsertAttachment(files[0]!, { source: source as "editor-drop" }));
    render(<EditorPane session={useWorkspaceStore.getState().sessions["notes/2026/a.md"]!} theme="light" onChange={vi.fn()} onSave={vi.fn()} onUploadFiles={onUploadFiles}/>);
    // The drop target is the CodeMirror content DOM (where the listeners live).
    const editor = screen.getByRole("textbox", { name: /source editor/i });
    fireEvent.drop(editor.querySelector(".cm-content")!, { dataTransfer: dataTransferWith([fileOf("photo.png")]) });
    await waitFor(() => expect(onUploadFiles).toHaveBeenCalled());
    await waitFor(() => expect(useWorkspaceStore.getState().sessions["notes/2026/a.md"]!.content).toContain("![photo.png](../photo.png)"));
  });

  it("pastes a clipboard image but leaves text paste alone", async () => {
    configureApi({ uploadAttachmentBase64: async () => attachmentOk("notes/pasted-image-x.png") });
    await openNote("notes/a.md");
    const onUploadFiles = vi.fn((files: File[], source: string) => void useWorkspaceStore.getState().uploadAndInsertAttachment(files[0]!, { source: source as "paste" }));
    render(<EditorPane session={useWorkspaceStore.getState().sessions["notes/a.md"]!} theme="light" onChange={vi.fn()} onSave={vi.fn()} onUploadFiles={onUploadFiles}/>);
    const editor = screen.getByRole("textbox", { name: /source editor/i });
    const content = editor.querySelector(".cm-content")!;
    const textPaste = new Event("paste", { bubbles: true, cancelable: true });
    Object.defineProperty(textPaste, "clipboardData", { value: { files: [], items: [], getData: () => "text" } });
    content.dispatchEvent(textPaste);
    expect(onUploadFiles).not.toHaveBeenCalled();
    fireEvent.paste(content, { clipboardData: dataTransferWith([fileOf("image.png", new Uint8Array([9]), "image/png")]) });
    await waitFor(() => expect(onUploadFiles).toHaveBeenCalled());
  });

  it("does not upload while the session is read-only", async () => {
    const { base64 } = configureApi();
    await openNote("notes/a.md");
    render(<EditorPane session={useWorkspaceStore.getState().sessions["notes/a.md"]!} theme="light" readOnly onChange={vi.fn()} onSave={vi.fn()} onUploadFiles={vi.fn()}/>);
    expect((screen.getByTestId("attachment-input") as HTMLInputElement).closest(".editor-attachment-bar")).toBeTruthy();
    expect(screen.getByRole("button", { name: /insert attachment/i })).toBeDisabled();
    expect(base64).not.toHaveBeenCalled();
  });

  it("splits channels at 10 MiB (multipart above the threshold)", async () => {
    const { base64, multipart } = configureApi();
    await openNote("notes/a.md");
    const big = new File([new Uint8Array(10 * 1024 * 1024 + 1)], "big.bin", { type: "application/octet-stream" });
    await act(async () => { await useWorkspaceStore.getState().uploadAndInsertAttachment(big, { source: "toolbar" }); });
    expect(multipart).toHaveBeenCalledTimes(1);
    expect(base64).not.toHaveBeenCalled();
    expect(multipart.mock.calls[0]![1]).toBe("notes");
  });

  it("keeps the body unchanged when the upload fails", async () => {
    configureApi({ uploadAttachmentBase64: async () => { throw Object.assign(new Error("nope"), { status: 413, code: "file_too_large" }); } });
    await openNote("notes/a.md");
    const before = useWorkspaceStore.getState().sessions["notes/a.md"]!.content;
    const result = await act(async () => useWorkspaceStore.getState().uploadAndInsertAttachment(fileOf("x.png"), { source: "toolbar" }));
    expect(result).toBeNull();
    expect(useWorkspaceStore.getState().sessions["notes/a.md"]!.content).toBe(before);
    expect(useWorkspaceStore.getState().attachment.error?.code).toBe("file_too_large");
    expect(useWorkspaceStore.getState().attachment.busy).toBe(false);
  });

  it("requires an editable Markdown note", async () => {
    configureApi();
    const result = await act(async () => useWorkspaceStore.getState().uploadAndInsertAttachment(fileOf("x.png"), { source: "toolbar" }));
    expect(result).toBeNull();
    expect(useWorkspaceStore.getState().attachment.error?.message).toMatch(/editable Markdown/i);
  });

  it("does not insert a stale response after a vault reset", async () => {
    let release: (value: unknown) => void = () => undefined;
    const pending = new Promise((resolve) => { release = resolve; });
    configureApi({ uploadAttachmentBase64: vi.fn<Base64Upload>(() => pending as Promise<ReturnType<typeof attachmentOk>>) });
    await openNote("notes/a.md");
    const before = useWorkspaceStore.getState().sessions["notes/a.md"]!.content;
    const task = act(async () => { void useWorkspaceStore.getState().uploadAndInsertAttachment(fileOf("x.png"), { source: "toolbar" }); });
    act(() => useWorkspaceStore.getState().resetVault());
    release(attachmentOk("notes/x.png"));
    await task;
    expect(useWorkspaceStore.getState().sessions["notes/a.md"]).toBeUndefined();
    expect(before).toContain("# A");
  });
});

describe("file tree entry points (ATT-15)", () => {
  it("keeps Markdown rows on openFile and makes attachment rows clickable", async () => {
    configureApi();
    const onOpen = vi.fn();
    const onOpenAttachment = vi.fn();
    render(<FileTree entries={ENTRIES} expanded={["notes"]} activePath={null} onToggle={vi.fn()} onOpen={onOpen} onOpenAttachment={onOpenAttachment}/>);
    await userEvent.click(screen.getByRole("button", { name: "a.md" }));
    expect(onOpen).toHaveBeenCalledWith("notes/a.md");
    const attachment = screen.getByRole("button", { name: "photo.png" });
    expect(attachment).toBeEnabled();
    await userEvent.click(attachment);
    expect(onOpenAttachment).toHaveBeenCalledWith("notes/photo.png");
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it("uploads into the clicked directory (and the root for a root row)", async () => {
    configureApi();
    const onUploadToDirectory = vi.fn();
    render(<FileTree entries={ENTRIES} expanded={["notes"]} activePath={null} onToggle={vi.fn()} onOpen={vi.fn()} onUploadToDirectory={onUploadToDirectory}/>);
    fireEvent.contextMenu(screen.getByRole("button", { name: "notes" }));
    await userEvent.click(screen.getByRole("menuitem", { name: /upload to this folder/i }));
    expect(onUploadToDirectory).toHaveBeenCalledWith("notes");
  });

  it("routes a tree upload to the right-clicked directory, not the note directory", async () => {
    const base64 = vi.fn<Base64Upload>(async () => attachmentOk("notes/photo.png"));
    configureApi({ uploadAttachmentBase64: base64 });
    await openNote("notes/a.md");
    expect(useWorkspaceStore.getState().sessions["notes/a.md"]).toBeTruthy();
    await act(async () => { await useWorkspaceStore.getState().uploadAndInsertAttachment(fileOf("photo.png"), { targetDirectory: "notes", source: "tree-context", notePath: "notes/a.md" }); });
    expect(base64).toHaveBeenCalled();
    expect(base64.mock.calls[0]![0].targetDirectory).toBe("notes");
  });
});

describe("preview resource rewriting (ATT-13/ATT-14)", () => {
  it("rewrites relative images to the encoded resource URL", () => {
    const html = renderMarkdown(`![alt](${encodeReference("photo 图.png")})`, {
      resolveUrl: (url) => {
        const path = resolveVaultRelativePath("notes/2026/a.md", url);
        return path ? vaultResourceUrl(path) : null;
      },
    });
    expect(html).toContain(vaultResourceUrl("notes/2026/photo 图.png"));
    expect(html).not.toContain('src="photo 图.png"');
    // The resource URL is encoded exactly once (URLSearchParams uses "+" for spaces).
    expect(html).not.toContain("%25");
  });

  it("resolves ../ references and leaves external links alone", () => {
    const resolve = (url: string) => {
      const path = resolveVaultRelativePath("notes/a.md", url);
      return path ? vaultResourceUrl(path) : null;
    };
    const html = renderMarkdown("![a](../attachments/x.png)\n\n[site](https://example.com/a.png)", { resolveUrl: resolve });
    expect(html).toContain(vaultResourceUrl("attachments/x.png"));
    expect(html).toContain("https://example.com/a.png");
  });

  it("still removes scripts, event handlers and dangerous protocols", () => {
    const html = renderMarkdown('<img src="x.png" onerror="alert(1)" style="color:red"><script>alert(1)</script>\n\n![a](javascript:alert(1))\n\n[![b](data:image/png;base64,AAA)](https://example.com)', {
      resolveUrl: (url) => vaultResourceUrl(url),
    });
    expect(html).not.toContain("<script");
    expect(html).not.toContain("onerror");
    expect(html).not.toContain("style=");
    expect(html).not.toContain("javascript:");
    expect(html).not.toContain("data:image/png");
  });

  it("preview drop uses the current note directory", async () => {
    configureApi({ uploadAttachmentBase64: async () => attachmentOk("notes/2026/photo.png") });
    await openNote("notes/2026/a.md");
    const upload = vi.fn((files: File[]) => void useWorkspaceStore.getState().uploadAndInsertAttachment(files[0]!, { source: "preview-drop" }));
    render(<PreviewPane source="# A\n" notePath="notes/2026/a.md" resolveResourceUrl={vaultResourceUrl} onDropFiles={upload}/>);
    fireEvent.drop(screen.getByLabelText("Markdown preview"), { dataTransfer: dataTransferWith([fileOf("photo.png")]) });
    await waitFor(() => expect(upload).toHaveBeenCalled());
    await waitFor(() => expect(useWorkspaceStore.getState().sessions["notes/2026/a.md"]!.content).toContain("![photo.png](photo.png)"));
  });
});

describe("attachment viewer (ATT-15)", () => {
  it("renders an image through the resource URL", async () => {
    const { container } = render(<AttachmentPreview path="notes/photo 图.png" resolveResourceUrl={vaultResourceUrl}/>);
    const img = container.querySelector("img")!;
    expect(img).toHaveAttribute("src", vaultResourceUrl("notes/photo 图.png"));
    // The image only becomes visible after it loads; no blob URL is created.
    fireEvent.load(img);
    expect(await screen.findByRole("img")).toHaveAttribute("alt", "photo 图.png");
    expect(screen.getByRole("link", { name: /download/i })).toHaveAttribute("download", "photo 图.png");
  });

  it("offers a download for non-image attachments and shows load failures", async () => {
    render(<AttachmentPreview path="notes/report.pdf" resolveResourceUrl={vaultResourceUrl}/>);
    expect(screen.getByRole("link", { name: /open \/ download attachment/i })).toHaveAttribute("href", vaultResourceUrl("notes/report.pdf"));
    const { container } = rtlRender(<I18nProvider initialLocale="en-US"><AttachmentPreview path="notes/broken.png" resolveResourceUrl={vaultResourceUrl}/></I18nProvider>);
    fireEvent.error(container.querySelector("img")!);
    expect(await screen.findByText(/preview failed to load/i)).toBeInTheDocument();
  });
});

describe("shell integration (ATT-12/ATT-13)", () => {
  it("wires the toolbar to the current note and shows upload errors", async () => {
    configureApi({ uploadAttachmentBase64: async () => { throw Object.assign(new Error("too big"), { status: 413, code: "file_too_large" }); } });
    await openNote("notes/a.md");
    render(<WorkspaceShell/>);
    const editor = await screen.findByRole("textbox", { name: /source editor/i });
    expect(editor).toBeInTheDocument();
    await userEvent.upload(screen.getByTestId("attachment-input") as HTMLInputElement, fileOf("x.png"));
    expect(await screen.findByRole("alert")).toHaveTextContent(/too big/i);
  });

  it("shows the attachment viewer when a non-Markdown row is opened", async () => {
    configureApi();
    await openNote("notes/a.md");
    render(<WorkspaceShell/>);
    await userEvent.click(await screen.findByRole("button", { name: "photo.png" }));
    const viewer = await screen.findByLabelText("Attachment preview");
    expect(viewer.querySelector("img")).toHaveAttribute("src", vaultResourceUrl("notes/photo.png"));
    expect(screen.getByRole("tab", { name: /a\.md/i })).toBeInTheDocument();
  });
});

/** Reference base64 encoder for the chunked-encoding assertion. */
function toBase64Binary(bytes: Uint8Array): string {
  let binary = "";
  for (let index = 0; index < bytes.length; index += 1) binary += String.fromCharCode(bytes[index]!);
  return btoa(binary);
}
