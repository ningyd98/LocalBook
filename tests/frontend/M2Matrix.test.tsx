/**
 * M2 B3 frontend matrix — auto-save debounce/serialization, encoding fidelity,
 * conflict handling, and workspace UI (tabs / FileTree / split panes).
 *
 * All fetch is mocked per test; nothing touches a real Vault/backend/network.
 * Rules honoured here:
 *  - UI-driven store mutations are wrapped in `act()` so React commits/effects
 *    run deterministically (the debounce lives in a component effect).
 *  - Fake timers are never combined with `waitFor`/`userEvent` (they cannot
 *    advance under vi fake timers); flushing is done explicitly instead.
 *  - Store-level save tests use real timers and plain awaits.
 */
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { render } from "./render";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { FileTree } from "../../apps/web/src/components/FileTree";
import { PreviewPane } from "../../apps/web/src/components/PreviewPane";
import { WorkspaceShell } from "../../apps/web/src/components/WorkspaceShell";
import { CodeMirrorEditor } from "../../packages/editor/src/CodeMirrorEditor";
import type { FileMutationResponse, VaultFileEntry } from "../../packages/protocol/src";
import { configureWorkspaceApi, toBase64, useWorkspaceStore } from "../../packages/workspace/src";
import type { WorkspaceApi } from "../../packages/workspace/src";

const read = (content: string, sha = "sha256:base", byteLength = new TextEncoder().encode(content).length) => ({
  path: "a.md", content_base64: toBase64(content), byte_length: byteLength, sha256: sha, content_type: "text/markdown",
});
const mutation: FileMutationResponse = { path: "a.md", sha256: "sha256:new", byte_length: 3, operation: "updated" };
const entries: VaultFileEntry[] = [
  { path: "docs", kind: "directory", size: 0, sha256: null },
  { path: "docs/child.md", kind: "file", size: 2, sha256: "sha256:child" },
  { path: "a.md", kind: "file", size: 2, sha256: "sha256:base" },
];

function resetStore() {
  useWorkspaceStore.setState({ tree: { entries: [], expandedPaths: [], collapsedPaths: [], status: "idle", error: null }, tabs: [], activePath: null, sessions: {}, theme: "light", splitRatio: 50 });
}

/** Mock api per test; unset members fall back to sane defaults. */
function configureApi(overrides: Partial<WorkspaceApi> = {}) {
  configureWorkspaceApi({
    fetchVaultFiles: overrides.fetchVaultFiles ?? vi.fn<WorkspaceApi["fetchVaultFiles"]>(async () => ({ entries })),
    fetchVaultFile: overrides.fetchVaultFile ?? vi.fn<WorkspaceApi["fetchVaultFile"]>(async () => read("# A\n😀\n")),
    patchVaultFile: overrides.patchVaultFile ?? vi.fn<WorkspaceApi["patchVaultFile"]>(async () => mutation),
  });
}

function sessionState() {
  return useWorkspaceStore.getState().sessions["a.md"];
}

/** Flush the React act queue plus pending promise continuations. */
async function flush() {
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}

const conflictError = () => Object.assign(new Error("conflict"), { status: 409, code: "file_conflict" });

beforeEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  resetStore();
  configureApi();
});

