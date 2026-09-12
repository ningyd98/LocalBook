import type { VaultFileEntry } from "@localnote/protocol";
import type { FileTreeExpansion } from "./types";

/**
 * One rendered row of the nested file tree.
 *
 * `children` is the recursive continuation of the same kind of row, so a note
 * can own a whole subtree: the sibling-folder convention
 * (`notes/A.md` + `notes/A/…`) makes that folder part of the note instead of a
 * second, unrelated row.
 */
export interface TreeHierarchyNode {
  path: string;
  kind: "file" | "directory";
  name: string;
  depth: number;
  children: TreeHierarchyNode[];
}

/**
 * For every row that renders **under** another row, the path of the document
 * that owns it. Ownership follows exactly what "新建子文档" writes:
 *
 * - `Notes/A/` is owned by the note `Notes/A.md` beside it, and every row
 *   inside `Notes/A/` hangs from that same note — so one document level
 *   collapses together with its own children, at any depth.
 * - `Notes/A/B.md` is owned by `Notes/A.md` through the folder `Notes/A/`.
 * - Any other row follows the folder it lives in, so `docs/child.md` stays
 *   inside the plain folder `docs/` and collapses with it.
 * - `notes/A/index.md` owns its folder, so a folder note never renders twice.
 *
 * A folder whose name merely looks like a note (`docs/` with no `docs.md`
 * beside it) has no note owner and keeps its own top-level row.
 */
export function foldedPaths(entries: readonly VaultFileEntry[]): Map<string, string> {
  const paths = new Set<string>();
  // Listing order is what makes this a single pass: a folder is always listed
  // before its contents and a folder note before its folder's rows.
  for (const entry of entries) if (!entry.path.includes("\u0000")) paths.add(entry.path);
  const owner = new Map<string, string>();
  for (const entry of entries) {
    const path = entry.path;
    if (!paths.has(path) || owner.has(path)) continue;
    const segments = path.split("/");
    const folder = segments.slice(0, -1).join("/");
    const name = (segments[segments.length - 1] ?? path).toLowerCase();
    // A folder note owns the folder it describes (in both directions).
    if (folder && entry.kind === "file" && name === "index.md") {
      owner.set(path, folder);
      owner.set(folder, path);
      continue;
    }
    // The row this path names (`Notes/A/`) is owned by the note of the same
    // name. Anything else follows its folder: a note's folder resolves to that
    // note (`Notes/A` → `Notes/A.md`), a plain folder to itself (`docs`).
    const sameName = paths.has(`${path}.md`) ? `${path}.md` : null;
    const inherited = folder ? owner.get(folder) ?? (paths.has(folder) ? folder : undefined) : undefined;
    const ownerPath = sameName ?? inherited;
    if (ownerPath) owner.set(path, ownerPath);
  }
  return owner;
}

/**
 * Rows to render for a Vault listing, with every owned path hanging under its
 * owner. `depth` is scoped to the subtree a row belongs to, and siblings keep
 * the listing's order (folders first, because the server sorts by code point
 * and `/` precedes `.`).
 */
export function buildTreeHierarchy(entries: readonly VaultFileEntry[]): TreeHierarchyNode[] {
  const nodes = new Map<string, TreeHierarchyNode>();
  for (const entry of entries) {
    // A control-character path is never part of the tree.
    if (entry.path.includes("\u0000")) continue;
    nodes.set(entry.path, {
      path: entry.path,
      kind: entry.kind,
      name: entry.path.split("/").at(-1) ?? entry.path,
      depth: 0,
      children: [],
    });
  }
  // An owned row hangs from its owner *instead of* the folder it physically
  // lives in: `docs/child.md` is a child document of `docs.md`, so it renders
  // under that note rather than inside the folder `docs/`.
  const owner = foldedPaths(entries);
  for (const [path, ownerPath] of owner) {
    const ownerNode = nodes.get(ownerPath);
    const child = nodes.get(path);
    if (ownerNode && child) ownerNode.children.push(child);
  }

  const roots = [...nodes.values()].filter(node => !owner.has(node.path));
  const byPath = (a: TreeHierarchyNode, b: TreeHierarchyNode) => a.path.localeCompare(b.path);
  const attach = (node: TreeHierarchyNode, depth: number) => {
    node.depth = depth;
    node.children.sort(byPath);
    for (const child of node.children) attach(child, depth + 1);
  };
  roots.sort(byPath);
  for (const node of roots) attach(node, 0);
  return roots;
}

/**
 * Which rows the tree shows.
 *
 * A row is visible unless one of its **owners**, or one of its ancestor
 * folders, is collapsed. The owner chain is not the path prefix chain:
 * `Notes/A/B.md` is owned by the note `Notes/A.md` through the folder
 * `Notes/A`. Collapsing a document therefore hides every document nested
 * inside it, at any depth, while its own row stays visible.
 */
export function visibleTreeRows(nodes: readonly TreeHierarchyNode[], owner: ReadonlyMap<string, string>, expansion: FileTreeExpansion): TreeHierarchyNode[] {
  const collapsed = new Set(expansion.collapsedPaths);
  const hidden = (path: string) => {
    if (collapsed.has(path) || !expansion.expandedPaths.includes(path)) return true;
    const parts = path.split("/");
    for (let depth = 1; depth < parts.length; depth += 1) {
      if (collapsed.has(parts.slice(0, depth).join("/"))) return true;
    }
    // The chain is finite: an owner path is always shorter than the row's own.
    for (let ownerPath = owner.get(path); ownerPath; ownerPath = owner.get(ownerPath)) {
      if (collapsed.has(ownerPath)) return true;
    }
    return false;
  };
  const rows: TreeHierarchyNode[] = [];
  const walk = (node: TreeHierarchyNode) => {
    rows.push(node);
    if (hidden(node.path)) return;
    for (const child of node.children) walk(child);
  };
  for (const node of nodes) walk(node);
  return rows;
}

/**
 * True when `path` currently shows its children. Only two things close a row:
 * the row itself being collapsed, or an ancestor folder being collapsed.
 * Everything else is open, so a folder or a freshly created child document is
 * visible the moment it appears.
 */
export function isPathExpanded(path: string, expansion: FileTreeExpansion): boolean {
  if (!expansion.expandedPaths.includes(path) || expansion.collapsedPaths.includes(path)) return false;
  const parts = path.split("/");
  for (let index = 1; index < parts.length; index += 1) {
    if (expansion.collapsedPaths.includes(parts.slice(0, index).join("/"))) return false;
  }
  return true;
}

/**
 * Paths that count as expanded when a listing renders "everything open":
 * every folder plus every note, which is exactly the listing itself.
 */
export function expandablePaths(entries: readonly VaultFileEntry[]): string[] {
  return entries.map(entry => entry.path);
}
