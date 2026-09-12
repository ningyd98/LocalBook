/**
 * Recycle bin: deleting moves the item there (files *and* folders), the panel
 * lists it with the server's retention window, restore puts it back, and
 * "delete forever" / "empty" are the only irreversible actions.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "./render";
import { WorkspaceShell } from "../../apps/web/src/components/WorkspaceShell";
import type { TrashEntryDTO, VaultFileEntry } from "../../packages/protocol/src";
import { configureWorkspaceApi, useWorkspaceStore } from "../../packages/workspace/src";
import type { WorkspaceApi } from "../../packages/workspace/src";

const b64 = (value: string) => btoa(value);
const dir = (path: string): VaultFileEntry => ({ path, kind: "directory", size: null, sha256: null });
const file = (path: string): VaultFileEntry => ({ path, kind: "file", size: 4, sha256: `sha256:${path}` });
const store = () => useWorkspaceStore.getState();
const LIVE: VaultFileEntry[] = [file("Notes.md"), dir("Notes"), file("Notes/child.md"), file("Other.md")];

function trashEntry(path: string, kind: "file" | "directory" = "file", days = 30): TrashEntryDTO {
  const deleted = new Date();
  return { id: `id-${path}`, original_path: path, name: path.split("/").at(-1) ?? path, kind, byte_length: 8, file_count: kind === "directory" ? 2 : 1, deleted_at: deleted.toISOString(), expires_at: new Date(deleted.getTime() + days * 86400_000).toISOString(), days_remaining: days };
}

function configureApi(overrides: Partial<WorkspaceApi> = {}) {
  const live = new Map(LIVE.map(entry => [entry.path, entry]));
  const bin: TrashEntryDTO[] = [];
  configureWorkspaceApi({
    fetchVaultFiles: vi.fn(async () => ({ entries: [...live.values()] })),
    fetchVaultFile: vi.fn(async (path: string) => ({ path, content_base64: b64("body"), byte_length: 4, sha256: `sha256:${path}`, content_type: "text/markdown" })),
    patchVaultFile: vi.fn(async () => { throw new Error("unused"); }),
    moveToTrash: vi.fn(async ({ path }: { path: string; expectedSha256?: string | null }) => {
      const entry = live.get(path);
      if (!entry) throw Object.assign(new Error("gone"), { status: 404, code: "not_found" });
      // A folder takes its contents with it, exactly like the server does.
      for (const key of [...live.keys()]) if (key === path || key.startsWith(`${path}/`)) live.delete(key);
      const trashed = trashEntry(path, entry.kind === "directory" ? "directory" : "file");
      bin.unshift(trashed);
      return trashed;
    }),
    fetchTrash: vi.fn(async () => ({ entries: [...bin], count: bin.length, total_bytes: bin.reduce((total, entry) => total + entry.byte_length, 0), retention_days: 30, generated_at: new Date().toISOString() })),
    restoreTrashEntry: vi.fn(async ({ id, renameIfOccupied }: { id: string; renameIfOccupied?: boolean }) => {
      const index = bin.findIndex(entry => entry.id === id);
      if (index === -1) throw Object.assign(new Error("gone"), { status: 404, code: "not_found" });
      const entry = bin[index]!;
      bin.splice(index, 1);
      const restored = live.has(entry.original_path) && renameIfOccupied ? `${entry.original_path} (restored)` : entry.original_path;
      live.set(restored, entry.kind === "directory" ? dir(restored) : file(restored));
      if (entry.kind === "directory") live.set(`${restored}/child.md`, file(`${restored}/child.md`));
      return { path: restored, sha256: null, byte_length: null, operation: "moved" as const };
    }),
    deleteTrashEntry: vi.fn(async (id: string) => {
      const index = bin.findIndex(entry => entry.id === id);
      if (index >= 0) bin.splice(index, 1);
      return { path: ".", sha256: null, byte_length: null, operation: "deleted" as const };
    }),
    emptyTrash: vi.fn(async () => { bin.length = 0; return { entries: [], count: 0, total_bytes: 0, retention_days: 30, generated_at: new Date().toISOString() }; }),
    fetchLinks: vi.fn(async (path: string) => ({ path, outgoing: [], broken_count: 0 })),
    fetchBacklinks: vi.fn(async (path: string) => ({ path, backlinks: [] })),
    ...overrides,
  });
  return { bin, live };
}

async function useRowMenu(rowPath: string, item: string) {
  fireEvent.contextMenu(screen.getByRole("button", { name: rowPath }));
  await userEvent.click(within(screen.getByRole("menu")).getByRole("menuitem", { name: item }));
}

beforeEach(() => {
  localStorage.clear();
  store().resetVault();
});

describe("trash store actions", () => {
  it("moves a file to the bin, closes its tab and lists it", async () => {
    configureApi();
    await store().loadTree();
    await store().openFile("Notes/child.md");

    const entry = await store().moveToTrash("Notes/child.md");

    expect(entry?.original_path).toBe("Notes/child.md");
    expect(store().tabs).toEqual([]);
    expect(store().activePath).toBeNull();
    expect(store().tree.entries.map(item => item.path)).not.toContain("Notes/child.md");
    expect(store().trash.entries.map(item => item.original_path)).toEqual(["Notes/child.md"]);
  });

  it("moves a whole folder and everything inside it", async () => {
    configureApi();
    await store().loadTree();
    await store().openFile("Notes/child.md");

    const entry = await store().moveToTrash("Notes");

    expect(entry?.kind).toBe("directory");
    const paths = store().tree.entries.map(item => item.path);
    expect(paths).toEqual(["Notes.md", "Other.md"]);
    expect(store().tabs).toEqual([]);
  });

  it("restores an entry and refreshes the tree", async () => {
    configureApi();
    await store().loadTree();
    const entry = await store().moveToTrash("Notes/child.md");

    const restored = await store().restoreFromTrash(entry!);

    expect(restored).toBe("Notes/child.md");
    expect(store().tree.entries.map(item => item.path)).toContain("Notes/child.md");
    expect(store().trash.entries).toEqual([]);
  });

  it("restores a folder with its contents", async () => {
    configureApi();
    await store().loadTree();
    const entry = await store().moveToTrash("Notes");
    expect(store().tree.entries.map(item => item.path)).not.toContain("Notes/child.md");

    await store().restoreFromTrash(entry!);

    expect(store().tree.entries.map(item => item.path)).toContain("Notes/child.md");
  });

  it("deletes one entry for good and empties the rest", async () => {
    configureApi();
    await store().loadTree();
    const first = await store().moveToTrash("Notes/child.md");
    const second = await store().moveToTrash("Other.md");

    expect(await store().deleteFromTrash(first!)).toBe(true);
    expect(store().trash.entries.map(item => item.id)).toEqual([second!.id]);

    expect(await store().emptyTrash()).toBe(true);
    expect(store().trash.entries).toEqual([]);
    expect(store().trash.totalBytes).toBe(0);
  });

  it("keeps the retention window the server reports", async () => {
    configureApi({ fetchTrash: vi.fn(async () => ({ entries: [trashEntry("a.md", "file", 7)], count: 1, total_bytes: 8, retention_days: 7, generated_at: new Date().toISOString() })) });
    await store().loadTrash();
    expect(store().trash.retentionDays).toBe(7);
    expect(store().trash.entries[0]!.days_remaining).toBe(7);
  });

  it("surfaces a conflict instead of swallowing it", async () => {
    configureApi({ moveToTrash: vi.fn(async () => { throw Object.assign(new Error("changed"), { status: 409, code: "file_conflict" }); }) });
    await store().loadTree();
    await expect(store().moveToTrash("Notes/child.md")).rejects.toMatchObject({ code: "file_conflict" });
    expect(store().tree.entries.map(item => item.path)).toContain("Notes/child.md");
  });
});

describe("recycle bin panel", () => {
  it("is collapsed by default and lists what was deleted", async () => {
    configureApi();
    await store().loadTree();
    await store().moveToTrash("Notes/child.md");
    render(<WorkspaceShell/>);

    const toggle = await screen.findByRole("button", { name: /Recycle bin/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(toggle);

    const panel = await screen.findByLabelText("Recycle bin");
    expect(within(panel).getByText(/kept for 30 days/i)).toBeInTheDocument();
    expect(await within(panel).findByText("child.md")).toBeInTheDocument();
    expect(within(panel).getByText(/30 day\(s\) left/i)).toBeInTheDocument();
  });

  it("restores from the panel and opens the restored note", async () => {
    configureApi();
    await store().loadTree();
    await store().moveToTrash("Notes/child.md");
    render(<WorkspaceShell/>);

    await userEvent.click(await screen.findByRole("button", { name: /Recycle bin/ }));
    const panel = await screen.findByLabelText("Recycle bin");
    await userEvent.click(await within(panel).findByRole("button", { name: "Restore" }));

    await waitFor(() => expect(store().activePath).toBe("Notes/child.md"));
    expect(await screen.findByRole("button", { name: "Notes/child.md" })).toBeInTheDocument();
  });

  it("asks before deleting one entry forever", async () => {
    configureApi();
    await store().loadTree();
    await store().moveToTrash("Notes/child.md");
    render(<WorkspaceShell/>);

    await userEvent.click(await screen.findByRole("button", { name: /Recycle bin/ }));
    const panel = await screen.findByLabelText("Recycle bin");
    await userEvent.click(await within(panel).findByRole("button", { name: "Delete forever" }));

    const dialog = await screen.findByRole("dialog", { name: "Delete forever" });
    expect(within(dialog).getByText(/cannot be restored/i)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(store().trash.entries).toHaveLength(1);

    await userEvent.click(await within(panel).findByRole("button", { name: "Delete forever" }));
    await userEvent.click(within(await screen.findByRole("dialog", { name: "Delete forever" })).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(store().trash.entries).toEqual([]));
  });

  it("empties the bin through its own confirmation", async () => {
    configureApi();
    await store().loadTree();
    await store().moveToTrash("Notes/child.md");
    await store().moveToTrash("Other.md");
    render(<WorkspaceShell/>);

    await userEvent.click(await screen.findByRole("button", { name: /Recycle bin/ }));
    const panel = await screen.findByLabelText("Recycle bin");
    await userEvent.click(within(panel).getByRole("button", { name: "Empty the recycle bin" }));

    const dialog = await screen.findByRole("dialog", { name: "Empty the recycle bin" });
    expect(within(dialog).getByText(/All 2 item\(s\)/i)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Confirm" }));

    await waitFor(() => expect(store().trash.entries).toEqual([]));
    expect(within(panel).getByText(/The recycle bin is empty/i)).toBeInTheDocument();
  });

  it("trashes a folder from the tree menu", async () => {
    configureApi();
    await store().loadTree();
    render(<WorkspaceShell/>);
    await screen.findByRole("button", { name: "Notes" });

    await useRowMenu("Notes", "Move to recycle bin");
    const dialog = await screen.findByRole("dialog", { name: "Move to recycle bin" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Confirm" }));

    await waitFor(() => expect(store().trash.entries.map(entry => entry.original_path)).toEqual(["Notes"]));
    expect(screen.queryByRole("button", { name: "Notes/child.md" })).not.toBeInTheDocument();
  });
});