describe("M2 B3 save matrix", () => {
  it("debounces editing for 800ms and PATCHes exactly once", async () => {
    vi.useFakeTimers();
    const patch = vi.fn<WorkspaceApi["patchVaultFile"]>(async () => mutation);
    configureApi({ fetchVaultFile: vi.fn(async () => read("old")), patchVaultFile: patch });
    await useWorkspaceStore.getState().openFile("a.md");
    render(<WorkspaceShell />);

    await act(async () => { useWorkspaceStore.getState().updateContent("a.md", "edited"); });
    expect(patch).not.toHaveBeenCalled();

    await act(async () => { vi.advanceTimersByTime(799); });
    expect(patch).not.toHaveBeenCalled();

    await act(async () => { vi.advanceTimersByTime(1); });
    await flush();
    expect(patch).toHaveBeenCalledTimes(1);
    expect((patch.mock.calls[0]![0] as Parameters<WorkspaceApi["patchVaultFile"]>[0]).expectedSha256).toBe("sha256:base");
    expect(sessionState()?.dirty).toBe(false);
    expect(sessionState()?.saveState).toBe("saved");

    // No second PATCH later: debounce stays silent after the save completed.
    await act(async () => { vi.advanceTimersByTime(5000); });
    await flush();
    expect(patch).toHaveBeenCalledTimes(1);
  });

  it("Ctrl-S cancels/refreshes the debounce, saves immediately, and never duplicates PATCH", async () => {
    vi.useFakeTimers();
    let resolve!: (v: FileMutationResponse) => void;
    const patch = vi.fn<WorkspaceApi["patchVaultFile"]>(() => new Promise<FileMutationResponse>((r) => { resolve = r; }));
    configureApi({ fetchVaultFile: vi.fn(async () => read("old")), patchVaultFile: patch });
    await useWorkspaceStore.getState().openFile("a.md");
    render(<WorkspaceShell />);

    // Arm the 800ms auto-save debounce first, then press Ctrl-S over it.
    await act(async () => { useWorkspaceStore.getState().updateContent("a.md", "edited"); });
    fireEvent.keyDown(window, { key: "s", ctrlKey: true });
    await flush();
    expect(patch).toHaveBeenCalledTimes(1); // immediate save, not debounced
    expect((patch.mock.calls[0]![0] as Parameters<WorkspaceApi["patchVaultFile"]>[0]).expectedSha256).toBe("sha256:base");

    // The debounce it replaced must not fire afterwards.
    await act(async () => { vi.advanceTimersByTime(800); });
    await flush();
    expect(patch).toHaveBeenCalledTimes(1);

    resolve(mutation); // in-flight save completes
    await flush();
    expect(patch).toHaveBeenCalledTimes(1);
    expect(sessionState()?.dirty).toBe(false);
    expect(sessionState()?.saveState).toBe("saved");

    // Nothing re-arms a save after completion.
    await act(async () => { vi.advanceTimersByTime(5000); });
    await flush();
    expect(patch).toHaveBeenCalledTimes(1);
  });

  it("surfaces a non-conflict save failure, preserves content, and recovers only on manual retry", async () => {
    const patch = vi.fn<WorkspaceApi["patchVaultFile"]>()
      .mockRejectedValueOnce(new Error("server unavailable"))
      .mockResolvedValueOnce(mutation);
    configureApi({ fetchVaultFile: vi.fn(async () => read("old")), patchVaultFile: patch });
    await useWorkspaceStore.getState().openFile("a.md");
    useWorkspaceStore.getState().updateContent("a.md", "edited");
    await useWorkspaceStore.getState().save("a.md");
    expect(sessionState()?.saveState).toBe("error");
    expect(sessionState()?.content).toBe("edited");
    expect(sessionState()?.dirty).toBe(true);
    expect(sessionState()?.error?.kind).toBe("network");
    expect(patch).toHaveBeenCalledTimes(1);
    // No auto re-PATCH while in the error state: recovery is manual only.
    await new Promise((r) => setTimeout(r, 25));
    expect(patch).toHaveBeenCalledTimes(1);

    await useWorkspaceStore.getState().save("a.md", "manual");
    expect(patch).toHaveBeenCalledTimes(2);
    expect(sessionState()?.saveState).toBe("saved");
    expect(sessionState()?.dirty).toBe(false);
  });

  it("surfaces an HTTP 500 save failure as error (not swallowed), keeps content, no auto re-PATCH, manual retry recovers", async () => {
    const patch = vi.fn<WorkspaceApi["patchVaultFile"]>()
      .mockRejectedValueOnce(Object.assign(new Error("internal error"), { status: 500, code: "internal_error" }))
      .mockResolvedValueOnce(mutation);
    configureApi({ fetchVaultFile: vi.fn(async () => read("old")), patchVaultFile: patch });
    await useWorkspaceStore.getState().openFile("a.md");
    useWorkspaceStore.getState().updateContent("a.md", "edited");
    await useWorkspaceStore.getState().save("a.md");
    expect(sessionState()?.saveState).toBe("error");
    expect(sessionState()?.error?.kind).toBe("api");
    expect(sessionState()?.error?.status).toBe(500);
    expect(sessionState()?.content).toBe("edited");
    expect(sessionState()?.dirty).toBe(true);
    expect(patch).toHaveBeenCalledTimes(1);
    // Error gate holds: nothing auto-rePATCHes while the session is in error.
    await new Promise((r) => setTimeout(r, 25));
    expect(patch).toHaveBeenCalledTimes(1);

    // Manual retry re-saves the latest content against the latest base.
    await useWorkspaceStore.getState().save("a.md", "manual");
    expect(patch).toHaveBeenCalledTimes(2);
    expect((patch.mock.calls[1]![0] as Parameters<WorkspaceApi["patchVaultFile"]>[0]).expectedSha256).toBe("sha256:base");
    expect(sessionState()?.saveState).toBe("saved");
    expect(sessionState()?.dirty).toBe(false);
  });

  it("surfaces an in-flight failure against concurrent latest edits without retry storm", async () => {
    let reject!: (error: Error) => void;
    const patch = vi.fn<WorkspaceApi["patchVaultFile"]>(() => new Promise<FileMutationResponse>((_resolve, r) => { reject = r; }));
    configureApi({ fetchVaultFile: vi.fn(async () => read("old")), patchVaultFile: patch });
    await useWorkspaceStore.getState().openFile("a.md");
    useWorkspaceStore.getState().updateContent("a.md", "first");
    const first = useWorkspaceStore.getState().save("a.md");
    useWorkspaceStore.getState().updateContent("a.md", "latest");
    const second = useWorkspaceStore.getState().save("a.md", "manual");
    expect(patch).toHaveBeenCalledTimes(1);
    reject(new Error("network down"));
    await first;
    await second;
    expect(sessionState()?.saveState).toBe("error");
    expect(sessionState()?.content).toBe("latest");
    expect(sessionState()?.dirty).toBe(true);
    expect(patch).toHaveBeenCalledTimes(1);
    // No retry storm: exactly one PATCH even after the failure settles.
    await new Promise((r) => setTimeout(r, 25));
    expect(sessionState()?.saveState).toBe("error");
    expect(sessionState()?.content).toBe("latest");
    expect(patch).toHaveBeenCalledTimes(1);
  });

  it("does not retry a 409 file_conflict — PATCH count stays strictly 1", async () => {
    const patch = vi.fn(async () => { throw conflictError(); });
    configureApi({ fetchVaultFile: vi.fn(async () => read("old")), patchVaultFile: patch });
    await useWorkspaceStore.getState().openFile("a.md");
    useWorkspaceStore.getState().updateContent("a.md", "edited");
    await useWorkspaceStore.getState().save("a.md");
    expect(patch).toHaveBeenCalledTimes(1);
    expect(sessionState()?.saveState).toBe("conflict");
    expect(sessionState()?.dirty).toBe(true);

    // Even further edits and explicit save attempts must not PATCH again:
    // the session stays in the conflict state until reload/keep-local.
    useWorkspaceStore.getState().updateContent("a.md", "edited again");
    await useWorkspaceStore.getState().save("a.md", "manual");
    await new Promise((r) => setTimeout(r, 10));
    expect(patch).toHaveBeenCalledTimes(1);
  });

  it("keep-local refreshes the hash and explicitly permits a later save; reload discards the draft", async () => {
    const fetchFile = vi.fn().mockResolvedValueOnce(read("server"))
      .mockResolvedValueOnce(read("remote", "sha256:remote"))
      .mockResolvedValueOnce(read("remote again", "sha256:latest"));
    const patch = vi.fn().mockRejectedValueOnce(conflictError()).mockResolvedValue(mutation);
    configureApi({fetchVaultFile:fetchFile,patchVaultFile:patch});
    await useWorkspaceStore.getState().openFile("a.md");
    useWorkspaceStore.getState().updateContent("a.md", "local");
    await useWorkspaceStore.getState().save("a.md");
    await useWorkspaceStore.getState().keepLocal("a.md");
    expect(sessionState()?.content).toBe("local");
    expect(sessionState()?.baseSha256).toBe("sha256:remote");
    await useWorkspaceStore.getState().save("a.md", "manual");
    expect(patch.mock.calls[1]?.[0].expectedSha256).toBe("sha256:remote");
    await useWorkspaceStore.getState().reloadConflict("a.md");
    expect(sessionState()?.content).toBe("remote again");
    expect(sessionState()?.dirty).toBe(false);
  });

  it("reloads invalid UTF-8 as a read-only session instead of throwing", async () => {
    const invalid = { path: "a.md", content_base64: btoa("\xff\xfe"), byte_length: 2, sha256: "sha256:bad", content_type: "text/markdown" };
    const fetchFile = vi.fn().mockResolvedValueOnce(read("local")).mockResolvedValueOnce(invalid);
    configureApi({ fetchVaultFile: fetchFile });
    await useWorkspaceStore.getState().openFile("a.md");
    useWorkspaceStore.getState().updateContent("a.md", "local edit");
    await useWorkspaceStore.getState().reloadConflict("a.md");
    expect(sessionState()?.encoding).toBe("invalid_utf8");
    expect(sessionState()?.saveState).toBe("saved");
    expect(sessionState()?.dirty).toBe(false);
    expect(sessionState()?.error?.code).toBe("invalid_utf8");
  });

  it("marks non-UTF-8 content read-only (encoding=invalid_utf8) and never PATCHes", async () => {
    const patch = vi.fn(async () => mutation);
    configureApi({
      fetchVaultFile: vi.fn(async () => ({ path: "a.md", content_base64: btoa("\xff\xfe"), byte_length: 2, sha256: "sha256:bad", content_type: "text/markdown" })),
      patchVaultFile: patch,
    });
    await useWorkspaceStore.getState().openFile("a.md");
    expect(sessionState()?.encoding).toBe("invalid_utf8");
    useWorkspaceStore.getState().updateContent("a.md", "attempt");
    await useWorkspaceStore.getState().save("a.md");
    expect(patch).not.toHaveBeenCalled();
    expect(useWorkspaceStore.getState().sessions["a.md"]?.content).toBe("");
  });

  it("renders the read-only editor fallback for non-UTF-8 files", async () => {
    configureApi({
      fetchVaultFile: vi.fn(async () => ({ path: "a.md", content_base64: btoa("\xff\xfe"), byte_length: 2, sha256: "sha256:bad", content_type: "text/markdown" })),
    });
    await useWorkspaceStore.getState().openFile("a.md");
    render(<WorkspaceShell />);
    expect(await screen.findByText("This file is not valid UTF-8 and is read-only.")).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Source editor for a.md" })).not.toBeInTheDocument();
  });

  it("preserves BOM/CRLF/no-final-newline untouched (no write-back) and re-encodes edited bytes", async () => {
    const original = "\ufeff# A\r\nline";
    const patch = vi.fn<WorkspaceApi["patchVaultFile"]>(async () => mutation);
    configureApi({ fetchVaultFile: vi.fn(async () => read(original, "sha256:base")), patchVaultFile: patch });
    await useWorkspaceStore.getState().openFile("a.md");
    expect(sessionState()?.content).toBe("# A\nline");
    expect(sessionState()?.hasBOM).toBe(true);
    expect(sessionState()?.lineSeparator).toBe("CRLF");

    // Unedited file: no write-back at all.
    await useWorkspaceStore.getState().save("a.md");
    expect(patch).not.toHaveBeenCalled();

    // Edited file: bytes are re-encoded exactly as the session text requires.
    useWorkspaceStore.getState().updateContent("a.md", `${sessionState()?.content}!`);
    await useWorkspaceStore.getState().save("a.md");
    expect(patch).toHaveBeenCalledTimes(1);
    const payload = patch.mock.calls[0]?.[0];
    expect(payload).toBeDefined();
    const bytes = Uint8Array.from(atob(payload!.contentBase64), (c) => c.charCodeAt(0));
    // Leading UTF-8 BOM preserved (EF BB BF)…
    expect([bytes[0], bytes[1], bytes[2]]).toEqual([0xef, 0xbb, 0xbf]);
    // …CRLF preserved (0D 0A)…
    expect(bytes).toContainEqual(0x0d);
    expect(bytes).toContainEqual(0x0a);
    // …and the file still has no trailing newline (ends with the appended '!').
    expect(bytes[bytes.length - 1]).toBe(0x21);
    expect(String.fromCharCode(...Array.from(bytes.slice(3)))).toBe("# A\r\nline!");
  });

  it("round-trips emoji and Unicode through base64", async () => {
    const unicode = "中文 😀 café";
    const patch = vi.fn<WorkspaceApi["patchVaultFile"]>(async () => mutation);
    configureApi({ fetchVaultFile: vi.fn(async () => read(unicode)), patchVaultFile: patch });
    await useWorkspaceStore.getState().openFile("a.md");
    useWorkspaceStore.getState().updateContent("a.md", `${unicode}!`);
    await useWorkspaceStore.getState().save("a.md");
    expect(patch).toHaveBeenCalledTimes(1);
    const payload = patch.mock.calls[0]?.[0];
    expect(payload).toBeDefined();
    const decoded = new TextDecoder().decode(Uint8Array.from(atob(payload!.contentBase64), (c) => c.charCodeAt(0)));
    expect(decoded).toBe(`${unicode}!`);
    // base64 itself is UTF-8: emoji encodes to 4 bytes (F0 9F 98 80).
    const raw = Uint8Array.from(atob(payload!.contentBase64), (c) => c.charCodeAt(0));
    expect(Array.from(raw)).toEqual([...new TextEncoder().encode(`${unicode}!`)]);
  });
});

