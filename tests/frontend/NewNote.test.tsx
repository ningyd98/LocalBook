import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "./render";
import { NewNoteDialog, suggestName } from "../../apps/web/src/components/NewNoteDialog";
import { configureWorkspaceApi, preferenceDefaults, useWorkspaceStore } from "../../packages/workspace/src";
import { setVaultSession } from "../../apps/web/src/api/client";
import { I18nProvider } from "../../apps/web/src/i18n";

const b64 = (value: string) => btoa(value);
const entry = (path: string) => ({ path, kind: "file" as const, size: 1, sha256: "sha256:x" });

function setup(create = vi.fn(async ({ path }: { path: string }) => ({ path, sha256: "sha256:new", byte_length: 0, operation: "created" as const }))) {
  configureWorkspaceApi({
    fetchVaultFiles: async () => ({ entries: [entry("existing.md")] }),
    fetchVaultFile: async (path: string) => ({ path, content_base64: b64(""), byte_length: 0, sha256: "sha256:x", content_type: "text/markdown" }),
    patchVaultFile: async () => { throw new Error("unused"); },
    createVaultFile: create,
  });
  return create;
}

beforeEach(() => {
  localStorage.clear();
  useWorkspaceStore.getState().resetVault();
  useWorkspaceStore.setState({ ...preferenceDefaults });
  setVaultSession("a");
});

describe("suggestName", () => {
  it("avoids collisions", () => {
    expect(suggestName([], "Untitled note")).toBe("Untitled note.md");
    expect(suggestName(["Untitled note.md"], "Untitled note")).toBe("Untitled note 2.md");
    expect(suggestName(["Untitled note.md", "Untitled note 2.md"], "Untitled note")).toBe("Untitled note 3.md");
  });
});

describe("NewNoteDialog", () => {
  it("prefills a free name, creates the note and opens it", async () => {
    const create = setup();
    render(<I18nProvider initialLocale="en-US"><NewNoteDialog open onClose={vi.fn()} /></I18nProvider>);
    const field = screen.getByPlaceholderText("my-note.md");
    expect(field).toHaveValue("Untitled note.md");
    await userEvent.clear(field);
    await userEvent.type(field, "my idea");
    await userEvent.click(screen.getByRole("button", { name: /Create & open/ }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    // ".md" is appended when the user omits an extension.
    expect(create.mock.calls[0]![0]).toEqual({ path: "my idea.md", contentBase64: b64("") });
    await waitFor(() => expect(useWorkspaceStore.getState().activePath).toBe("my idea.md"));
  });

  it("keeps an explicit extension and allows subfolders", async () => {
    const create = setup();
    render(<I18nProvider initialLocale="en-US"><NewNoteDialog open onClose={vi.fn()} /></I18nProvider>);
    const field = screen.getByPlaceholderText("my-note.md");
    await userEvent.clear(field);
    await userEvent.type(field, "notes/today.markdown");
    await userEvent.click(screen.getByRole("button", { name: /Create & open/ }));
    await waitFor(() => expect(create).toHaveBeenCalled());
    expect(create.mock.calls[0]![0].path).toBe("notes/today.markdown");
  });

  it("shows the server error and stays open when creation fails", async () => {
    const onClose = vi.fn();
    const create = vi.fn(async () => { throw Object.assign(new Error("File already exists"), { status: 409, code: "file_exists" }); });
    setup(create);
    render(<I18nProvider initialLocale="en-US"><NewNoteDialog open onClose={onClose} /></I18nProvider>);
    await userEvent.click(screen.getByRole("button", { name: /Create & open/ }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });
});
