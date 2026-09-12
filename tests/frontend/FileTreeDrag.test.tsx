import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "./render";
import { FileTree } from "../../apps/web/src/components/FileTree";
import { NewFolderDialog, suggestFolderName } from "../../apps/web/src/components/NewFolderDialog";
import { configureWorkspaceApi, preferenceDefaults, useWorkspaceStore } from "../../packages/workspace/src";
import { setVaultSession } from "../../apps/web/src/api/client";
import { I18nProvider } from "../../apps/web/src/i18n";

const b64 = (value: string) => btoa(value);
const dir = (path: string) => ({ path, kind: "directory" as const, size: null, sha256: null });
const file = (path: string) => ({ path, kind: "file" as const, size: 1, sha256: "sha256:x" });

const ENTRIES = [dir("notes"), file("notes/a.md"), file("b.md")];

function setup(overrides: Partial<Parameters<typeof configureWorkspaceApi>[0]> = {}) {
  configureWorkspaceApi({
    fetchVaultFiles: async () => ({ entries: ENTRIES }),
    fetchVaultFile: async (path: string) => ({ path, content_base64: b64("body"), byte_length: 4, sha256: "sha256:x", content_type: "text/markdown" }),
    patchVaultFile: async () => { throw new Error("unused"); },
    createVaultDirectory: async ({ path }) => ({ path, sha256: null, byte_length: null, operation: "created" }),
    moveVaultFile: async ({ destinationPath }) => ({ path: destinationPath, sha256: null, byte_length: null, operation: "moved" }),
    ...overrides,
  });
}

/** A minimal DataTransfer stand-in for jsdom drag events. */
function dataTransfer() {
  const store = new Map<string, string>();
  return {
    effectAllowed: "",
    dropEffect: "",
    setData: (type: string, value: string) => { store.set(type, value); },
    getData: (type: string) => store.get(type) ?? "",
  };
}

beforeEach(() => {
  localStorage.clear();
  useWorkspaceStore.getState().resetVault();
  useWorkspaceStore.setState({ ...preferenceDefaults });
  setVaultSession("a");
});

describe("suggestFolderName", () => {
  it("avoids collisions and supports a parent", () => {
    expect(suggestFolderName([], "New folder")).toBe("New folder");
    expect(suggestFolderName(["New folder"], "New folder")).toBe("New folder 2");
    expect(suggestFolderName([], "New folder", "notes")).toBe("notes/New folder");
  });
});

describe("FileTree drag & drop", () => {
  const renderTree = (onMove = vi.fn()) => {
    render(<FileTree entries={ENTRIES} expanded={["notes"]} activePath={null} onToggle={vi.fn()} onOpen={vi.fn()} onMove={onMove} />);
    return onMove;
  };

  it("moves a file onto a folder", () => {
    const onMove = renderTree();
    const source = screen.getByRole("button", { name: /b\.md/ });
    const target = screen.getByRole("button", { name: "notes" });
    const dt = dataTransfer();
    fireEvent.dragStart(source, { dataTransfer: dt });
    fireEvent.dragOver(target, { dataTransfer: dt });
    fireEvent.drop(target, { dataTransfer: dt });
    expect(onMove).toHaveBeenCalledWith("b.md", "notes");
  });

  it("refuses to drop a folder into itself or its own child", () => {
    const onMove = renderTree();
    const folder = screen.getByRole("button", { name: "notes" });
    const child = screen.getByRole("button", { name: "notes/a.md" });
    const dt = dataTransfer();
    fireEvent.dragStart(folder, { dataTransfer: dt });
    fireEvent.dragOver(folder, { dataTransfer: dt });
    fireEvent.drop(folder, { dataTransfer: dt });
    fireEvent.dragOver(child, { dataTransfer: dt });
    fireEvent.drop(child, { dataTransfer: dt });
    expect(onMove).not.toHaveBeenCalled();
  });

  it("does nothing when the file is already in that folder", () => {
    const onMove = renderTree();
    const source = screen.getByRole("button", { name: "notes/a.md" });
    const target = screen.getByRole("button", { name: "notes" });
    const dt = dataTransfer();
    fireEvent.dragStart(source, { dataTransfer: dt });
    fireEvent.drop(target, { dataTransfer: dt });
    expect(onMove).not.toHaveBeenCalled();
  });
});

