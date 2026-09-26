/**
 * Inline text formatting (bold / italic / highlight / colour / clear) and the
 * right-click table insertion.
 *
 * Three layers are covered:
 *
 *  1. the source → preview pipeline (`==highlight==` and the constrained colour
 *     span), including the sanitizer's allow-list;
 *  2. the editor commands themselves, driven through the public handle;
 *  3. the two surfaces that expose them — the toolbar and the editor's
 *     right-click menu.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "./render";
import { Preview } from "../../packages/markdown/src/Preview";
import { renderMarkdown } from "../../packages/markdown/src/render";
import { preprocessHighlights } from "../../packages/protocol/src";
import { EditorView, keymap } from "../../packages/editor/node_modules/@codemirror/view/dist/index.js";
import { CodeMirrorEditor, buildTableMarkdown, normalizeColor } from "../../packages/editor/src";
import type { CodeMirrorEditorHandle } from "../../packages/editor/src";
import { FormatToolbar } from "../../apps/web/src/components/FormatToolbar";
import type { EditorFormatCommands } from "../../apps/web/src/components/FormatToolbar";
import { WorkspaceShell } from "../../apps/web/src/components/WorkspaceShell";
import { configureWorkspaceApi, useWorkspaceStore } from "../../packages/workspace/src";
import type { EditorSession, WorkspaceApi } from "../../packages/workspace/src";

const NL = String.fromCharCode(10);
const withLines = (...lines: string[]) => lines.join(NL) + NL;

const session = (content: string): EditorSession => ({
  path: "a.md", content, baseSha256: "sha256:base", baseContentBase64: "", byteLength: content.length,
  encoding: "utf8", lineSeparator: "LF", hasBOM: false, dirty: false, saveState: "saved",
  error: null, conflict: null, notice: null, requestVersion: 0,
});

const b64 = (value: string) => btoa(value);

function configureApi(overrides: Partial<WorkspaceApi> = {}) {
  configureWorkspaceApi({
    fetchVaultFiles: vi.fn<WorkspaceApi["fetchVaultFiles"]>(async () => ({ entries: [{ path: "a.md", kind: "file" as const, size: 4, sha256: "sha256:a" }] })),
    fetchVaultFile: vi.fn<WorkspaceApi["fetchVaultFile"]>(async (path: string) => ({ path, content_base64: b64("alpha"), byte_length: 5, sha256: "sha256:base", content_type: "text/markdown" })),
    patchVaultFile: vi.fn<WorkspaceApi["patchVaultFile"]>(async () => ({ path: "a.md", sha256: "sha256:new", byte_length: 0, operation: "updated" as const })),
    fetchLinks: vi.fn(async (path: string) => ({ path, outgoing: [], broken_count: 0 })),
    fetchBacklinks: vi.fn(async (path: string) => ({ path, backlinks: [] })),
    ...overrides,
  });
}

/** Mount the editor with an imperative handle and return both. */
function mountEditor(value: string, livePreview?: Record<string, unknown>) {
  const handle: { current: CodeMirrorEditorHandle | null } = { current: null };
  const onChange = vi.fn();
  const view = render(<CodeMirrorEditor value={value} theme="light" onChange={onChange} handleRef={handle} livePreview={livePreview}/>);
  const cm = (view.container.querySelector(".cm-content") as unknown as { cmView: { view: EditorView } }).cmView.view;
  return { handle, cm, container: view.container, onChange };
}

/** Select `[from, to)` in the editor and return the view. */
function select(view: EditorView, from: number, to = from) {
  view.dispatch({ selection: { anchor: from, head: to } });
  return view;
}

beforeEach(() => {
  localStorage.clear();
  useWorkspaceStore.getState().registerCaretInsert(null);
  useWorkspaceStore.setState({ sessions: { "a.md": session("alpha") }, activePath: "a.md", tabs: [], workspaceFrozen: false, vaultStale: false });
  configureApi();
});

