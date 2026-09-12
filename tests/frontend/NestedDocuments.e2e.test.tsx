/**
 * Opt-in integration test for nested documents against a real server.
 *
 * It drives the real `WorkspaceApi` (plain `fetch`) through the workspace
 * store and renders the real Vault listing, so it verifies the whole chain:
 * "新建子文档" → POST /vault/directory + POST /vault/file → GET /vault/files →
 * nested tree → collapse/expand per level.
 *
 * Skipped unless `LOCALNOTE_E2E_BASE_URL` is set (see `tests/frontend/.env.test`
 * for the disposable-Vault recipe). It never runs in the default gates.
 */
import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { render } from "./render";
import { FileTree } from "../../apps/web/src/components/FileTree";
import type { FileMutationResponse, VaultFileEntry } from "../../packages/protocol/src";
import { buildTreeHierarchy, configureWorkspaceApi, foldedPaths, useWorkspaceStore } from "../../packages/workspace/src";

/** Live tree wired to the store, so a collapse toggles a real re-render. */
function LiveTree() {
  const tree = useWorkspaceStore((state) => state.tree);
  const toggle = useWorkspaceStore((state) => state.toggleDirectory);
  return <FileTree entries={tree.entries} expanded={tree.expandedPaths} collapsed={tree.collapsedPaths} onToggle={toggle} onOpen={() => undefined}/>;
}

const baseUrl = import.meta.env.LOCALNOTE_E2E_BASE_URL as string | undefined;
const live = Boolean(baseUrl);
let vaultSession: string | null = null;

/** Real HTTP client for the same endpoints `apps/web/src/api/client.ts` uses. */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("Content-Type", "application/json");
  if (vaultSession) headers.set("X-LocalNote-Vault-Session", vaultSession);
  const response = await fetch(`${baseUrl}/api/v1${path}`, { ...init, headers });
  const body = await response.json().catch(() => null) as { error?: { code?: string; message?: string } } | null;
  if (!response.ok) throw Object.assign(new Error(body?.error?.message ?? "request failed"), { status: response.status, code: body?.error?.code });
  if (path === "/settings") vaultSession = (body as unknown as { vault_session_id: string }).vault_session_id;
  return body as T;
}

function liveApi() {
  return {
    fetchVaultFiles: (options?: { recursive?: boolean }) => request<{ entries: VaultFileEntry[] }>(`/vault/files?recursive=${String(options?.recursive ?? true)}&include_hidden=false`),
    fetchVaultFile: (path: string) => request<{ path: string; content_base64: string; byte_length: number; sha256: string; content_type: string | null }>(`/vault/file?${new URLSearchParams({ path })}`),
    patchVaultFile: (args: { path: string; contentBase64: string; expectedSha256: string }) => request<FileMutationResponse>("/vault/file", { method: "PATCH", body: JSON.stringify({ path: args.path, content_base64: args.contentBase64, expected_sha256: args.expectedSha256 }) }),
    createVaultDirectory: (args: { path: string }) => request<FileMutationResponse>("/vault/directory", { method: "POST", body: JSON.stringify({ path: args.path }) }),
    createVaultFile: (args: { path: string; contentBase64: string }) => request<FileMutationResponse>("/vault/file", { method: "POST", body: JSON.stringify({ path: args.path, content_base64: args.contentBase64 }) }),
    fetchLinks: (path: string) => request<{ outgoing: never[]; broken_count: number }>(`/links/${path}`),
    fetchBacklinks: (path: string) => request<{ backlinks: never[] }>(`/backlinks/${path}`),
  };
}

const store = () => useWorkspaceStore.getState();
/** Unique parent per run so the spec is repeatable against one Vault. */
let parent = "";

describe.skipIf(!live)("nested documents against a real backend", () => {
  beforeEach(async () => {
    parent = `e2e-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    await request("/settings");
    configureWorkspaceApi(liveApi());
    store().resetVault();
    await store().createNote(`${parent}.md`, "# parent\n");
  });

  it("writes the nested layout to disk and derives the same tree the UI renders", async () => {
    const childPath = await store().createChildNote(`${parent}.md`, { name: "child" });
    expect(childPath).toBe(`${parent}/child.md`);
    const grandchild = await store().createChildNote(childPath, { name: "grandchild" });
    expect(grandchild).toBe(`${parent}/child/grandchild.md`);

    // The real listing holds the child folder beside the note and the nested
    // documents inside it.
    await store().loadTree();
    const listed = store().tree.entries.map(entry => entry.path);
    expect(listed).toContain(parent);
    expect(listed).toContain(`${parent}/child.md`);
    expect(listed).toContain(grandchild);

    // Every container is expanded on load, so the whole nested structure shows.
    expect(store().tree.collapsedPaths).toEqual([]);
    render(<LiveTree/>);
    expect(screen.getByRole("button", { name: `${parent}.md` }).getAttribute("aria-expanded")).toBe("true");

    const nodes = buildTreeHierarchy(store().tree.entries);
    const parentNode = nodes.find(node => node.path === `${parent}.md`)!;
    const shape = (node: { path: string; children: { path: string }[] }): Array<{ path: string; children: string[] }> =>
      node.children.map(child => ({ path: child.path, children: [] }));
    expect(shape(parentNode).map(entry => entry.path)).toEqual([parent, `${parent}/child.md`]);
    expect(foldedPaths(store().tree.entries).get(`${parent}/child.md`)).toBe(`${parent}.md`);
    expect(foldedPaths(store().tree.entries).get(grandchild)).toBe(`${parent}/child.md`);
  });

  it("links the nested document from its parent, on disk", async () => {
    await store().openFile(`${parent}.md`);
    const childPath = await store().createChildNote(`${parent}.md`, { name: "linked" });
    expect(childPath).toBe(`${parent}/linked.md`);

    // The parent got a real wikilink and that content reached the file system.
    await waitFor(() => expect(store().sessions[`${parent}.md`]!.dirty).toBe(false));
    const sent = await fetch(`${baseUrl}/api/v1/vault/file?${new URLSearchParams({ path: `${parent}.md` })}`, { headers: { "X-LocalNote-Vault-Session": vaultSession ?? "" } }).then(response => response.json()) as { content_base64: string };
    expect(atob(sent.content_base64)).toContain("[[linked]]");
  });

  it("creates a sibling document next to the nested one", async () => {
    const childPath = await store().createChildNote(`${parent}.md`, { name: "child" });
    const sibling = await store().createSiblingNote(childPath, { name: "sibling" });
    expect(sibling).toBe(`${parent}/sibling.md`);

    // Both land in the same folder, which belongs to the parent note.
    await store().loadTree();
    const childDir = store().tree.entries.filter(entry => entry.path.startsWith(`${parent}/`) && !entry.path.slice(parent.length + 1).includes("/")).map(entry => entry.path);
    expect(childDir.sort()).toEqual([`${parent}/child.md`, `${parent}/sibling.md`]);
  });
});
