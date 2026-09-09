/**
 * Nested-note creation from a `[[wikilink]]` (Obsidian-style).
 *
 * A missing link becomes a create-able anchor in the preview and a "创建"
 * button in the links panel; both call the same store action, which creates
 * the note next to the source note (auto-creating authored sub-folders) or
 * opens the note that already resolves by basename.
 */
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "./render";
import { Preview } from "../../packages/markdown/src/Preview";
import { preprocessWikilinks, wikilinkCreatePath, parseWikilink } from "../../packages/protocol/src";
import { I18nProvider } from "../../apps/web/src/i18n";
import { LinkItem } from "../../apps/web/src/components/LinkItem";
import { configureWorkspaceApi, useWorkspaceStore } from "../../packages/workspace/src";

const b64 = (value: string) => btoa(value);
const file = (path: string) => ({ path, kind: "file" as const, size: 1, sha256: "sha256:x" });
const dir = (path: string) => ({ path, kind: "directory" as const, size: null, sha256: null });

function setup(overrides: Record<string, unknown> = {}) {
  const created: string[] = [];
  const folders: string[] = [];
  configureWorkspaceApi({
    fetchVaultFiles: async () => ({ entries: [file("notes/a.md"), dir("notes")] }),
    fetchVaultFile: async (path: string) => ({ path, content_base64: b64("body"), byte_length: 4, sha256: "sha256:x", content_type: "text/markdown" }),
    patchVaultFile: async () => ({ path: "notes/a.md", sha256: "sha256:new", byte_length: 0, operation: "updated" as const }),
    createVaultFile: async ({ path }: { path: string }) => { created.push(path); return { path, sha256: "sha256:new", byte_length: 0, operation: "created" as const }; },
    createVaultDirectory: async ({ path }: { path: string }) => { folders.push(path); return { path, sha256: null, byte_length: null, operation: "created" as const }; },
    ...overrides,
  });
  return { created, folders };
}

beforeEach(() => {
  localStorage.clear();
  useWorkspaceStore.getState().resetVault();
});

describe("wikilink parsing helpers", () => {
  it("splits target, alias, section, block and embed", () => {
    expect(parseWikilink("[[Note]]")).toMatchObject({ target: "Note", label: "Note", embed: false });
    expect(parseWikilink("[[Note|Alias]]")).toMatchObject({ target: "Note", label: "Alias" });
    expect(parseWikilink("[[Note#Head]]")).toMatchObject({ target: "Note", section: "Head" });
    expect(parseWikilink("[[Note^blk]]")).toMatchObject({ target: "Note", block: "blk" });
    expect(parseWikilink("![[img.png]]")).toMatchObject({ target: "img.png", embed: true });
    expect(parseWikilink("[[https://x.dev]]")).toMatchObject({ web: true });
  });

  it("computes the creation path relative to the source note", () => {
    expect(wikilinkCreatePath("notes/a.md", "Child")).toBe("notes/Child.md");
    expect(wikilinkCreatePath("a.md", "Child")).toBe("Child.md");
    expect(wikilinkCreatePath("notes/a.md", "sub/Child")).toBe("notes/sub/Child.md");
    expect(wikilinkCreatePath("notes/a.md", "Child.md")).toBe("notes/Child.md");
    expect(wikilinkCreatePath("notes/a.md", "../escape")).toBeNull();
    expect(wikilinkCreatePath("notes/a.md", "https://x.dev")).toBeNull();
    expect(wikilinkCreatePath("notes/a.md", "")).toBeNull();
  });

  it("rewrites links but never touches code spans or fenced blocks", () => {
    const out = preprocessWikilinks("a [[New Note]] b `[[code]]` c\n```\n[[fenced]]\n```\nd");
    expect(out).toContain("[New Note](wikilink:New%20Note)");
    expect(out).toContain("`[[code]]`");
    expect(out).toContain("[[fenced]]");
    // Embeds and web links keep their authored form.
    expect(preprocessWikilinks("![[img.png]]")).toBe("![[img.png]]");
    expect(preprocessWikilinks("[[https://x.dev]]")).toBe("[[https://x.dev]]");
  });
});