describe("==highlight== preprocessing", () => {
  it("turns a pair into <mark> and leaves code alone", () => {
    expect(preprocessHighlights(withLines("a ==hi== b"))).toBe(withLines("a <mark>hi</mark> b"));
    expect(preprocessHighlights(withLines("`==lit==`"))).toBe(withLines("`==lit==`"));
    expect(preprocessHighlights(withLines("```", "==fenced==", "```"))).toBe(withLines("```", "==fenced==", "```"));
    // An empty pair and a lone delimiter are not highlights. A pair *may*
    // contain `=`: stacking formats writes `==<span style="color:#…">x</span>==`,
    // whose tag is full of them.
    expect(preprocessHighlights(withLines("a ==== b"))).toBe(withLines("a ==== b"));
    expect(preprocessHighlights(withLines("a == b"))).toBe(withLines("a == b"));
    expect(preprocessHighlights(withLines("a ==x=y== b"))).toBe(withLines("a <mark>x=y</mark> b"));
    expect(preprocessHighlights(withLines('a ==<span style="color:#e5484d">x</span>== b')))
      .toBe(withLines('a <mark><span style="color:#e5484d">x</span></mark> b'));
  });

  it("renders the mark, the colour span and a GFM table", () => {
    expect(renderMarkdown("a ==hi== b")).toContain("<mark>hi</mark>");

    const colored = renderMarkdown('<span style="color:#e5484d">red</span>');
    expect(colored).toContain('style="color:#e5484d"');
    expect(colored).toContain("red");

    const table = renderMarkdown(withLines("| a | b |", "| --- | --- |", "| 1 | 2 |"));
    expect(table).toContain("<table>");
    expect(table).toContain("<th>a</th>");
    expect(table).toContain("<td>2</td>");
  });

  it("keeps only a bare colour declaration in a hand-written style", () => {
    const combined = renderMarkdown('<span style="color:#e5484d;background:url(//evil)">x</span>');
    expect(combined).not.toContain("background");
    expect(combined).not.toContain("evil");
    expect(combined).toContain("x");

    const overlay = renderMarkdown('<span style="position:fixed;top:0">x</span>');
    expect(overlay).not.toContain("position");
    expect(overlay).not.toContain("fixed");

    const handler = renderMarkdown('<span onclick="alert(1)">x</span>');
    expect(handler).not.toContain("onclick");
    // The same allow-list keeps the pre-existing attachment behaviour intact.
    expect(renderMarkdown('<img src="javascript:alert(1)">')).not.toContain("javascript:");
  });

  it("styles the rendered mark in the preview pane", () => {
    const { container } = render(<Preview source={withLines("a ==hi== b")}/>);
    expect(container.querySelector("mark")?.textContent).toBe("hi");
  });
});

