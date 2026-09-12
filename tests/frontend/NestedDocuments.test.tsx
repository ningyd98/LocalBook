/**
 * Nested documents ("新建子文档") and per-level collapse/expand.
 *
 * Covers the pure hierarchy model (which row owns which, what a collapse
 * hides), the store actions that create a nested/sibling document, and every
 * right-click entry point: file tree (note, folder, empty space), editor
 * surface, preview surface and tab.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "./render";
import { FileTree } from "../../apps/web/src/components/FileTree";
import { WorkspaceShell } from "../../apps/web/src/components/WorkspaceShell";
import type { VaultFileEntry } from "../../packages/protocol/src";
import { buildTreeHierarchy, configureWorkspaceApi, expandablePaths, foldedPaths, isPathExpanded, useWorkspaceStore, visibleTreeRows } from "../../packages/workspace/src";
import type { TreeHierarchyNode } from "../../packages/workspace/src";
import type { WorkspaceApi } from "../../packages/workspace/src";

const b64 = (value: string) => btoa(value);
const dir = (path: string): VaultFileEntry => ({ path, kind: "directory", size: null, sha256: null });
const file = (path: string): VaultFileEntry => ({ path, kind: "file", size: 4, sha256: `sha256:${path}` });

/** `docs/child.md` is nested here: the app's own layout puts documents of `docs.md` in `docs/`. */
const NESTED: VaultFileEntry[] = [
  dir("docs"),
  file("docs.md"),
  file("docs/child.md"),
  dir("docs/child"),
  file("docs/child/grandchild.md"),
  file("loose.md"),
];

const paths = (entries: VaultFileEntry[]) => entries.map(entry => entry.path);

function shape(nodes: TreeHierarchyNode[]): Array<{ path: string; children: string[] }> {
  return nodes.map(node => ({ path: node.path, children: node.children.map(child => child.path) }));
}

describe("nested document hierarchy", () => {
  it("nests a folder under the note that owns it, at every level", () => {
    const nodes = buildTreeHierarchy(NESTED);

    // `docs/child.md` is the child document of `docs.md` (written into the
    // folder `docs/`), and `docs/child/` belongs to it in turn.
    expect(shape(nodes)).toEqual([
      { path: "docs.md", children: ["docs", "docs/child.md"] },
      { path: "loose.md", children: [] },
    ]);
    const child = nodes[0]!.children[1]!;
    expect(child.path).toBe("docs/child.md");
    expect(child.children.map(node => node.path)).toEqual(["docs/child", "docs/child/grandchild.md"]);
    expect(child.depth).toBe(1);
    expect(child.children[0]!.depth).toBe(2);
  });

  it("keeps a folder that has no note of the same name as its own row", () => {
    const entries = [dir("plain"), file("plain/note.md")];
    expect(shape(buildTreeHierarchy(entries))).toEqual([
      { path: "plain", children: ["plain/note.md"] },
    ]);
    expect(foldedPaths(entries).get("plain/note.md")).toBe("plain");
  });

  it("hides a whole subtree when its document is collapsed, and nothing else", () => {
    const nodes = buildTreeHierarchy(NESTED);
    const owner = foldedPaths(NESTED);
    const expansion = { expandedPaths: paths(NESTED), collapsedPaths: [] as string[] };
    expect(visibleTreeRows(nodes, owner, expansion).map(row => row.path)).toEqual([
      "docs.md", "docs", "docs/child.md", "docs/child", "docs/child/grandchild.md", "loose.md",
    ]);

    const collapsed = visibleTreeRows(nodes, owner, { ...expansion, collapsedPaths: ["docs.md"] });
    expect(collapsed.map(row => row.path)).toEqual(["docs.md", "loose.md"]);

    // Collapsing a nested document keeps its owner and every sibling row.
    const middle = visibleTreeRows(nodes, owner, { ...expansion, collapsedPaths: ["docs/child.md"] });
    expect(middle.map(row => row.path)).toEqual(["docs.md", "docs", "docs/child.md", "loose.md"]);
  });

  it("reports each row's own expansion state", () => {
    const expansion = { expandedPaths: ["docs.md"], collapsedPaths: ["docs.md"] };
    expect(isPathExpanded("docs.md", expansion)).toBe(false);
    expect(isPathExpanded("loose.md", expansion)).toBe(false); // not a container
    expect(isPathExpanded("loose.md", { ...expansion, collapsedPaths: [] })).toBe(false);
    expect(isPathExpanded("docs.md", { ...expansion, expandedPaths: ["docs.md"], collapsedPaths: [] })).toBe(true);
    // A row inside a collapsed folder cannot show children either.
    expect(isPathExpanded("docs/child.md", { expandedPaths: paths(NESTED), collapsedPaths: ["docs"] })).toBe(false);
    expect(expandablePaths(NESTED)).toEqual(paths(NESTED));
  });
});

