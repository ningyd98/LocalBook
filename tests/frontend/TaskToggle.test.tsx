/**
 * Obsidian-style task lists: `- [ ]` / `- [x]` render as clickable checkboxes
 * in the preview and in the live-preview editor; one click flips the marker in
 * the source (which keeps the byte-faithful save path untouched).
 */
import { fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "./render";
import { Preview } from "../../packages/markdown/src/Preview";
import { renderMarkdown } from "../../packages/markdown/src/render";
import { markTaskCheckboxes, resolveVaultRelativePath, taskItems, toggleTaskInSource } from "../../packages/protocol/src";
import { CodeMirrorEditor } from "../../packages/editor/src/CodeMirrorEditor";
import { configureWorkspaceApi, useWorkspaceStore } from "../../packages/workspace/src";
import type { EditorSession, WorkspaceApi } from "../../packages/workspace/src";

const b64 = (value: string) => btoa(value);
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
    fetchVaultFile: vi.fn<WorkspaceApi["fetchVaultFile"]>(async () => ({ path: "a.md", content_base64: b64(""), byte_length: 0, sha256: "sha256:base", content_type: "text/markdown" })),
    patchVaultFile: vi.fn<WorkspaceApi["patchVaultFile"]>(async () => ({ path: "a.md", sha256: "sha256:new", byte_length: 0, operation: "updated" as const })),
    ...overrides,
  });
}

beforeEach(() => {
  localStorage.clear();
  useWorkspaceStore.getState().registerCaretInsert(null);
  useWorkspaceStore.setState({ sessions: { "a.md": session(withLines("- [ ] open", "- [x] done")) }, activePath: "a.md", tabs: [], workspaceFrozen: false, vaultStale: false });
  api();
});

describe("task source helpers", () => {
  it("finds task markers in document order and ignores code blocks", () => {
    const source = withLines("- [ ] one", "- [x] two", "```", "- [ ] in code", "```", "1. [X] three");
    expect(taskItems(source).map((item) => item.checked)).toEqual([false, true, true]);
    expect(taskItems(source).map((item) => item.index)).toEqual([0, 1, 2]);
  });

  it("flips exactly one marker", () => {
    const source = withLines("- [ ] one", "- [x] two");
    expect(toggleTaskInSource(source, 0)).toBe(withLines("- [x] one", "- [x] two"));
    expect(toggleTaskInSource(source, 1)).toBe(withLines("- [ ] one", "- [ ] two"));
    // Out-of-range index is a no-op, never a crash.
    expect(toggleTaskInSource(source, 99)).toBe(source);
  });

  it("marks markers with their ordinal for the rendered preview", () => {
    const marked = markTaskCheckboxes(withLines("- [ ] one"));
    expect(marked).toContain('data-task-index="0"');
    expect(marked).toContain('data-task-checked="false"');
  });
});

describe("rendered preview checkboxes", () => {
  it("renders a clickable span that keeps the checked state", () => {
    const html = renderMarkdown(withLines("- [ ] open", "- [x] done"));
    expect(html).toContain('class="task-toggle"');
    expect(html).toContain('data-task-index="1"');
    expect(html).toContain('data-task-checked="true"');
    // Never a disabled native checkbox: the preview owns the interaction.
    expect(html).not.toContain("disabled");
  });

  it("reports the ordinal when clicked", async () => {
    const onToggleTask = vi.fn();
    const { container } = render(<Preview source={withLines("- [ ] open", "- [x] done")} onToggleTask={onToggleTask} />);
    const boxes = container.querySelectorAll(".task-toggle");
    expect(boxes).toHaveLength(2);
    await userEvent.click(boxes[1] as HTMLElement);
    expect(onToggleTask).toHaveBeenCalledWith(1);
  });

  it("does not intercept clicks when no handler is supplied", async () => {
    const { container } = render(<Preview source={withLines("- [ ] open")} />);
    await userEvent.click(container.querySelector(".task-toggle") as HTMLElement);
    // Nothing to assert beyond "no throw"; the span stays a span.
    expect(container.querySelector(".task-toggle")).toBeInTheDocument();
  });
});