describe("editor formatting commands", () => {
  it("toggles bold, italic and highlight around a selection", () => {
    const { handle, cm } = mountEditor("hello world");

    select(cm, 0, 5);
    expect(handle.current!.toggleBold()).toBe(true);
    expect(cm.state.doc.toString()).toBe("**hello** world");

    // The same command on the still-wrapped selection removes it again.
    select(cm, 0, 9);
    handle.current!.toggleBold();
    expect(cm.state.doc.toString()).toBe("hello world");

    select(cm, 0, 5);
    handle.current!.toggleItalic();
    expect(cm.state.doc.toString()).toBe("*hello* world");

    select(cm, 0, 7);
    handle.current!.toggleHighlight();
    expect(cm.state.doc.toString()).toBe("==*hello*== world");
  });

  it("unwraps the pair the caret sits inside, and only that one", () => {
    const first = mountEditor("**bold** and **two**");
    select(first.cm, 4);
    first.handle.current!.toggleBold();
    expect(first.cm.state.doc.toString()).toBe("bold and **two**");

    // The caret inside the second pair only removes that pair.
    const second = mountEditor("**a** and **b**");
    select(second.cm, 13);
    second.handle.current!.toggleBold();
    expect(second.cm.state.doc.toString()).toBe("**a** and b");

    const third = mountEditor("==hi==");
    select(third.cm, 3);
    third.handle.current!.toggleHighlight();
    expect(third.cm.state.doc.toString()).toBe("hi");
  });

  it("inserts an empty pair at the caret for typing into", () => {
    const { handle, cm } = mountEditor("hello");
    select(cm, 5);
    handle.current!.toggleBold();
    expect(cm.state.doc.toString()).toBe("hello****");
    // The caret sits between the delimiters.
    expect(cm.state.selection.main.from).toBe(7);
    expect(cm.state.selection.main.to).toBe(7);
  });

  it("applies, recolours and removes a text colour", () => {
    const value = '<span style="color:#e5484d">red</span>';
    const { handle, cm } = mountEditor(value);
    const start = value.indexOf("red");

    select(cm, start, start + 3);
    handle.current!.applyTextColor("#3b7dd8");
    expect(cm.state.doc.toString()).toBe('<span style="color:#3b7dd8">red</span>');

    // Choosing the colour it already has toggles the span off.
    select(cm, start, start + 3);
    handle.current!.applyTextColor("#3b7dd8");
    expect(cm.state.doc.toString()).toBe("red");

    // An unusable colour is refused instead of written into the note.
    select(cm, 0, 3);
    expect(handle.current!.applyTextColor("red;position:fixed")).toBe(false);
  });

  it("recolours a selection that already includes its span", () => {
    const value = '<span style="color:#e5484d">red</span>';
    const { handle, cm } = mountEditor(value);
    select(cm, 0, value.length);
    handle.current!.applyTextColor("#2f9e63");
    expect(cm.state.doc.toString()).toBe('<span style="color:#2f9e63">red</span>');
  });

  it("clears every format the commands can create", () => {
    const { handle, cm } = mountEditor("**bold** ==hi== <span style=\"color:#e5484d\">red</span>");
    select(cm, 0, cm.state.doc.length);
    handle.current!.clearFormatting();
    expect(cm.state.doc.toString()).toBe("bold hi red");
  });

  it("normalises authored colours", () => {
    expect(normalizeColor("#E5484D")).toBe("#e5484d");
    expect(normalizeColor(" #abc ")).toBe("#abc");
    expect(normalizeColor("rgb(1,2,3)")).toBeNull();
    expect(normalizeColor("#12345")).toBeNull();
  });
});