describe("CodeMirror lifecycle matrix", () => {
  it("mounts initial doc, emits edits, respects readOnly, and destroys cleanly", async () => {
    const onChange = vi.fn();
    const { unmount, rerender } = render(<CodeMirrorEditor value="# initial" theme="light" onChange={onChange} />);
    const editor = screen.getByRole("textbox", { name: "Markdown source" });
    expect(editor).toHaveTextContent("# initial");
    const content = editor.querySelector(".cm-content") as HTMLElement;
    expect(content).toBeInTheDocument();
    await userEvent.click(content);
    await userEvent.type(content, "!");
    expect(onChange).toHaveBeenCalled();
    expect(onChange.mock.lastCall?.[0]).toContain("!");

    rerender(<CodeMirrorEditor value="# locked" theme="light" onChange={onChange} readOnly />);
    const lockedContent = screen.getByRole("textbox", { name: "Markdown source" }).querySelector(".cm-content");
    expect(lockedContent).toBeInTheDocument();
    expect(lockedContent).not.toHaveAttribute("contenteditable", "true");
    const callsBeforeLockedInput = onChange.mock.calls.length;
    if (lockedContent) await userEvent.type(lockedContent, "!");
    expect(onChange.mock.calls.length).toBe(callsBeforeLockedInput);

    // Unmount destroys the whole CodeMirror instance cleanly (no throw) and
    // removes every editor node from the DOM.
    expect(() => unmount()).not.toThrow();
    expect(document.querySelector(".cm-editor")).toBeNull();
  });
});