describe("toggleTask store action", () => {
  it("flips the marker in the session content", () => {
    const ok = useWorkspaceStore.getState().toggleTask("a.md", 0);
    expect(ok).toBe(true);
    expect(useWorkspaceStore.getState().sessions["a.md"]!.content).toBe(withLines("- [x] open", "- [x] done"));
    expect(useWorkspaceStore.getState().sessions["a.md"]!.dirty).toBe(true);
  });

  it("is a no-op for an unknown note or index", () => {
    expect(useWorkspaceStore.getState().toggleTask("missing.md", 0)).toBe(false);
    expect(useWorkspaceStore.getState().toggleTask("a.md", 42)).toBe(false);
    expect(useWorkspaceStore.getState().sessions["a.md"]!.content).toBe(withLines("- [ ] open", "- [x] done"));
  });
});

describe("live preview checkbox", () => {
  it("renders a widget per task item and reports clicks", async () => {
    const onToggleTask = vi.fn();
    const onChange = vi.fn();
    const { container } = render(<CodeMirrorEditor
      value={withLines("- [ ] open", "- [x] done")}
      theme="light"
      onChange={onChange}
      livePreview={{ onToggleTask }}
    />);
    // The caret's own line keeps its raw `[ ]`; move it to the end so both
    // task lines render their checkbox.
    const view = (container.querySelector(".cm-content") as unknown as { cmView: { view: { state: { doc: { length: number } }; dispatch: (spec: unknown) => void } } }).cmView.view;
    view.dispatch({ selection: { anchor: view.state.doc.length } });
    await waitFor(() => expect(container.querySelectorAll(".cm-lp-task")).toHaveLength(2));
    const boxes = container.querySelectorAll(".cm-lp-task");
    expect(boxes[0]).toHaveAttribute("data-task-index", "0");
    expect(boxes[1]).toHaveClass("cm-lp-task-done");
    // userEvent's pointer pipeline does not reach CodeMirror's DOM handlers
    // under jsdom; a bubbling click event exercises the same code path.
    fireEvent.click(boxes[0] as HTMLElement);
    expect(onToggleTask).toHaveBeenCalledWith(0);
  });
});

describe("live preview images resolve against the note directory", () => {
  it("resolves a relative image path before building the resource URL", async () => {
    // The shell injects exactly this resolver; passing the authored relative
    // path to the resource endpoint verbatim 404s for notes in a sub-folder.
    const resolveResourceUrl = (path: string) => {
      const resolved = resolveVaultRelativePath("2026.9/2026-09-09.md", path);
      return resolved ? `/api/v1/vault/resource?${new URLSearchParams({ path: resolved })}` : null;
    };
    const { container } = render(<CodeMirrorEditor
      value={withLines("![shot.png](shot.png)")}
      theme="light"
      onChange={vi.fn()}
      livePreview={{ notePath: "2026.9/2026-09-09.md", resolveResourceUrl }}
    />);
    // The caret sits on the image line by default, which keeps its raw text;
    // move it away so the image widget renders.
    const view = (container.querySelector(".cm-content") as unknown as { cmView: { view: { state: { doc: { length: number } }; dispatch: (spec: unknown) => void } } }).cmView.view;
    view.dispatch({ selection: { anchor: view.state.doc.length } });
    await waitFor(() => expect(container.querySelector(".cm-lp-image img")).toBeTruthy());
    const src = container.querySelector(".cm-lp-image img")!.getAttribute("src")!;
    expect(decodeURIComponent(src)).toBe("/api/v1/vault/resource?path=2026.9/shot.png");
    expect(decodeURIComponent(src)).not.toBe("/api/v1/vault/resource?path=shot.png");
  });

  it("keeps the authored text when the path cannot be resolved", async () => {
    const { container } = render(<CodeMirrorEditor
      value={withLines("![x](../../../escape.png)")}
      theme="light"
      onChange={vi.fn()}
      livePreview={{ notePath: "2026.9/a.md", resolveResourceUrl: () => null }}
    />);
    await waitFor(() => expect(container.querySelector(".cm-lp-image")).toBeNull());
  });
});