describe("multi-line and stacked formatting", () => {
  it("gives every line of a multi-line selection its own pair", () => {
    const { handle, cm } = mountEditor(withLines("alpha", "beta"));
    select(cm, 0, cm.state.doc.length);
    handle.current!.toggleBold();
    expect(cm.state.doc.toString()).toBe(withLines("**alpha**", "**beta**"));
    // Re-applying it over the same selection unwraps both lines again.
    select(cm, 0, cm.state.doc.length);
    handle.current!.toggleBold();
    expect(cm.state.doc.toString()).toBe(withLines("alpha", "beta"));
  });

  it("highlights and colours a multi-line selection line by line", () => {
    const highlighted = mountEditor(withLines("alpha", "beta"));
    select(highlighted.cm, 0, highlighted.cm.state.doc.length);
    highlighted.handle.current!.toggleHighlight();
    expect(highlighted.cm.state.doc.toString()).toBe(withLines("==alpha==", "==beta=="));

    const coloured = mountEditor(withLines("alpha", "beta"));
    select(coloured.cm, 0, coloured.cm.state.doc.length);
    coloured.handle.current!.applyTextColor("#e5484d");
    expect(coloured.cm.state.doc.toString()).toBe(withLines('<span style="color:#e5484d">alpha</span>', '<span style="color:#e5484d">beta</span>'));
    // The colour it already has toggles both spans off again.
    select(coloured.cm, 0, coloured.cm.state.doc.length);
    coloured.handle.current!.applyTextColor("#e5484d");
    expect(coloured.cm.state.doc.toString()).toBe(withLines("alpha", "beta"));
  });

  it("wraps only the selected part of the first and last line", () => {
    const { handle, cm } = mountEditor(withLines("one two", "three four", "five six"));
    // From inside "two" to inside "five".
    select(cm, 4, 22);
    handle.current!.toggleHighlight();
    expect(cm.state.doc.toString()).toBe(withLines("one ==two==", "==three four==", "==fiv==e six"));
  });

  it("repairs a pair the older command left across lines", () => {
    const { handle, cm } = mountEditor(withLines("==alpha", "beta=="));
    select(cm, 0, cm.state.doc.length);
    handle.current!.toggleHighlight();
    expect(cm.state.doc.toString()).toBe(withLines("==alpha==", "==beta=="));
    // A bullet keeps its list marker: only the orphaned `==` halves are dropped.
    const list = mountEditor(withLines("==alpha", "* item=="));
    select(list.cm, 0, list.cm.state.doc.length);
    list.handle.current!.toggleHighlight();
    expect(list.cm.state.doc.toString()).toBe(withLines("==alpha==", "==* item=="));
  });

  it("stacks formats inside the colour span instead of around it", () => {
    const { handle, cm } = mountEditor("alpha");
    select(cm, 0, 5);
    handle.current!.applyTextColor("#e5484d");
    select(cm, 0, cm.state.doc.length);
    handle.current!.toggleHighlight();
    // `==<span …>==` would be a pair no highlight scanner can read back.
    expect(cm.state.doc.toString()).toBe('<span style="color:#e5484d">==alpha==</span>');
    select(cm, 0, cm.state.doc.length);
    handle.current!.toggleBold();
    expect(cm.state.doc.toString()).toBe('<span style="color:#e5484d">**==alpha==**</span>');
    // Both pairs toggle back off without leaving doubled markers behind.
    select(cm, 0, cm.state.doc.length);
    handle.current!.toggleBold();
    select(cm, 0, cm.state.doc.length);
    handle.current!.toggleHighlight();
    expect(cm.state.doc.toString()).toBe('<span style="color:#e5484d">alpha</span>');
  });

  it("renders what the commands write, on every line and stacked", () => {
    const bold = renderMarkdown(withLines("**alpha**", "**beta**"));
    expect(bold).toContain("<strong>alpha</strong>");
    expect(bold).toContain("<strong>beta</strong>");

    const highlight = renderMarkdown(withLines("==alpha==", "==beta=="));
    expect(highlight).toContain("<mark>alpha</mark>");
    expect(highlight).toContain("<mark>beta</mark>");

    const stacked = renderMarkdown('<span style="color:#e5484d">==alpha==</span>');
    expect(stacked).toContain("<mark>alpha</mark>");
    expect(stacked).toContain('style="color:#e5484d"');
    expect(renderMarkdown('<span style="color:#e5484d">**alpha**</span>')).toContain("<strong>alpha</strong>");
  });
});

describe("table insertion", () => {
  it("builds a header, a separator and the requested body rows", () => {
    expect(buildTableMarkdown(2, 2)).toBe(withLines("|  |  |", "| --- | --- |", "|  |  |").trimEnd());
    expect(buildTableMarkdown(3, 3).split(NL)).toHaveLength(4);
    // Out-of-range requests are clamped, never rejected with a broken table.
    expect(buildTableMarkdown(0, 0).split(NL).length).toBeGreaterThanOrEqual(2);
  });

  it("inserts the table as its own block and parks the caret in the first cell", () => {
    const { handle, cm } = mountEditor("before\nafter");
    select(cm, 7);
    expect(handle.current!.insertTable(3, 2)).toBe(true);
    const doc = cm.state.doc.toString();
    expect(doc.startsWith(`before${NL}|  |  |`)).toBe(true);
    expect(doc).toContain("| --- | --- |");
    expect(doc.split(NL).filter(line => line.startsWith("|"))).toHaveLength(4);
    // The caret sits inside the first header cell of the inserted table:
    // "before\n" then "|" + " " + caret + " " + "|".
    expect(cm.state.selection.main.from).toBe(9);
    expect(cm.state.doc.sliceString(7, 11)).toBe("|  |");
  });
});

