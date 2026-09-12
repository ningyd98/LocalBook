/**
 * Deleting: the file tree moves a file or folder to the recycle bin, and the
 * permanent `DELETE /vault/file` remains the primitive behind it.
 *
 * `moveToTrash` confirms the digest against the bytes on disk, closes every
 * affected tab and session, and the shell dialog explains the retention window;
 * `deleteEntry` (the byte-level primitive) keeps its own guarantees, including
 * refusing a folder the way the server does.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "./render";
import { FileTree } from "../../apps/web/src/components/FileTree";
import { WorkspaceShell } from "../../apps/web/src/components/WorkspaceShell";
import type { VaultFileEntry } from "../../packages/protocol/src";
import { configureWorkspaceApi, useWorkspaceStore } from "../../packages/workspace/src";
import type { WorkspaceApi } from "../../packages/workspace/src";

const b64 = (value: string) => btoa(value);
const dir = (path: string): VaultFileEntry => ({ path, kind: "directory", size: null, sha256: null });
const file = (path: string): VaultFileEntry => ({ path, kind: "file", size: 4, sha256: `sha256:${path}` });
const store = () => useWorkspaceStore.getState();
const ENTRIES: VaultFileEntry[] = [file("Notes.md"), dir("Notes"), file("Notes/child.md"), file("Other.md")];

function configureApi(overrides: Partial<WorkspaceApi> = {}) {
  const removed: string[] = [];
  configureWorkspaceApi({
    fetchVaultFiles: vi.fn(async () => ({ entries: ENTRIES.filter(entry => !removed.some(path => entry.path === path || entry.path.startsWith(`${path}/`))) })),
    fetchVaultFile: vi.fn(async (path: string) => ({ path, content_base64: b64("body"), byte_length: 4, sha256: `sha256:${path}`, content_type: "text/markdown" })),
    patchVaultFile: vi.fn(async () => { throw new Error("unused"); }),
    deleteVaultFile: vi.fn(async ({ path, expectedSha256 }: { path: string; expectedSha256: string }) => {
      expect(expectedSha256).toBe(`sha256:${path}`);
      removed.push(path);
      return { path, sha256: null, byte_length: null, operation: "deleted" as const };
    }),
    moveToTrash: vi.fn(async ({ path }: { path: string }) => {
      removed.push(path);
      return { id: `id-${path}`, original_path: path, name: path, kind: "file" as const, byte_length: 4, file_count: 1, deleted_at: new Date().toISOString(), expires_at: new Date(Date.now() + 30 * 86400_000).toISOString(), days_remaining: 30 };
    }),
    fetchTrash: vi.fn(async () => ({ entries: [], count: 0, total_bytes: 0, retention_days: 30, generated_at: new Date().toISOString() })),
    fetchLinks: vi.fn(async (path: string) => ({ path, outgoing: [], broken_count: 0 })),
    fetchBacklinks: vi.fn(async (path: string) => ({ path, backlinks: [] })),
    ...overrides,
  });
  return { removed };
}

/** Open a row's context menu (the row itself, never a same-named menu item). */
function openRowMenu(rowPath: string) {
  fireEvent.contextMenu(screen.getByRole("button", { name: rowPath }));
}
/** Pick one entry of the currently open context menu. */
async function pickMenuItem(item: string) {
  await userEvent.click(within(screen.getByRole("menu")).getByRole("menuitem", { name: item }));
}
async function useRowMenu(rowPath: string, item: string) {
  openRowMenu(rowPath);
  await pickMenuItem(item);
}

beforeEach(() => {
  localStorage.clear();
  store().resetVault();
});