describe("M2 B3 workspace UI matrix", () => {
  it("keeps in-flight edits and serializes saves (no lost edits)", async () => {
    let resolve!: (v: FileMutationResponse) => void;
    const patch = vi.fn<WorkspaceApi["patchVaultFile"]>(() => new Promise<FileMutationResponse>((r) => { resolve = r; }));
    configureApi({ fetchVaultFile: vi.fn(async () => read("old")), patchVaultFile: patch });
    await useWorkspaceStore.getState().openFile("a.md");
    useWorkspaceStore.getState().updateContent("a.md", "first");
    const first = useWorkspaceStore.getState().save("a.md");
    useWorkspaceStore.getState().updateContent("a.md", "second"); // typed while save #1 in flight
    const second = useWorkspaceStore.getState().save("a.md");
    expect(patch).toHaveBeenCalledTimes(1); // second save is coalesced, not issued

    resolve(mutation);
    await first;
    expect((patch.mock.calls[0]![0] as Parameters<WorkspaceApi["patchVaultFile"]>[0]).expectedSha256).toBe("sha256:base");
    await waitFor(() => expect(patch).toHaveBeenCalledTimes(2)); // follow-up PATCH for newer edit
    expect((patch.mock.calls[1]![0] as Parameters<WorkspaceApi["patchVaultFile"]>[0]).expectedSha256).toBe("sha256:new");
    expect(sessionState()?.dirty).toBe(true); // "second" still unsaved at this point

    resolve({ ...mutation, sha256: "sha256:last" });
    await second;
    await new Promise((r) => setTimeout(r, 0));
    expect(sessionState()?.dirty).toBe(false);
    expect(patch).toHaveBeenCalledTimes(2);
  });

  it("dirty-tab close asks for confirmation; cancel keeps it, confirm closes", async () => {
    await useWorkspaceStore.getState().openFile("a.md");
    useWorkspaceStore.getState().updateContent("a.md", "dirty");

    const cancelled = useWorkspaceStore.getState().closeTab("a.md", () => false);
    expect(cancelled).toBe(false);
    expect(useWorkspaceStore.getState().tabs.some((tab) => tab.path === "a.md")).toBe(true);
    expect(useWorkspaceStore.getState().sessions["a.md"]).toBeDefined();

    const confirmed = useWorkspaceStore.getState().closeTab("a.md", () => true);
    expect(confirmed).toBe(true);
    expect(useWorkspaceStore.getState().tabs.some((tab) => tab.path === "a.md")).toBe(false);
    expect(useWorkspaceStore.getState().sessions["a.md"]).toBeUndefined();
  });

  it("clean tab closes without asking", () => {
    useWorkspaceStore.setState({ tabs: [{ path: "a.md", title: "a.md", dirty: false, loading: false, error: null }], activePath: "a.md", sessions: {} });
    expect(useWorkspaceStore.getState().closeTab("a.md", () => false)).toBe(true);
  });

  it("FileTree expands by default and collapses through the toggle (controlled props)", async () => {
    const open = vi.fn(); const toggle = vi.fn();
    const { rerender } = render(<FileTree entries={entries} expanded={["docs"]} onToggle={toggle} onOpen={open} />);
    // Expanded: descendants visible.
    expect(screen.getByRole("button", { name: "docs/child.md" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "docs" }));
    expect(toggle).toHaveBeenCalledWith("docs");
    await userEvent.click(screen.getByRole("button", { name: "a.md" }));
    expect(open).toHaveBeenCalledWith("a.md");

    // Explicitly collapsed: descendants hidden, top level kept.
    rerender(<FileTree entries={entries} expanded={[]} collapsed={["docs"]} onToggle={toggle} onOpen={open} />);
    expect(screen.queryByRole("button", { name: "docs/child.md" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "docs" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /a\.md/ })).toBeInTheDocument();

    // Expanded again: children return.
    rerender(<FileTree entries={entries} expanded={["docs"]} collapsed={[]} onToggle={toggle} onOpen={open} />);
    expect(screen.getByRole("button", { name: "docs/child.md" })).toBeInTheDocument();
  });

  it("FileTree collapses/expands through the store and opens a file in the shell", async () => {
    render(<WorkspaceShell />);
    // Tree loads asynchronously; a folder shows its contents until collapsed.
    const docsRow = await screen.findByRole("button", { name: "docs" });
    expect(await screen.findByRole("button", { name: "docs/child.md" })).toBeInTheDocument();

    await userEvent.click(docsRow); // collapse -> descendants hidden again
    await waitFor(() => expect(screen.queryByRole("button", { name: "docs/child.md" })).not.toBeInTheDocument());
    expect(useWorkspaceStore.getState().tree.collapsedPaths).toContain("docs");

    await userEvent.click(screen.getByRole("button", { name: "docs" })); // expand -> descendants appear
    expect(await screen.findByRole("button", { name: "docs/child.md" })).toBeInTheDocument();
    expect(useWorkspaceStore.getState().tree.collapsedPaths).not.toContain("docs");

    // Clicking a file opens its editor session.
    await userEvent.click(screen.getByRole("button", { name: "a.md" }));
    expect(await screen.findByRole("textbox", { name: "Source editor for a.md" })).toBeInTheDocument();
  });

  it("renders split source/preview and writes split-ratio layout state back (clamped 20–80)", async () => {
    configureApi({
      fetchVaultFile: vi.fn(async () => read("# Preview\n\nHello 😀", "sha256:base")),
    });
    await useWorkspaceStore.getState().openFile("a.md");
    render(<WorkspaceShell />);

    // Editing starts in a single column. Split reveals preview without remounting source.
    await userEvent.click(screen.getByRole("button", { name: "Split" }));
    expect(await screen.findByRole("textbox", { name: "Source editor for a.md" })).toBeInTheDocument();
    const preview = screen.getByRole("article", { name: "Markdown preview" });
    expect(preview).toBeInTheDocument();
    expect(await within(preview).findByRole("heading", { name: "Preview" })).toBeInTheDocument();
    expect(within(preview).getByText("Hello 😀")).toBeInTheDocument();

    // The same setter the split pane's onLayout handler writes through clamps.
    const store = useWorkspaceStore.getState();
    store.setSplitRatio(15);
    expect(useWorkspaceStore.getState().splitRatio).toBe(20);
    store.setSplitRatio(95);
    expect(useWorkspaceStore.getState().splitRatio).toBe(80);
    store.setSplitRatio(62);
    expect(useWorkspaceStore.getState().splitRatio).toBe(62);
  });

  it("renders an isolated PreviewPane with sanitized markdown", () => {
    render(<PreviewPane source={"# Preview\n\n<script>alert(1)</script>"} />);
    expect(screen.getByRole("heading", { name: "Preview" })).toBeInTheDocument();
    expect(screen.queryByText(/alert\(1\)/)).not.toBeInTheDocument();
  });
});
