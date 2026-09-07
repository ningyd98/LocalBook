/**
 * M3 NotesLinks panel matrix — presentational component fed by store state;
 * no fetch/backend/network.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { NotesLinksPanel } from "../../apps/web/src/components/NotesLinksPanel";
import type { NoteRelationsState } from "../../packages/workspace/src";

const ready: NoteRelationsState = {
  status: "ready",
  path: "notes/a.md",
  outgoing: [
    { target: "Ref A", raw: "[[Ref A]]", kind: "wikilink", display: null, section: null, block: null, resolved_path: "notes/Ref A.md", broken: false, ambiguous: false, candidates: [] },
    { target: "Cfg B", raw: "[[Cfg B]]", kind: "wikilink", display: null, section: null, block: null, resolved_path: null, broken: true, ambiguous: false, candidates: [] },
    { target: "Alpha", raw: "[[Alpha]]", kind: "wikilink", display: null, section: null, block: null, resolved_path: "notes/Alpha.md", broken: false, ambiguous: true, candidates: ["notes/Alpha.md", "dup/Alpha.md"] },
    { target: "image.png", raw: "![[image.png]]", kind: "embed", display: null, section: null, block: null, resolved_path: "attachments/image.png", broken: false, ambiguous: false, candidates: [] },
    { target: "https://example.com", raw: "[[https://example.com]]", kind: "web", display: null, section: null, block: null, resolved_path: null, broken: false, ambiguous: false, candidates: [] },
  ],
  backlinks: [{ source_path: "notes/zeta.md", title: "Zeta", text: "see [[a]]" }],
  brokenCount: 1,
  error: null,
};

describe("NotesLinksPanel", () => {
  it("renders outgoing and backlinks; resolved targets open the note", async () => {
    const onOpen = vi.fn();
    render(<NotesLinksPanel relations={ready} onOpen={onOpen} onRetry={() => undefined} />);

    expect(screen.getByText("notes/a.md")).toBeInTheDocument();
    // resolved markdown link is clickable and opens the target note
    await userEvent.click(screen.getByRole("button", { name: "Ref A" }));
    expect(onOpen).toHaveBeenCalledWith("notes/Ref A.md");
    // broken + ambiguous are marked, not clickable as resolved
    expect(screen.getByText("broken")).toBeInTheDocument();
    expect(screen.getByText("ambiguous")).toBeInTheDocument();
    expect(screen.getByText("1 broken")).toBeInTheDocument();
    // embed target resolves to an attachment: not editable ⇒ static text
    expect(screen.queryByRole("button", { name: "image.png" })).not.toBeInTheDocument();
    // web link is an external anchor, never a note open
    const web = screen.getByRole("link", { name: /https:\/\/example\.com/ });
    expect(web.getAttribute("href")).toBe("https://example.com");
    // backlink source opens the note
    await userEvent.click(screen.getByRole("button", { name: "Zeta" }));
    expect(onOpen).toHaveBeenCalledWith("notes/zeta.md");
  });

  it("renders nothing when no note is active", () => {
    const empty: NoteRelationsState = { status: "idle", path: null, outgoing: null, backlinks: null, brokenCount: 0, error: null };
    const { container } = render(<NotesLinksPanel relations={empty} onOpen={() => undefined} onRetry={() => undefined} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows loading/empty/error states and retries", async () => {
    const onRetry = vi.fn();
    const errorState: NoteRelationsState = { status: "error", path: "notes/a.md", outgoing: null, backlinks: null, brokenCount: 0, error: { kind: "api", status: 503, code: "index_unavailable", message: "Derived index is unavailable", path: "notes/a.md" } };
    render(<NotesLinksPanel relations={errorState} onOpen={() => undefined} onRetry={onRetry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Derived index is unavailable");
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalled();
  });
});