describe("deleteEntry store action", () => {
  it("confirms the digest, removes the file and closes its tabs", async () => {
    const { removed } = configureApi();
    await store().loadTree();
    await store().openFile("Notes/child.md");
    expect(store().tabs).toHaveLength(1);

    const deleted = await store().deleteEntry("Notes/child.md");

    expect(deleted).toBe("Notes/child.md");
    expect(removed).toEqual(["Notes/child.md"]);
    expect(store().tabs).toEqual([]);
    expect(store().activePath).toBeNull();
    expect(store().sessions["Notes/child.md"]).toBeUndefined();
    expect(store().tree.entries.map(entry => entry.path)).not.toContain("Notes/child.md");
  });

  it("keeps the other tabs open and re-activates the neighbour", async () => {
    configureApi();
    await store().loadTree();
    await store().openFile("Notes.md");
    await store().openFile("Other.md");

    await store().deleteEntry("Other.md");

    expect(store().tabs.map(tab => tab.path)).toEqual(["Notes.md"]);
    expect(store().activePath).toBe("Notes.md");
  });

  it("refuses a folder that still contains files, without sending a request", async () => {
    const { removed } = configureApi();
    await store().loadTree();

    await expect(store().deleteEntry("Notes")).rejects.toMatchObject({ code: "folder_not_empty" });
    expect(removed).toEqual([]);
    expect(store().tree.entries.map(entry => entry.path)).toContain("Notes/child.md");
  });

  it("refuses an empty folder too, matching the server (files only)", async () => {
    const { removed } = configureApi({ fetchVaultFiles: vi.fn(async () => ({ entries: [file("Notes.md"), dir("Empty")] })) });
    await store().loadTree();

    await expect(store().deleteEntry("Empty")).rejects.toMatchObject({ code: "not_a_file" });
    expect(removed).toEqual([]);
    expect(store().tree.entries.map(entry => entry.path)).toContain("Empty");
  });

  it("reports an unknown path instead of calling the API", async () => {
    const { removed } = configureApi();
    await store().loadTree();
    await expect(store().deleteEntry("ghost.md")).rejects.toMatchObject({ code: "not_found" });
    await expect(store().deleteEntry("   ")).rejects.toMatchObject({ code: "invalid_request" });
    expect(removed).toEqual([]);
  });

  it("surfaces a conflict when the file changed on disk", async () => {
    configureApi({
      deleteVaultFile: vi.fn(async () => { throw Object.assign(new Error("File changed"), { status: 409, code: "file_conflict" }); }),
    });
    await store().loadTree();
    await expect(store().deleteEntry("Other.md")).rejects.toMatchObject({ code: "file_conflict" });
    expect(store().tree.entries.map(entry => entry.path)).toContain("Other.md");
  });
});

describe("delete entry points", () => {
  it("offers Delete on a file row and on a folder row", async () => {
    const onDelete = vi.fn();
    render(<FileTree entries={ENTRIES} expanded={ENTRIES.map(entry => entry.path)} onToggle={vi.fn()} onOpen={vi.fn()} onDelete={onDelete}/>);

    await useRowMenu("Notes/child.md", "Move to recycle bin");
    await useRowMenu("Notes", "Move to recycle bin");

    expect(onDelete.mock.calls.map(call => call[0])).toEqual(["Notes/child.md", "Notes"]);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("confirms in the shell, then removes the file and closes the tab", async () => {
    configureApi();
    await store().loadTree();
    await store().openFile("Notes/child.md");
    render(<WorkspaceShell/>);

    await screen.findByRole("button", { name: "Notes/child.md" });
    await useRowMenu("Notes/child.md", "Move to recycle bin");

    const dialog = await screen.findByRole("dialog", { name: "Move to recycle bin" });
    expect(within(dialog).getByText(/can be restored for 30 days/i)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Confirm" }));

    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Move to recycle bin" })).not.toBeInTheDocument());
    expect(store().tabs).toEqual([]);
    expect(screen.queryByRole("button", { name: "Notes/child.md" })).not.toBeInTheDocument();
  });

  it("warns about unsaved edits and trashes a folder with its contents", async () => {
    configureApi();
    await store().loadTree();
    await store().openFile("Notes/child.md");
    store().updateContent("Notes/child.md", "draft");
    render(<WorkspaceShell/>);

    await screen.findByRole("button", { name: "Notes/child.md" });
    await useRowMenu("Notes/child.md", "Move to recycle bin");
    const dirty = await screen.findByRole("dialog", { name: "Move to recycle bin" });
    expect(within(dirty).getByText(/unsaved changes/i)).toBeInTheDocument();
    await userEvent.click(within(dirty).getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Move to recycle bin" })).not.toBeInTheDocument());
    // "Notes" also names the dirty tab button, so target the tree row by its title.
    fireEvent.contextMenu(screen.getAllByRole("button", { name: "Notes" }).find(button => button.getAttribute("title") === "Notes")!);
    await pickMenuItem("Move to recycle bin");
    const folder = await screen.findByRole("dialog", { name: "Move to recycle bin" });
    // A folder is trashed as a whole, contents included.
    expect(within(folder).getByText(/and its 1 file\(s\) move to the recycle bin/i)).toBeInTheDocument();
    await userEvent.click(within(folder).getByRole("button", { name: "Confirm" }));

    // Both the folder and the file inside it left the tree.
    await waitFor(() => expect(screen.queryByRole("button", { name: "Notes/child.md" })).not.toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Notes" })).not.toBeInTheDocument();
  });
});
