/**
 * Split view: the editor and the preview scroll as one document.
 *
 * jsdom has no layout, so the specs give the two scroll boxes an explicit size
 * (with a minimal `scrollTop`/`scrollHeight`/`clientHeight` model) and confirm
 * the mirroring rule: same relative position, no feedback loop, and no dragging
 * when one side cannot scroll.
 */
import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "./render";
import { WorkspaceShell } from "../../apps/web/src/components/WorkspaceShell";
import { preferenceDefaults, useWorkspaceStore } from "../../packages/workspace/src";
import type { VaultFileEntry } from "../../packages/protocol/src";

const b64 = (value: string) => btoa(value);
const store = () => useWorkspaceStore.getState();
const ENTRIES: VaultFileEntry[] = [{ path: "note.md", kind: "file", size: 10, sha256: "sha256:note" }];
const BODY = ["# Title", "", ...Array.from({ length: 200 }, (_, index) => `- item ${index}`)].join("\n");

/** Give one element a scrollable geometry; jsdom reports nothing by default. */
function makeScrollable(element: HTMLElement, { content, viewport }: { content: number; viewport: number }) {
  let top = 0;
  Object.defineProperty(element, "scrollHeight", { configurable: true, get: () => content });
  Object.defineProperty(element, "clientHeight", { configurable: true, get: () => viewport });
  Object.defineProperty(element, "scrollTop", {
    configurable: true,
    get: () => top,
    set: (value: number) => {
      top = Math.max(0, Math.min(content - viewport, value));
      element.dispatchEvent(new Event("scroll"));
    },
  });
  return { get top() { return top; }, set top(value: number) { element.scrollTop = value; } };
}

function configureApi() {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({}), { status: 200 })));
  useWorkspaceStore.setState({
    ...preferenceDefaults,
    tree: { entries: ENTRIES, expandedPaths: [], collapsedPaths: [], status: "ready", error: null },
    tabs: [{ path: "note.md", title: "note.md", dirty: false, loading: false, error: null }],
    activePath: "note.md",
    sessions: {
      "note.md": { path: "note.md", content: BODY, baseSha256: "sha256:note", baseContentBase64: b64(BODY), byteLength: BODY.length, encoding: "utf8", lineSeparator: "LF", hasBOM: false, dirty: false, saveState: "saved", error: null, conflict: null, notice: null, requestVersion: 1 },
    },
  });
}

/** Render the shell in split mode and return the two scroll boxes + geometry. */
async function splitView() {
  render(<WorkspaceShell/>);
  await screen.findByRole("textbox", { name: "Source editor for note.md" });
  act(() => store().setPreferences({ editorMode: "split" }));
  const editor = document.querySelector(".editor-panel .cm-scroller") as HTMLElement;
  const preview = document.querySelector(".preview-panel .preview-pane") as HTMLElement;
  expect(editor).toBeTruthy();
  expect(preview).toBeTruthy();
  const editorBox = makeScrollable(editor, { content: 5000, viewport: 500 });
  const previewBox = makeScrollable(preview, { content: 2500, viewport: 500 });
  return { editor, preview, editorBox, previewBox };
}

/** The hook writes the peer inside requestAnimationFrame. */
const settle = () => act(async () => { await new Promise(resolve => setTimeout(resolve, 40)); });

beforeEach(() => {
  localStorage.clear();
  store().resetVault();
  configureApi();
});