function configureApi(entries: VaultFileEntry[], overrides: Partial<WorkspaceApi> = {}) {
  const created: string[] = [];
  const folders: string[] = [];
  configureWorkspaceApi({
    fetchVaultFiles: vi.fn(async () => ({ entries: [...entries, ...created.map(path => file(path)), ...folders.map(path => dir(path))] })),
    fetchVaultFile: vi.fn(async (path: string) => ({ path, content_base64: b64("body"), byte_length: 4, sha256: `sha256:${path}`, content_type: "text/markdown" })),
    patchVaultFile: vi.fn(async () => ({ path: "x", sha256: "sha256:x", byte_length: 1, operation: "updated" as const })),
    createVaultDirectory: vi.fn(async ({ path }: { path: string }) => { folders.push(path); return { path, sha256: null, byte_length: null, operation: "created" as const }; }),
    createVaultFile: vi.fn(async ({ path }: { path: string }) => { created.push(path); return { path, sha256: null, byte_length: 0, operation: "created" as const }; }),
    fetchLinks: vi.fn(async (path: string) => ({ path, outgoing: [], broken_count: 0 })),
    fetchBacklinks: vi.fn(async (path: string) => ({ path, backlinks: [] })),
    ...overrides,
  });
  return { created, folders };
}

describe("createChildNote / createSiblingNote", () => {
  beforeEach(() => {
    localStorage.clear();
    useWorkspaceStoreReset();
  });

  it("creates the child folder and the nested document inside it", async () => {
    const { created, folders } = configureApi([file("Notes/A.md")]);
    await useWorkspaceStoreLoad();

    const path = await store().createChildNote("Notes/A.md", { name: "Untitled" });

    expect(path).toBe("Notes/A/Untitled.md");
    expect(folders).toEqual(["Notes", "Notes/A"]); // parents are created level by level
    expect(created).toEqual(["Notes/A/Untitled.md"]);
    // The new document is revealed and opened.
    expect(store().tree.collapsedPaths).toEqual([]);
    expect(store().activePath).toBe(path);
  });

  it("uses the folder itself for a folder note and avoids name collisions", async () => {
    const { created } = configureApi([dir("day"), file("day/index.md"), file("day/Untitled.md"), file("day/Untitled 2.md")]);
    await useWorkspaceStoreLoad();

    const path = await store().createChildNote("day/index.md", { name: "Untitled" });

    expect(path).toBe("day/Untitled 3.md");
    expect(created).toEqual(["day/Untitled 3.md"]);
  });

  it("creates a sibling next to the note it was asked about", async () => {
    const { created, folders } = configureApi([file("Notes/A.md"), dir("Notes"), dir("Notes/A")]);
    await useWorkspaceStoreLoad();

    const path = await store().createSiblingNote("Notes/A/B.md", { name: "Peer" });

    expect(path).toBe("Notes/A/Peer.md");
    expect(folders).toEqual([]); // both folders already exist
    expect(created).toEqual(["Notes/A/Peer.md"]);
  });

  it("rejects an unusable parent path", async () => {
    configureApi([file("A.md")]);
    await useWorkspaceStoreLoad();
    await expect(store().createChildNote("  ")).rejects.toMatchObject({ code: "invalid_request" });
    await expect(store().createChildNote("../outside.md")).rejects.toMatchObject({ code: "invalid_request" });
  });
});

