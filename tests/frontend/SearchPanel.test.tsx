/**
 * M3 search UI matrix — fetch/store API is mocked; no real backend/network.
 * Results are rendered as text (never as HTML) and keyword highlights use
 * <mark>, so a malicious snippet cannot inject markup.
 */
import { screen, waitFor } from "@testing-library/react";
import { render } from "./render";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SearchPanel } from "../../apps/web/src/components/SearchPanel";
import type { FileMutationResponse, FileReadResponse, SearchResponse } from "../../packages/protocol/src";
import { configureWorkspaceApi, useWorkspaceStore } from "../../packages/workspace/src";
import type { WorkspaceApi } from "../../packages/workspace/src";

const response: SearchResponse = {
  query: "你好",
  hits: [
    { path: "notes/你好 note.md", title: "你好 note", snippet: "…你好 世界…", matched_terms: ["你好"], score: 3 },
    { path: "docs/a.md", title: "Alpha", snippet: "text with <img src=x onerror=alert(1)> 你好", matched_terms: ["你好"], score: 1 },
  ],
  total: 2,
  degraded: false,
  skipped_notes: 0,
  generated_at: "2026-01-01T00:00:00Z",
};

function resetStore() {
  useWorkspaceStore.setState({
    tree: { entries: [], expandedPaths: [], collapsedPaths: [], status: "idle", error: null },
    tabs: [], activePath: null, sessions: {},
    relations: { status: "idle", path: null, outgoing: null, backlinks: null, brokenCount: 0, error: null },
    search: { status: "idle", query: "", response: null, error: null },
    theme: "light", splitRatio: 50,
  });
}

function configureApi(overrides: Partial<WorkspaceApi> = {}) {
  configureWorkspaceApi({
    fetchVaultFiles: vi.fn<WorkspaceApi["fetchVaultFiles"]>(async () => ({ entries: [] })),
    fetchVaultFile: vi.fn<WorkspaceApi["fetchVaultFile"]>(async (): Promise<FileReadResponse> => ({ path: "x.md", content_base64: "I3g=", byte_length: 3, sha256: "sha256:x", content_type: "text/markdown" })),
    patchVaultFile: vi.fn<WorkspaceApi["patchVaultFile"]>(async (): Promise<FileMutationResponse> => ({ path: "x.md", sha256: "sha256:x", byte_length: 3, operation: "updated" })),
    ...overrides,
  });
}

beforeEach(() => {
  vi.restoreAllMocks();
  resetStore();
});

describe("SearchPanel", () => {
  it("runs a query and renders results with text highlights", async () => {
    const search = vi.fn<(query: string) => Promise<SearchResponse>>(async () => response);
    configureApi({ searchNotes: search });
    const onOpen = vi.fn();
    render(<SearchPanel onOpen={onOpen} onClose={() => undefined} />);

    await userEvent.type(screen.getByLabelText("Search query"), "你好");
    await userEvent.click(screen.getByRole("button", { name: "Search" }));

    await waitFor(() => expect(screen.getByLabelText("Search results")).toBeInTheDocument());
    expect(search).toHaveBeenCalledWith("你好");
    expect(screen.getByRole("button", { name: "你好 note" })).toBeInTheDocument();
    // snippet text is rendered (possibly split across highlight marks) and
    // the matched term is marked, never injected as HTML
    const snippets = Array.from(document.querySelectorAll(".search-result-snippet"));
    expect(snippets.some((node) => node.textContent?.includes("你好 世界"))).toBe(true);
    expect(document.querySelector("mark")?.textContent).toBe("你好");
    expect(document.querySelectorAll("img")).toHaveLength(0);

    await userEvent.click(screen.getByRole("button", { name: "你好 note" }));
    expect(onOpen).toHaveBeenCalledWith("notes/你好 note.md");
  });

  it("keeps the Search button disabled for blank queries and shows the idle hint", async () => {
    const search = vi.fn<(query: string) => Promise<SearchResponse>>();
    configureApi({ searchNotes: search });
    render(<SearchPanel onOpen={() => undefined} onClose={() => undefined} />);
    expect(screen.getByText(/Type keywords to search/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Search" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Search query"), "   ");
    expect(screen.getByRole("button", { name: "Search" })).toBeDisabled();
    expect(search).not.toHaveBeenCalled();
  });

  it("renders a safe error message when the API fails", async () => {
    configureApi({
      searchNotes: vi.fn(async () => {
        throw Object.assign(new Error("Search failed"), { status: 503, code: "index_unavailable" });
      }),
    });
    render(<SearchPanel onOpen={() => undefined} onClose={() => undefined} />);
    await userEvent.type(screen.getByLabelText("Search query"), "hello");
    await userEvent.click(screen.getByRole("button", { name: "Search" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Search failed"));
  });

  it("shows degraded and empty states", async () => {
    configureApi({
      searchNotes: vi.fn(async () => ({ ...response, hits: [], total: 0, degraded: true, skipped_notes: 2 })),
    });
    render(<SearchPanel onOpen={() => undefined} onClose={() => undefined} />);
    await userEvent.type(screen.getByLabelText("Search query"), "zzz");
    await userEvent.click(screen.getByRole("button", { name: "Search" }));
    await waitFor(() => expect(screen.getByText(/No results for/)).toBeInTheDocument());
    expect(screen.getByText(/Some notes were skipped \(2 unreadable\)/)).toBeInTheDocument();
  });
});