describe("split view scroll sync", () => {
  it("mirrors the relative position from the editor to the preview", async () => {
    const { editor, editorBox, previewBox } = await splitView();

    editor.scrollTop = 2250; // halfway through a 4500px range
    await settle();

    expect(editorBox.top).toBe(2250);
    expect(previewBox.top).toBeCloseTo(1000, 0); // halfway through 2000px
  });

  it("mirrors the other way too, so either pane can drive", async () => {
    const { preview, editorBox, previewBox } = await splitView();

    preview.scrollTop = 2000; // bottom of a 2000px range
    await settle();

    expect(previewBox.top).toBe(2000);
    expect(editorBox.top).toBe(4500); // bottom of 4500px
  });

  it("does not echo back and forth", async () => {
    const { editor, editorBox, previewBox } = await splitView();

    editor.scrollTop = 900;
    await settle();
    const afterFirst = previewBox.top;
    expect(afterFirst).toBeCloseTo(400, 0);

    // A mirrored scroll must not push the editor somewhere else.
    editor.scrollTop = 900;
    await settle();
    expect(editorBox.top).toBe(900);
    expect(previewBox.top).toBe(afterFirst);
  });

  it("leaves a pane alone when the other cannot scroll", async () => {
    const { editor, previewBox } = await splitView();
    const preview = document.querySelector(".preview-panel .preview-pane") as HTMLElement;
    // A short preview: no scrollable range at all.
    Object.defineProperty(preview, "scrollHeight", { configurable: true, get: () => 400 });
    Object.defineProperty(preview, "clientHeight", { configurable: true, get: () => 500 });

    editor.scrollTop = 3000;
    await settle();

    expect(previewBox.top).toBe(0);
  });

  it("stops syncing when the toggle is switched off", async () => {
    const { editor, previewBox } = await splitView();
    await userEvent.click(screen.getByRole("button", { name: "Sync scroll" }));
    expect(store().syncScroll).toBe(false);

    editor.scrollTop = 2250;
    await settle();

    expect(previewBox.top).toBe(0);
    expect(localStorage.getItem("localnote-preferences")).toContain('"syncScroll":false');
  });

  it("follows a continuous scroll gesture, every step", async () => {
    const { editor, previewBox } = await splitView();

    // A real drag fires many scroll events; none may be swallowed as an echo.
    for (const position of [100, 500, 1200, 2600, 3600, 4500]) {
      editor.scrollTop = position;
      await settle();
      expect(previewBox.top).toBeCloseTo((position / 4500) * 2000, 0);
    }
  });

  it("binds when the editor's scroll box appears late", async () => {
    // The reported bug: the hook ran before CodeMirror had built its DOM, gave
    // up for good, and the two panes stayed independent until something else
    // re-ran the effect. The scroller is now created after the first bind.
    render(<WorkspaceShell/>);
    await screen.findByRole("textbox", { name: "Source editor for note.md" });
    act(() => store().setPreferences({ editorMode: "split" }));

    const editorPane = document.querySelector(".editor-panel") as HTMLElement;
    const preview = document.querySelector(".preview-panel .preview-pane") as HTMLElement;
    const previewBox = makeScrollable(preview, { content: 2500, viewport: 500 });
    // Start from the state the bug describes: no editor scroll box at all yet.
    editorPane.querySelector(".cm-scroller")?.remove();

    // Let the hook poll for a while with nothing to bind — the state the bug
    // left it in permanently — and only then mount the scroll box.
    await settle();

    const late = document.createElement("div");
    late.className = "cm-scroller";
    const editorBox = makeScrollable(late, { content: 5000, viewport: 500 });
    editorPane.appendChild(late);

    await settle();
    late.scrollTop = 4500; // to the bottom
    await settle();

    expect(editorBox.top).toBe(4500);
    expect(previewBox.top).toBe(2000);
  });

  it("re-binds when the scroll box is replaced, as switching notes does", async () => {
    const { editor, editorBox, previewBox } = await splitView();
    editor.scrollTop = 2250;
    await settle();
    expect(previewBox.top).toBeCloseTo(1000, 0);

    // A new CodeMirror instance replaces the old scroller.
    const replacement = document.createElement("div");
    replacement.className = "cm-scroller";
    const replacementBox = makeScrollable(replacement, { content: 5000, viewport: 500 });
    editor.replaceWith(replacement);

    await settle();
    replacement.scrollTop = 4500;
    await settle();

    expect(replacementBox.top).toBe(4500);
    expect(previewBox.top).toBe(2000);
    expect(editorBox.top).toBe(2250); // the detached element stopped receiving writes
  });

  it("only shows the toggle in split mode", async () => {
    configureApi();
    render(<WorkspaceShell/>);
    await screen.findByRole("textbox", { name: "Source editor for note.md" });
    expect(screen.queryByRole("button", { name: "Sync scroll" })).not.toBeInTheDocument();

    act(() => store().setPreferences({ editorMode: "split" }));
    expect(await screen.findByRole("button", { name: "Sync scroll" })).toHaveAttribute("aria-pressed", "true");
  });

  it("defaults to on and survives a reload", async () => {
    expect(preferenceDefaults.syncScroll).toBe(true);
    act(() => store().setPreferences({ syncScroll: false }));
    store().resetVault();
    expect(store().syncScroll).toBe(false);
  });
});
