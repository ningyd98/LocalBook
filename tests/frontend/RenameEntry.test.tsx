/**
 * Rename matrix: the tree's inline rename UI and the store action behind it.
 * A rename is a same-directory move, so it must honour the same no-overwrite
 * contract (expected sha256) and keep open tabs/sessions pointing at the file.
 */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "./render";
import { FileTree } from "../../apps/web/src/components/FileTree";
import { I18nProvider } from "../../apps/web/src/i18n";
import { configureWorkspaceApi, useWorkspaceStore } from "../../packages/workspace/src";

const b64 = (value: string) => btoa(value);
const dir = (path: string) => ({ path, kind: "directory" as const, size: null, sha256: null });
const file = (path: string) => ({ path, kind: "file" as const, size: 1, sha256: "sha256:x" });
const ENTRIES = [dir("notes"), file("notes/a.md"), file("b.md")];

function setup(overrides: Record<string, unknown> = {}) {
  configureWorkspaceApi({
    fetchVaultFiles: async () => ({ entries: ENTRIES }),
    fetchVaultFile: async (path: string) => ({ path, content_base64: b64("body"), byte_length: 4, sha256: "sha256:x", content_type: "text/markdown" }),
    patchVaultFile: async () => { throw new Error("unused"); },
    moveVaultFile: async ({ destinationPath }: { destinationPath: string }) => ({ path: destinationPath, sha256: null, byte_length: null, operation: "moved" as const }),
    ...overrides,
  });
}

beforeEach(() => {
  localStorage.clear();
  useWorkspaceStore.getState().resetVault();
});

describe("renameEntry store action", () => {
  it("renames in place and keeps the directory, confirming the on-disk digest", async () => {
    const move = vi.fn(async ({ destinationPath }: { destinationPath: string }) => ({ path: destinationPath, sha256: null, byte_length: null, operation: "moved" as const }));
    setup({ moveVaultFile: move });
    await useWorkspaceStore.getState().loadTree();
    await useWorkspaceStore.getState().openFile("notes/a.md");

    const renamed = await useWorkspaceStore.getState().renameEntry("notes/a.md", "renamed.md");

    expect(renamed).toBe("notes/renamed.md");
    expect(move).toHaveBeenCalledWith({ sourcePath: "notes/a.md", destinationPath: "notes/renamed.md", expectedSha256: "sha256:x" });
    // The digest is read from disk (not the session base), so unsaved edits do
    // not turn a rename into a 409 conflict.
    // The open tab and its session follow the file so edits are not lost.
    const state = useWorkspaceStore.getState();
    expect(state.activePath).toBe("notes/renamed.md");
    expect(state.sessions["notes/renamed.md"]).toBeDefined();
    expect(state.sessions["notes/a.md"]).toBeUndefined();
    expect(state.tabs.some(tab => tab.path === "notes/renamed.md" && tab.title === "renamed.md")).toBe(true);
  });

  it("is a no-op when the name did not change", async () => {
    const move = vi.fn();
    setup({ moveVaultFile: move });
    await useWorkspaceStore.getState().loadTree();
    await expect(useWorkspaceStore.getState().renameEntry("b.md", "b.md")).resolves.toBe("b.md");
    expect(move).not.toHaveBeenCalled();
  });

  it.each(["", "   ", "a/b.md", "a\\b.md", ".", ".."])("rejects the unsafe name %j without calling the API", async (name) => {
    const move = vi.fn();
    setup({ moveVaultFile: move });
    await useWorkspaceStore.getState().loadTree();
    await expect(useWorkspaceStore.getState().renameEntry("b.md", name)).rejects.toMatchObject({ code: "invalid_name" });
    expect(move).not.toHaveBeenCalled();
  });

  it("surfaces a 409 conflict instead of overwriting the target", async () => {
    setup({
      moveVaultFile: async () => { throw Object.assign(new Error("exists"), { status: 409, code: "already_exists" }); },
    });
    await useWorkspaceStore.getState().loadTree();
    await expect(useWorkspaceStore.getState().renameEntry("b.md", "a.md")).rejects.toMatchObject({ code: "already_exists" });
    // The old path is still the open one: nothing moved on the client either.
    expect(useWorkspaceStore.getState().sessions["b.md"]).toBeUndefined();
  });

  it("reads the digest when the file is not open", async () => {
    const move = vi.fn(async ({ destinationPath }: { destinationPath: string }) => ({ path: destinationPath, sha256: null, byte_length: null, operation: "moved" as const }));
    const fetchFile = vi.fn(async (path: string) => ({ path, content_base64: b64("body"), byte_length: 4, sha256: "sha256:fresh", content_type: "text/markdown" }));
    setup({ moveVaultFile: move, fetchVaultFile: fetchFile });
    await useWorkspaceStore.getState().loadTree();
    await useWorkspaceStore.getState().renameEntry("b.md", "c.md");
    expect(fetchFile).toHaveBeenCalledWith("b.md");
    expect(move).toHaveBeenCalledWith({ sourcePath: "b.md", destinationPath: "c.md", expectedSha256: "sha256:fresh" });
  });
});