describe("openOrCreateLinkedNote", () => {
  it("creates the note in the source note's directory and opens it", async () => {
    const { created } = setup();
    await useWorkspaceStore.getState().loadTree();
    await useWorkspaceStore.getState().openFile("notes/a.md");

    const path = await useWorkspaceStore.getState().openOrCreateLinkedNote("notes/a.md", "Child");

    expect(path).toBe("notes/Child.md");
    expect(created).toEqual(["notes/Child.md"]);
    expect(useWorkspaceStore.getState().activePath).toBe("notes/Child.md");
  });

  it("creates missing sub-folders for an authored nested target", async () => {
    const { created, folders } = setup();
    await useWorkspaceStore.getState().loadTree();
    await useWorkspaceStore.getState().openFile("notes/a.md");

    const path = await useWorkspaceStore.getState().openOrCreateLinkedNote("notes/a.md", "deep/nested/Child");

    expect(path).toBe("notes/deep/nested/Child.md");
    expect(folders).toEqual(["notes/deep", "notes/deep/nested"]);
    expect(created).toEqual(["notes/deep/nested/Child.md"]);
  });

  it("opens an existing note that already resolves by basename", async () => {
    const { created } = setup({ fetchVaultFiles: async () => ({ entries: [file("archive/Existing.md"), file("notes/a.md")] }) });
    await useWorkspaceStore.getState().loadTree();
    await useWorkspaceStore.getState().openFile("notes/a.md");

    const path = await useWorkspaceStore.getState().openOrCreateLinkedNote("notes/a.md", "Existing");

    expect(path).toBe("archive/Existing.md");
    expect(created).toEqual([]);
    expect(useWorkspaceStore.getState().activePath).toBe("archive/Existing.md");
  });

  it("rejects an unsafe target without creating anything", async () => {
    const { created } = setup();
    await useWorkspaceStore.getState().loadTree();
    await useWorkspaceStore.getState().openFile("notes/a.md");

    await expect(useWorkspaceStore.getState().openOrCreateLinkedNote("notes/a.md", "../escape")).rejects.toMatchObject({ code: "invalid_name" });
    await expect(useWorkspaceStore.getState().openOrCreateLinkedNote("notes/a.md", "https://x.dev")).rejects.toMatchObject({ code: "invalid_name" });
    expect(created).toEqual([]);
  });
});

describe("preview wikilink interaction", () => {
  it("marks a missing target and reports the click", async () => {
    const onOpenWikilink = vi.fn();
    render(<Preview source={"see [[Missing Note]]"} wikilinkExists={() => false} onOpenWikilink={onOpenWikilink} />);
    const anchor = screen.getByText("Missing Note");
    expect(anchor).toHaveClass("wikilink", "wikilink-missing");
    expect(anchor).toHaveAttribute("data-wikilink", "Missing Note");
    await userEvent.click(anchor);
    expect(onOpenWikilink).toHaveBeenCalledWith("Missing Note");
  });

  it("does not mark an existing target and never navigates the page", async () => {
    const onOpenWikilink = vi.fn();
    render(<Preview source={"see [[Existing]]"} wikilinkExists={() => true} onOpenWikilink={onOpenWikilink} />);
    const anchor = screen.getByText("Existing");
    expect(anchor).not.toHaveClass("wikilink-missing");
    expect(anchor.getAttribute("href")).toMatch(/^#wikilink-/);
    await userEvent.click(anchor);
    expect(onOpenWikilink).toHaveBeenCalledWith("Existing");
  });

  it("keeps code spans literal and unclickable", async () => {
    const onOpenWikilink = vi.fn();
    render(<Preview source={"`[[code]]`"} wikilinkExists={() => false} onOpenWikilink={onOpenWikilink} />);
    await userEvent.click(screen.getByText("[[code]]"));
    expect(onOpenWikilink).not.toHaveBeenCalled();
  });
});

describe("links panel create button", () => {
  const broken = { raw: "[[Missing]]", target: "Missing", kind: "wikilink" as const, display: null, section: null, block: null, resolved_path: null, broken: true, ambiguous: false, candidates: [] };

  it("offers create for a broken link and calls back with the target", async () => {
    const onCreate = vi.fn();
    render(<I18nProvider><ul><LinkItem link={broken} onOpen={vi.fn()} onCreate={onCreate}/></ul></I18nProvider>);
    await userEvent.click(screen.getByRole("button", { name: "创建" }));
    expect(onCreate).toHaveBeenCalledWith("Missing");
  });

  it("shows no create button when the caller cannot create", () => {
    render(<I18nProvider><ul><LinkItem link={broken} onOpen={vi.fn()}/></ul></I18nProvider>);
    expect(screen.queryByRole("button", { name: "创建" })).not.toBeInTheDocument();
  });
});