const store = () => useWorkspaceStore.getState();
const useWorkspaceStoreReset = () => store().resetVault();
const useWorkspaceStoreLoad = () => store().loadTree();

describe("right-click entry points", () => {
  beforeEach(() => {
    localStorage.clear();
    useWorkspaceStoreReset();
    configureApi(NESTED);
  });

  it("offers nested/sibling creation on a note row and runs it", async () => {
    const onChild = vi.fn();
    const onSibling = vi.fn();
    render(<FileTree entries={NESTED} expanded={paths(NESTED)} onToggle={vi.fn()} onOpen={vi.fn()} onNewChildNote={onChild} onNewSiblingNote={onSibling}/>);

    fireEvent.contextMenu(screen.getByRole("button", { name: "docs/child.md" }));
    const menu = screen.getByRole("menu", { name: "File actions" });
    await userEvent.click(within(menu).getByRole("menuitem", { name: "New nested document" }));
    expect(onChild).toHaveBeenCalledWith("docs/child.md");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();

    fireEvent.contextMenu(screen.getByRole("button", { name: "docs/child.md" }));
    await userEvent.click(within(screen.getByRole("menu")).getByRole("menuitem", { name: "New document at this level" }));
    expect(onSibling).toHaveBeenCalledWith("docs/child.md");
  });

  it("offers nested creation on a folder row and on empty tree space", async () => {
    const onChild = vi.fn();
    const onNewRoot = vi.fn();
    render(<FileTree entries={NESTED} expanded={paths(NESTED)} onToggle={vi.fn()} onOpen={vi.fn()} onNewChildNote={onChild} onNewRootNote={onNewRoot}/>);

    fireEvent.contextMenu(screen.getByRole("button", { name: "docs" }));
    await userEvent.click(within(screen.getByRole("menu")).getByRole("menuitem", { name: "New nested document" }));
    expect(onChild).toHaveBeenCalledWith("docs");

    fireEvent.contextMenu(document.querySelector(".file-tree")!);
    await userEvent.click(within(screen.getByRole("menu")).getByRole("menuitem", { name: "New document" }));
    expect(onNewRoot).toHaveBeenCalled();
  });

  it("creates a document from the editor, the preview and a tab", async () => {
    await store().openFile("docs/child.md");
    render(<WorkspaceShell />);
    await screen.findByRole("textbox", { name: "Source editor for docs/child.md" });

    // Editor surface (CodeMirror content DOM).
    fireEvent.contextMenu(document.querySelector(".cm-content")!);
    await userEvent.click(within(screen.getByRole("menu")).getByRole("menuitem", { name: "New nested document" }));
    await waitFor(() => expect(store().sessions["docs/child/Untitled document.md"]).toBeDefined());

    // Preview surface of the newly opened document, then tab right-click.
    const tab = screen.getByRole("tab", { name: /Untitled document/ });
    fireEvent.contextMenu(tab);
    await userEvent.click(within(screen.getByRole("menu")).getByRole("menuitem", { name: "New document at this level" }));
    await waitFor(() => expect(store().sessions["docs/child/Untitled document 2.md"]).toBeDefined());
  });

  it("shows the parent documents of the open note", async () => {
    await store().openFile("docs/child/grandchild.md");
    render(<WorkspaceShell />);

    const ancestors = await screen.findByRole("navigation", { name: "Parent documents" });
    const labels = within(ancestors).getAllByRole("button").map(button => button.textContent);
    expect(labels).toEqual(["docs", "child"]);
    await userEvent.click(within(ancestors).getByRole("button", { name: /child/ }));
    expect(store().activePath).toBe("docs/child.md");
  });
});
