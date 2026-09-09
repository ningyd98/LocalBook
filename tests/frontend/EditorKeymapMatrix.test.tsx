/**
 * Regression matrix for three reported defects:
 *
 *  1. the editor had no keymap at all (Enter/Backspace/undo dead);
 *  2. attachment references were appended at the end instead of the caret;
 *  3. ordered-list numbers disappeared in the preview (Tailwind preflight
 *     resets `list-style`, so the pane must restore the markers itself).
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { screen } from "@testing-library/react";
import { render } from "./render";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { EditorState } from "../../packages/editor/node_modules/@codemirror/state/dist/index.js";
import { EditorView, keymap } from "../../packages/editor/node_modules/@codemirror/view/dist/index.js";
import { CodeMirrorEditor, setHeadingLevel } from "../../packages/editor/src/CodeMirrorEditor";
import { HeadingToolbar } from "../../apps/web/src/components/HeadingToolbar";
import { I18nProvider } from "../../apps/web/src/i18n";
import { configureWorkspaceApi, useWorkspaceStore } from "../../packages/workspace/src";
import type { EditorSession, WorkspaceApi } from "../../packages/workspace/src";

const NL = String.fromCharCode(10);
const withLines = (...lines: string[]) => lines.join(NL) + NL;

const session = (content: string): EditorSession => ({
  path: "a.md", content, baseSha256: "sha256:base", baseContentBase64: "", byteLength: content.length,
  encoding: "utf8", lineSeparator: "LF", hasBOM: false, dirty: false, saveState: "saved",
  error: null, conflict: null, notice: null, requestVersion: 0,
});

function api(overrides: Partial<WorkspaceApi> = {}) {
  configureWorkspaceApi({
    fetchVaultFiles: vi.fn<WorkspaceApi["fetchVaultFiles"]>(async () => ({ entries: [] })),
    fetchVaultFile: vi.fn<WorkspaceApi["fetchVaultFile"]>(async () => ({ path: "a.md", content_base64: "", byte_length: 0, sha256: "sha256:base", content_type: "text/markdown" })),
    patchVaultFile: vi.fn<WorkspaceApi["patchVaultFile"]>(async () => ({ path: "a.md", sha256: "sha256:new", byte_length: 0, operation: "updated" as const })),
    ...overrides,
  });
}

function mountEditor(value: string) {
  render(<CodeMirrorEditor value={value} theme="light" onChange={vi.fn()} />);
  const content = screen.getByRole("textbox", { name: "Markdown source" }).querySelector(".cm-content") as HTMLElement;
  return (content as unknown as { cmView: { view: EditorView } }).cmView.view;
}

beforeEach(() => {
  useWorkspaceStore.getState().registerCaretInsert(null);
  useWorkspaceStore.setState({ sessions: { "a.md": session(withLines("alpha", "beta")) }, activePath: "a.md", tabs: [], workspaceFrozen: false, vaultStale: false });
  api();
});

describe("editor keymap (defect 1)", () => {
  it("registers the standard keymap plus the heading shortcuts", async () => {
    const view = mountEditor("line");
    await userEvent.click(view.dom);
    // The facet used to hold one extension with no bindings at all, so no key
    // did anything. It is a nested array; flatten the first group.
    const facet = view.state.facet(keymap) as unknown as ({ key?: string }[] | { key?: string })[];
    const keys = facet.flat().map((binding) => binding.key);
    expect(keys).toEqual(expect.arrayContaining(["Enter", "Backspace", "Mod-z"]));
    expect(keys).toEqual(expect.arrayContaining(["Ctrl-1", "Ctrl-2", "Ctrl-0"]));
  });

  it("Ctrl-1..6 set headings and toggle them off", () => {
    const view = mountEditor(withLines("title", "body"));
    view.dispatch({ selection: { anchor: 0 } });

    expect(setHeadingLevel(1)(view)).toBe(true);
    expect(view.state.doc.toString()).toBe(withLines("# title", "body"));

    expect(setHeadingLevel(2)(view)).toBe(true);
    expect(view.state.doc.toString()).toBe(withLines("## title", "body"));

    expect(setHeadingLevel(0)(view)).toBe(true);
    expect(view.state.doc.toString()).toBe(withLines("title", "body"));

    // Applying the level a line already has toggles it off.
    setHeadingLevel(3)(view);
    expect(view.state.doc.toString()).toBe(withLines("### title", "body"));
    setHeadingLevel(3)(view);
    expect(view.state.doc.toString()).toBe(withLines("title", "body"));
  });

  it("heading command only touches the selected lines", () => {
    const host = document.createElement("div");
    document.body.appendChild(host);
    const view = new EditorView({ state: EditorState.create({ doc: withLines("one", "two", "three") }), parent: host });
    view.dispatch({ selection: { anchor: 0, head: 7 } });
    setHeadingLevel(2)(view);
    expect(view.state.doc.toString()).toBe(withLines("## one", "## two", "three"));
    view.destroy();
    host.remove();
  });
});

describe("attachment insertion point (defect 2)", () => {
  it("uses the registered caret handler instead of appending", () => {
    const caret = vi.fn(() => true);
    useWorkspaceStore.getState().registerCaretInsert(caret);
    const before = useWorkspaceStore.getState().sessions["a.md"]!.content;
    const inserted = useWorkspaceStore.getState().insertMarkdownAtSelection("a.md", "![x](x.png)");
    expect(inserted).toBe(true);
    expect(caret).toHaveBeenCalledWith("a.md", "![x](x.png)");
    // The store must not also append: the editor echo owns the content update.
    expect(useWorkspaceStore.getState().sessions["a.md"]!.content).toBe(before);
  });

  it("falls back to appending when no editor is mounted", () => {
    useWorkspaceStore.getState().registerCaretInsert(() => false);
    useWorkspaceStore.getState().insertMarkdownAtSelection("a.md", "![x](x.png)");
    expect(useWorkspaceStore.getState().sessions["a.md"]!.content).toBe(withLines("alpha", "beta", "![x](x.png)"));
  });

  it("ignores a handler registered for another note", () => {
    useWorkspaceStore.getState().registerCaretInsert((path) => path === "b.md");
    useWorkspaceStore.getState().insertMarkdownAtSelection("a.md", "![x](x.png)");
    expect(useWorkspaceStore.getState().sessions["a.md"]!.content).toContain("![x](x.png)");
  });
});

describe("preview list markers (defect 3)", () => {
  // Vitest runs with the web package as cwd (see apps/web/vite.config.ts).
  const css = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf8");

  it("restores ordered/unordered markers that Tailwind preflight removes", () => {
    expect(css).toMatch(/\.preview-pane ol\{[^}]*list-style:decimal/);
    expect(css).toMatch(/\.preview-pane ul,\.preview-pane ol\{[^}]*list-style:disc/);
    expect(css).toMatch(/\.preview-pane li\{[^}]*display:list-item/);
  });
});

describe("heading toolbar (defect 2 follow-up)", () => {
  it("renders H1..H6 plus a body-text button and reports the level", async () => {
    const onHeading = vi.fn();
    render(<I18nProvider><HeadingToolbar onHeading={onHeading}/></I18nProvider>);
    for (const level of [1, 2, 3, 4, 5, 6, 0]) {
      const label = level === 0 ? "正文（取消标题）" : `${["一", "二", "三", "四", "五", "六"][level - 1]}级标题`;
      await userEvent.click(screen.getByRole("button", { name: label }));
      expect(onHeading).toHaveBeenLastCalledWith(level);
    }
    expect(onHeading).toHaveBeenCalledTimes(7);
  });

  it("disables every button when the editor is read-only", () => {
    render(<I18nProvider><HeadingToolbar onHeading={vi.fn()} disabled/></I18nProvider>);
    for (const button of screen.getAllByRole("button")) expect(button).toBeDisabled();
  });

  it("applies a level through the editor handle used by the buttons", () => {
    const view = mountEditor(withLines("title", "body"));
    view.dispatch({ selection: { anchor: 0 } });
    // Mirrors CodeMirrorEditorHandle.setHeading, which the toolbar calls.
    expect(setHeadingLevel(4)(view)).toBe(true);
    expect(view.state.doc.toString()).toBe(withLines("#### title", "body"));
  });
});