describe("live preview decorations", () => {
  it("hides the == delimiters and marks the content", async () => {
    const { cm, container } = mountEditor(withLines("==hi==", "plain"), {});
    select(cm, cm.state.doc.length);
    await waitFor(() => expect(container.querySelector(".cm-lp-highlight")).toBeTruthy());
    expect(container.querySelector(".cm-lp-highlight")!.textContent).toBe("hi");
    expect(container.querySelectorAll(".cm-lp-highlight")).toHaveLength(1);
  });

  it("marks every line of a per-line highlight", async () => {
    const { cm, container } = mountEditor(withLines("==alpha==", "==beta==", "plain"), {});
    select(cm, cm.state.doc.length);
    await waitFor(() => expect(container.querySelectorAll(".cm-lp-highlight")).toHaveLength(2));
    expect(Array.from(container.querySelectorAll(".cm-lp-highlight")).map(node => node.textContent))
      .toEqual(["alpha", "beta"]);
  });

  it("renders stacked formats and a colour span over a line break", async () => {
    const stacked = mountEditor(withLines('<span style="color:#e5484d">==alpha==</span>', "plain"), {});
    select(stacked.cm, stacked.cm.state.doc.length);
    await waitFor(() => expect(stacked.container.querySelector(".cm-lp-highlight")).toBeTruthy());
    expect(stacked.container.querySelector('.cm-content span[style*="#e5484d"]')).toBeTruthy();

    // A span left behind by the older command across a soft line break.
    const wrapped = mountEditor(`<span style="color:#e5484d">alpha${NL}beta</span>${NL}plain`, {});
    select(wrapped.cm, wrapped.cm.state.doc.length);
    await waitFor(() => expect(wrapped.container.querySelector('.cm-content span[style*="#e5484d"]')).toBeTruthy());
  });

  it("renders a colour span as styled text", async () => {
    const { cm, container } = mountEditor(withLines('<span style="color:#e5484d">red</span>', "plain"), {});
    select(cm, cm.state.doc.length);
    await waitFor(() => expect(container.querySelector('.cm-content span[style*="#e5484d"]')).toBeTruthy());
    expect(container.querySelector('.cm-content span[style*="#e5484d"]')!.textContent).toBe("red");
  });
});

describe("keyboard shortcuts", () => {
  it("binds bold, italic and highlight", () => {
    const { cm } = mountEditor("line");
    const facet = cm.state.facet(keymap) as unknown as ({ key?: string }[] | { key?: string })[];
    const keys = facet.flat().map(binding => binding.key);
    expect(keys).toContain("Mod-b");
    expect(keys).toContain("Ctrl-b");
    expect(keys).toContain("Mod-i");
    expect(keys).toContain("Mod-Shift-h");
  });
});