describe("FileTree inline rename", () => {
  function renderTree(onRename = vi.fn(async (_path: string, newName: string) => newName)) {
    render(<I18nProvider><FileTree entries={ENTRIES} expanded={["notes"]} activePath="b.md" onToggle={vi.fn()} onOpen={vi.fn()} onRename={onRename}/></I18nProvider>);
    return onRename;
  }

  it("renames via the context menu and Enter", async () => {
    const onRename = renderTree();
    const row = screen.getByTitle("b.md");
    await userEvent.pointer({ target: row, keys: "[MouseRight]" });
    await userEvent.click(await screen.findByRole("menuitem", { name: "重命名" }));

    const input = screen.getByRole("textbox", { name: "重命名 b.md" });
    expect(input).toHaveValue("b.md");
    await userEvent.clear(input);
    await userEvent.type(input, "renamed.md{Enter}");
    await waitFor(() => expect(onRename).toHaveBeenCalledWith("b.md", "renamed.md"));
    await waitFor(() => expect(screen.queryByRole("textbox", { name: "重命名 b.md" })).not.toBeInTheDocument());
  });

  it("renames on double click and cancels with Escape", async () => {
    const onRename = renderTree();
    await userEvent.dblClick(screen.getByTitle("notes/a.md"));
    const input = screen.getByRole("textbox", { name: "重命名 notes/a.md" });
    await userEvent.type(input, "x{Escape}");
    expect(onRename).not.toHaveBeenCalled();
    expect(screen.queryByRole("textbox", { name: "重命名 notes/a.md" })).not.toBeInTheDocument();
  });

  it("keeps the input open and shows an error when the API rejects the name", async () => {
    const onRename = vi.fn(async () => { throw Object.assign(new Error("exists"), { status: 409, code: "already_exists" }); });
    renderTree(onRename);
    await userEvent.dblClick(screen.getByTitle("b.md"));
    const input = screen.getByRole("textbox", { name: "重命名 b.md" });
    await userEvent.clear(input);
    await userEvent.type(input, "notes{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("同名文件已存在");
    expect(screen.getByRole("textbox", { name: "重命名 b.md" })).toBeInTheDocument();
  });

  it("rejects a name containing a path separator without calling the API", async () => {
    const onRename = renderTree();
    await userEvent.dblClick(screen.getByTitle("b.md"));
    const input = screen.getByRole("textbox", { name: "重命名 b.md" });
    await userEvent.clear(input);
    await userEvent.type(input, "nested/b.md{Enter}");
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(onRename).not.toHaveBeenCalled();
  });

  it("offers no rename entry for directories (the API cannot move them)", async () => {
    renderTree();
    await userEvent.pointer({ target: screen.getByTitle("notes"), keys: "[MouseRight]" });
    // The folder menu keeps only the upload entry.
    expect(screen.queryByRole("menuitem", { name: "重命名" })).not.toBeInTheDocument();
  });
});
