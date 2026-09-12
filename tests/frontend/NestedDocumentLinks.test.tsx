/**
 * Nested documents must be *linked*, not only nested on disk:
 *
 * - creating a child/sibling document adds a `[[wikilink]]` to the note it was
 *   created from (and never touches a note that is closed or has unsaved edits);
 * - renaming that document re-points the parent's link instead of leaving a
 *   dangling `[[old name]]`;
 * - the source rewriter itself only touches real wikilinks (never code) and
 *   preserves aliases/sections/embeds.
 */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "./render";
import { WorkspaceShell } from "../../apps/web/src/components/WorkspaceShell";
import type { FileMutationResponse, VaultFileEntry } from "../../packages/protocol/src";
import { configureWorkspaceApi, rewriteWikilinkTarget, useWorkspaceStore, wikilinkSpans } from "../../packages/workspace/src";
import type { WorkspaceApi } from "../../packages/workspace/src";

const b64 = (value: string) => btoa(value);
const dir = (path: string): VaultFileEntry => ({ path, kind: "directory", size: null, sha256: null });
const file = (path: string): VaultFileEntry => ({ path, kind: "file", size: 4, sha256: `sha256:${path}` });
const store = () => useWorkspaceStore.getState();

const PARENT = "# Notes\n\nA paragraph with `[[code.md]]` in inline code.\n";
const ENTRIES: VaultFileEntry[] = [file("Notes.md"), file("Other.md")];
const bodies = new Map<string, string>([["Notes.md", PARENT]]);

function configureApi(overrides: Partial<WorkspaceApi> = {}) {
  const created: string[] = [];
  const folders: string[] = [];
  const moves: Array<{ sourcePath: string; destinationPath: string }> = [];
  configureWorkspaceApi({
    fetchVaultFiles: vi.fn(async () => ({ entries: [...ENTRIES, ...created.map(file), ...folders.map(dir)] })),
    fetchVaultFile: vi.fn(async (path: string) => ({ path, content_base64: b64(bodies.get(path) ?? "body"), byte_length: 4, sha256: `sha256:${path}`, content_type: "text/markdown" })),
    patchVaultFile: vi.fn(async ({ path, contentBase64 }: { path: string; contentBase64: string }) => {
      bodies.set(path, atob(contentBase64));
      return { path, sha256: `sha256:${path}:saved`, byte_length: 4, operation: "updated" as const } satisfies FileMutationResponse;
    }),
    createVaultDirectory: vi.fn(async ({ path }: { path: string }) => { folders.push(path); return { path, sha256: null, byte_length: null, operation: "created" as const }; }),
    createVaultFile: vi.fn(async ({ path, contentBase64 }: { path: string; contentBase64: string }) => {
      bodies.set(path, atob(contentBase64));
      created.push(path);
      return { path, sha256: null, byte_length: 0, operation: "created" as const };
    }),
    moveVaultFile: vi.fn(async ({ sourcePath, destinationPath }: { sourcePath: string; destinationPath: string }) => {
      moves.push({ sourcePath, destinationPath });
      const body = bodies.get(sourcePath);
      if (body !== undefined) { bodies.delete(sourcePath); bodies.set(destinationPath, body); }
      return { path: destinationPath, sha256: null, byte_length: null, operation: "moved" as const };
    }),
    fetchLinks: vi.fn(async (path: string) => ({ path, outgoing: [], broken_count: 0 })),
    fetchBacklinks: vi.fn(async (path: string) => ({ path, backlinks: [] })),
    ...overrides,
  });
  return { created, folders, moves };
}

beforeEach(() => {
  localStorage.clear();
  store().resetVault();
  bodies.clear();
  bodies.set("Notes.md", PARENT);
});