describe("format toolbar", () => {
  const commands = (): EditorFormatCommands => ({
    toggleBold: vi.fn(() => true),
    toggleItalic: vi.fn(() => true),
    toggleHighlight: vi.fn(() => true),
    applyTextColor: vi.fn(() => true),
    clearFormatting: vi.fn(() => true),
    insertTable: vi.fn(() => true),
  });

  it("runs each inline command from its button", async () => {
    const bound = commands();
    render(<FormatToolbar commands={bound}/>);
    await userEvent.click(screen.getByRole("button", { name: "Bold (Ctrl+B)" }));
    await userEvent.click(screen.getByRole("button", { name: "Italic (Ctrl+I)" }));
    await userEvent.click(screen.getByRole("button", { name: "Highlight (Ctrl+Shift+H)" }));
    await userEvent.click(screen.getByRole("button", { name: "Clear formatting" }));
    expect(bound.toggleBold).toHaveBeenCalled();
    expect(bound.toggleItalic).toHaveBeenCalled();
    expect(bound.toggleHighlight).toHaveBeenCalled();
    expect(bound.clearFormatting).toHaveBeenCalled();
  });

  it("offers the palette behind the colour button and applies a swatch", async () => {
    const bound = commands();
    render(<FormatToolbar commands={bound}/>);
    expect(screen.queryByRole("button", { name: "Red text" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Text colour" }));
    await userEvent.click(screen.getByRole("button", { name: "Red text" }));
    expect(bound.applyTextColor).toHaveBeenCalledWith("#e5484d");
    // Picking closes the popover again.
    expect(screen.queryByRole("button", { name: "Red text" })).not.toBeInTheDocument();
  });

  it("disables every control on a read-only note", () => {
    render(<FormatToolbar commands={commands()} disabled/>);
    expect(screen.getByRole("button", { name: "Bold (Ctrl+B)" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Clear formatting" })).toBeDisabled();
  });
});

describe("editor right-click menu", () => {
  beforeEach(() => {
    useWorkspaceStore.getState().resetVault();
    configureApi();
  });

  it("offers formatting, colours, a table grid and the document actions", async () => {
    await useWorkspaceStore.getState().openFile("a.md");
    render(<WorkspaceShell/>);
    await screen.findByRole("textbox", { name: "Source editor for a.md" });

    fireEvent.contextMenu(document.querySelector(".cm-content")!);
    const menu = screen.getByRole("menu", { name: "Format & insert" });
    expect(within(menu).getByRole("menuitem", { name: "Bold (Ctrl+B)" })).toBeInTheDocument();
    expect(within(menu).getByRole("menuitem", { name: "Clear formatting" })).toBeInTheDocument();
    expect(within(menu).getByRole("button", { name: "Red text" })).toBeInTheDocument();
    // Regression: the nested-document actions still live in this menu.
    expect(within(menu).getByRole("menuitem", { name: "New nested document" })).toBeInTheDocument();
  });

  it("inserts the picked table into the open note", async () => {
    await useWorkspaceStore.getState().openFile("a.md");
    render(<WorkspaceShell/>);
    const textbox = await screen.findByRole("textbox", { name: "Source editor for a.md" });
    // Put the caret at the end of the note so the table lands after the text.
    const view = ((textbox.querySelector(".cm-content") as unknown as { cmView: { view: EditorView } }).cmView.view);
    view.dispatch({ selection: { anchor: view.state.doc.length } });

    fireEvent.contextMenu(textbox.querySelector(".cm-content")!);
    await userEvent.click(within(screen.getByRole("menu")).getByRole("button", { name: "2 × 2 table" }));

    await waitFor(() => expect(useWorkspaceStore.getState().sessions["a.md"]!.content).toContain("| --- | --- |"));
    const content = useWorkspaceStore.getState().sessions["a.md"]!.content;
    expect(content.startsWith("alpha")).toBe(true);
    expect(content.split(NL).filter(line => line.startsWith("|"))).toHaveLength(3);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("applies a colour span picked from the menu", async () => {
    await useWorkspaceStore.getState().openFile("a.md");
    render(<WorkspaceShell/>);
    const textbox = await screen.findByRole("textbox", { name: "Source editor for a.md" });
    const view = ((textbox.querySelector(".cm-content") as unknown as { cmView: { view: EditorView } }).cmView.view);
    view.dispatch({ selection: { anchor: 0, head: 5 } });

    fireEvent.contextMenu(textbox.querySelector(".cm-content")!);
    await userEvent.click(within(screen.getByRole("menu")).getByRole("button", { name: "Blue text" }));

    await waitFor(() => expect(useWorkspaceStore.getState().sessions["a.md"]!.content).toContain('<span style="color:#3b7dd8">alpha</span>'));
  });

  it("wraps the selection in ==highlight== from the menu", async () => {
    await useWorkspaceStore.getState().openFile("a.md");
    render(<WorkspaceShell/>);
    const textbox = await screen.findByRole("textbox", { name: "Source editor for a.md" });
    const view = ((textbox.querySelector(".cm-content") as unknown as { cmView: { view: EditorView } }).cmView.view);
    view.dispatch({ selection: { anchor: 0, head: 5 } });

    fireEvent.contextMenu(textbox.querySelector(".cm-content")!);
    await userEvent.click(within(screen.getByRole("menu")).getByRole("menuitem", { name: "Highlight (Ctrl+Shift+H)" }));

    await waitFor(() => expect(useWorkspaceStore.getState().sessions["a.md"]!.content).toBe("==alpha=="));
  });
});