describe("FileTree attachment entry points (ATT-15)", () => {
  it("offers 'upload to this folder' on a directory and passes the real path", async () => {
    const onUploadToDirectory = vi.fn();
    render(<I18nProvider initialLocale="en-US"><FileTree entries={ENTRIES} expanded={["notes"]} activePath={null} onToggle={vi.fn()} onOpen={vi.fn()} onUploadToDirectory={onUploadToDirectory}/></I18nProvider>);
    fireEvent.contextMenu(screen.getByRole("button", { name: "notes" }));
    await userEvent.click(screen.getByRole("menuitem", { name: /upload to this folder/i }));
    expect(onUploadToDirectory).toHaveBeenCalledWith("notes");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("does not offer the upload menu on a file row", () => {
    render(<I18nProvider initialLocale="en-US"><FileTree entries={ENTRIES} expanded={["notes"]} activePath={null} onToggle={vi.fn()} onOpen={vi.fn()} onUploadToDirectory={vi.fn()}/></I18nProvider>);
    fireEvent.contextMenu(screen.getByRole("button", { name: /a\.md/ }));
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("keeps Markdown rows on openFile while attachment rows open the viewer", async () => {
    const onOpen = vi.fn();
    const onOpenAttachment = vi.fn();
    render(<I18nProvider initialLocale="en-US"><FileTree entries={[...ENTRIES, file("notes/report.pdf")]} expanded={["notes"]} activePath={null} onToggle={vi.fn()} onOpen={onOpen} onOpenAttachment={onOpenAttachment}/></I18nProvider>);
    await userEvent.click(screen.getByRole("button", { name: /a\.md/ }));
    expect(onOpen).toHaveBeenCalledWith("notes/a.md");
    await userEvent.click(screen.getByRole("button", { name: /report\.pdf/ }));
    expect(onOpenAttachment).toHaveBeenCalledWith("notes/report.pdf");
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it("disables attachment rows only when no viewer callback exists", () => {
    render(<I18nProvider initialLocale="en-US"><FileTree entries={[...ENTRIES, file("notes/report.pdf")]} expanded={["notes"]} activePath={null} onToggle={vi.fn()} onOpen={vi.fn()}/></I18nProvider>);
    expect(screen.getByRole("button", { name: /report\.pdf/ })).toBeDisabled();
  });
});

describe("NewFolderDialog", () => {
  it("creates a folder and refreshes the tree", async () => {
    const create = vi.fn(async ({ path }: { path: string }) => ({ path, sha256: null, byte_length: null, operation: "created" as const }));
    setup({ createVaultDirectory: create });
    render(<I18nProvider initialLocale="en-US"><NewFolderDialog open onClose={vi.fn()} /></I18nProvider>);
    const field = screen.getByPlaceholderText("my-folder");
    expect(field).toHaveValue("New folder");
    await userEvent.clear(field);
    await userEvent.type(field, "journal");
    await userEvent.click(screen.getByRole("button", { name: /Create folder/ }));
    await waitFor(() => expect(create).toHaveBeenCalledWith({ path: "journal" }));
    await waitFor(() => expect(useWorkspaceStore.getState().tree.expandedPaths).toContain("journal"));
  });

  it("shows the server error and stays open", async () => {
    const onClose = vi.fn();
    setup({ createVaultDirectory: async () => { throw Object.assign(new Error("Folder exists"), { status: 409, code: "already_exists" }); } });
    render(<I18nProvider initialLocale="en-US"><NewFolderDialog open onClose={onClose} /></I18nProvider>);
    await userEvent.click(screen.getByRole("button", { name: /Create folder/ }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });
});


describe("moveEntry store action", () => {
  it("sends the expected digest of the file being moved", async () => {
    const move = vi.fn(async ({ destinationPath }: { sourcePath: string; destinationPath: string }) => ({ path: destinationPath, sha256: null, byte_length: null, operation: "moved" as const }));
    setup({ moveVaultFile: move });
    await useWorkspaceStore.getState().loadTree();
    const moved = await useWorkspaceStore.getState().moveEntry("b.md", "notes");
    expect(moved).toBe("notes/b.md");
    expect(move).toHaveBeenCalledWith({ sourcePath: "b.md", destinationPath: "notes/b.md", expectedSha256: "sha256:x" });
  });

  it("re-points an open tab to the new path", async () => {
    const move = vi.fn(async ({ destinationPath }: { destinationPath: string }) => ({ path: destinationPath, sha256: null, byte_length: null, operation: "moved" as const }));
    setup({ moveVaultFile: move });
    await useWorkspaceStore.getState().loadTree();
    await useWorkspaceStore.getState().openFile("b.md");
    expect(useWorkspaceStore.getState().activePath).toBe("b.md");
    await useWorkspaceStore.getState().moveEntry("b.md", "notes");
    const state = useWorkspaceStore.getState();
    expect(state.activePath).toBe("notes/b.md");
    expect(state.sessions["notes/b.md"]).toBeDefined();
    expect(state.sessions["b.md"]).toBeUndefined();
  });
});