describe("linkOwnedDocument (store)", () => {
  it("links a new child document from the open parent and saves it", async () => {
    configureApi();
    await store().openFile("Notes.md");
    await store().createChildNote("Notes.md", { name: "child" });

    const content = store().sessions["Notes.md"]!.content;
    expect(content).toContain("[[child]]");
    // Appended as its own paragraph, and the note is queued for normal saving.
    expect(content.trimEnd().endsWith("[[child]]")).toBe(true);
    await waitFor(() => expect(store().sessions["Notes.md"]!.dirty).toBe(false));
    expect(bodies.get("Notes.md")).toContain("[[child]]");
  });

  it("leaves a closed parent untouched", async () => {
    configureApi();
    await store().loadTree();
    await store().createChildNote("Notes.md", { name: "child" });

    expect(store().sessions["Notes.md"]).toBeUndefined();
    expect(bodies.get("Notes.md")).toBe(PARENT);
  });

  it("never overwrites a parent with unsaved edits", async () => {
    configureApi();
    await store().openFile("Notes.md");
    store().updateContent("Notes.md", `${PARENT}\nmy unsaved paragraph\n`);

    await store().createChildNote("Notes.md", { name: "child" });

    expect(store().sessions["Notes.md"]!.content).toBe(`${PARENT}\nmy unsaved paragraph\n`);
    expect(bodies.get("Notes.md")).toBe(PARENT);
  });

  it("does not duplicate a link that is already there", async () => {
    configureApi();
    bodies.set("Notes.md", `${PARENT}\n[[child]]\n`);
    await store().openFile("Notes.md");
    await store().createChildNote("Notes.md", { name: "child" });

    expect(store().sessions["Notes.md"]!.content.match(/\[\[child\]\]/g)).toHaveLength(1);
  });

  it("can be switched off for callers that write their own link", async () => {
    configureApi();
    await store().openFile("Notes.md");
    await store().createChildNote("Notes.md", { name: "child", linkFromOwner: false });
    expect(store().sessions["Notes.md"]!.content).not.toContain("[[child]]");
  });
});

describe("rename keeps the parent link pointing at the document", () => {
  it("rewrites the parent's link to the new name", async () => {
    const { moves } = configureApi();
    await store().openFile("Notes.md");
    await store().createChildNote("Notes.md", { name: "child" });
    await waitFor(() => expect(store().sessions["Notes.md"]!.dirty).toBe(false));

    await store().renameEntry("Notes/child.md", "renamed.md");

    expect(moves).toEqual([{ sourcePath: "Notes/child.md", destinationPath: "Notes/renamed.md" }]);
    expect(store().sessions["Notes.md"]!.content).toContain("[[renamed]]");
    expect(store().sessions["Notes.md"]!.content).not.toContain("[[child]]");
    await waitFor(() => expect(bodies.get("Notes.md")).toContain("[[renamed]]"));
  });
});

describe("rewriteWikilinkTarget", () => {
  it("preserves alias, section, block and embed", () => {
    const source = "[[Old|x]] and [[Old#Section]] and [[Old^blk]] and ![[Old]]";
    expect(rewriteWikilinkTarget(source, "Old", "New", false)).toBe("[[New|x]] and [[New#Section]] and [[New^blk]] and ![[New]]");
  });

  it("matches by stem and ignores other targets, web links and code", () => {
    const source = [
      "[[Other]]",
      "[[folder/Old]]",
      "[a](Old)",
      "[b](https://example.com/Old)",
      "`[[Old]]`",
      "```md",
      "[[Old]]",
      "```",
      "[[Old]]",
    ].join("\n");
    const expected = [
      "[[Other]]",
      "[[folder/New]]",
      "[a](Old)",
      "[b](https://example.com/Old)",
      "`[[Old]]`",
      "```md",
      "[[Old]]",
      "```",
      "[[New]]",
    ].join("\n");
    expect(rewriteWikilinkTarget(source, "Old", "New", false)).toBe(expected);
  });

  it("leaves an ambiguous stem alone", () => {
    expect(rewriteWikilinkTarget("[[Old]]", "Old", "New", true)).toBe("[[Old]]");
  });

  it("returns the input when nothing matches", () => {
    expect(rewriteWikilinkTarget("no links here", "Old", "New", false)).toBe("no links here");
  });

  it("locates spans outside code", () => {
    expect(wikilinkSpans("a [[One]] `[[Two]]`\n```\n[[Three]]\n```\n[[Four]]").map(span => span.body)).toEqual(["[[One]]", "[[Four]]"]);
  });
});

describe("editor surface", () => {
  it("links the child from the note the user right-clicked", async () => {
    configureApi();
    await store().openFile("Notes.md");
    render(<WorkspaceShell />);
    await screen.findByRole("textbox", { name: "Source editor for Notes.md" });

    // The tree rows only exist after the listing loads.
    const row = await screen.findByRole("button", { name: "Notes.md" });
    await userEvent.pointer({ target: row, keys: "[MouseRight]" });
    await screen.findByRole("menu", { name: "File actions" });
    await userEvent.click(await screen.findByRole("menuitem", { name: "New nested document" }));

    const childPath = "Notes/Untitled document.md";
    await waitFor(() => expect(store().sessions["Notes.md"]!.content).toContain(`[[${childPath.split("/").at(-1)!.replace(/\.md$/, "")}]]`));
    expect(store().activePath).toBe(childPath);
  });
});
