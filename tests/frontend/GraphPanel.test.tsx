/**
 * M5 GraphPanel matrix (PLAN-M5 §9.2 rows 3–5/8): store-driven loading,
 * scope switching, tag/note click wiring, unavailable state and close.
 * Sigma renders via the jsdom fallback path — everything fetch-related is
 * an API double; no backend/network/WebGL.
 */
import { screen, waitFor } from "@testing-library/react";
import { render } from "./render";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { GraphPanel } from "../../apps/web/src/components/GraphPanel";
import type { GraphQuery, GraphResponse } from "../../packages/protocol/src";
import { configureWorkspaceApi, useWorkspaceStore } from "../../packages/workspace/src";
import type { WorkspaceApi } from "../../packages/workspace/src";

function baseResponse(): GraphResponse {
  return {
    model: "note-tag-v1",
    scope: "global",
    root: null,
    nodes: [
      { id: "note:a", type: "note", label: "A", path: "notes/a.md", title: "A", tag: null, tag_folded: null },
      { id: "tag:work", type: "tag", label: "工作", path: null, title: null, tag: "工作", tag_folded: "工作" },
    ],
    edges: [
      { id: "e1", source: "note:a", target: "tag:work", type: "tag", directed: true, raw: null, resolved_path: null, section: null, block: null, broken: false, ambiguous: false, candidates: [], context: null },
      { id: "e2", source: "note:a", target: "", type: "link", directed: true, raw: "[[Cfg C]]", resolved_path: null, section: null, block: null, broken: true, ambiguous: false, candidates: [], context: null },
    ],
    page: { limit: 500, offset: 0, next_offset: null, total_nodes: 2, total_edges: 2, truncated: false },
    generated_at: "",
  };
}

function resetStore() {
  useWorkspaceStore.setState({
    tree: { entries: [], expandedPaths: [], status: "idle", error: null },
    tabs: [], activePath: null, sessions: {},
    relations: { status: "idle", path: null, outgoing: null, backlinks: null, brokenCount: 0, error: null },
    search: { status: "idle", query: "", response: null, error: null },
    graph: { status: "idle", scope: "global", note: null, depth: 1, direction: "both", tag: null, includeBroken: true, limit: 500, offset: 0, response: null, error: null, requestVersion: 0 },
    theme: "light", splitRatio: 50,
  });
}

function apiWith(fetchGraphImpl: WorkspaceApi["fetchGraph"]): WorkspaceApi {
  return {
    fetchVaultFiles: vi.fn(async () => ({ entries: [] })),
    fetchVaultFile: vi.fn(async () => { throw new Error("unused"); }),
    patchVaultFile: vi.fn(async () => { throw new Error("unused"); }),
    fetchGraph: fetchGraphImpl,
    fetchLocalGraph: vi.fn(async (note: string, _query?: GraphQuery): Promise<GraphResponse> => ({ ...baseResponse(), scope: "local", root: note, page: { ...baseResponse().page, total_nodes: 2 } })),
    fetchTagGraph: vi.fn(async (tag: string, _query?: GraphQuery): Promise<GraphResponse> => ({ ...baseResponse(), scope: "tag", nodes: [{ id: "tag:work", type: "tag", label: tag, path: null, title: null, tag, tag_folded: tag }], edges: [], page: { ...baseResponse().page, total_nodes: 1, total_edges: 0 } })),
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  resetStore();
});

describe("GraphPanel", () => {
  it("auto-loads the global graph and shows stats through the fallback list", async () => {
    const fetchGraph = vi.fn(async (query?: GraphQuery) => ({ ...baseResponse(), page: { ...baseResponse().page, limit: query?.limit ?? 500, offset: query?.offset ?? 0 } }));
    configureWorkspaceApi(apiWith(fetchGraph));
    const onOpenNote = vi.fn();
    render(<GraphPanel onOpenNote={onOpenNote} onClose={vi.fn()} />);

    await screen.findByText(/nodes · .* edges/);
    expect(fetchGraph).toHaveBeenCalledWith({ limit: 500, offset: 0, include_broken: true });
    expect(screen.getByRole("region", { name: /Graph fallback view/ })).toBeTruthy();
  });

  it("switches scope to local and fetches around the typed note", async () => {
    const api = apiWith(vi.fn(async () => baseResponse()));
    configureWorkspaceApi(api);
    const fetchLocalGraph = api.fetchLocalGraph as ReturnType<typeof vi.fn>;
    render(<GraphPanel onOpenNote={vi.fn()} onClose={vi.fn()} />);
    await screen.findByText(/nodes · .* edges/);

    await userEvent.selectOptions(screen.getByLabelText("Graph scope"), "local");
    const noteInput = await screen.findByLabelText("Local graph root note");
    await userEvent.clear(noteInput);
    await userEvent.type(noteInput, "notes/a.md");
    await userEvent.click(screen.getByRole("button", { name: /Apply/ }));

    await waitFor(() => expect(fetchLocalGraph).toHaveBeenCalled());
    expect(fetchLocalGraph).toHaveBeenCalledWith("notes/a.md", expect.objectContaining({ depth: 1, direction: "both" }));
    await screen.findByText(/Root: notes\/a\.md/);
  });

  it("opens a note on click and filters by a clicked tag", async () => {
    const api = apiWith(vi.fn(async () => baseResponse()));
    configureWorkspaceApi(api);
    const onOpenNote = vi.fn();
    render(<GraphPanel onOpenNote={onOpenNote} onClose={vi.fn()} />);

    const noteButton = await screen.findByRole("button", { name: "A" });
    await userEvent.click(noteButton);
    expect(onOpenNote).toHaveBeenCalledWith("notes/a.md");

    const tagButton = screen.getByRole("button", { name: "工作" });
    await userEvent.click(tagButton);
    const fetchGraph = api.fetchGraph as ReturnType<typeof vi.fn>;
    await waitFor(() => {
      const calls = fetchGraph.mock.calls;
      const tagged = calls.find((call) => (call[0] as GraphQuery | undefined)?.tag === "工作");
      expect(tagged).toBeTruthy();
    });
  });

  it("shows the unavailable alert for a 503 index response and can retry", async () => {
    let calls = 0;
    const fetchGraph = vi.fn(async () => {
      calls += 1;
      if (calls === 1) {
        throw Object.assign(new Error("Derived index is unavailable"), { status: 503, code: "index_unavailable" });
      }
      return baseResponse();
    });
    configureWorkspaceApi(apiWith(fetchGraph));
    render(<GraphPanel onOpenNote={vi.fn()} onClose={vi.fn()} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/derived index is unavailable/i);
    await userEvent.click(screen.getByRole("button", { name: /Retry/ }));
    await screen.findByText(/nodes · .* edges/);
    expect(fetchGraph).toHaveBeenCalledTimes(2);
  });

  it("closes through the header button", async () => {
    configureWorkspaceApi(apiWith(vi.fn(async () => baseResponse())));
    const onClose = vi.fn();
    render(<GraphPanel onOpenNote={vi.fn()} onClose={onClose} />);
    await screen.findByText(/nodes · .* edges/);
    await userEvent.click(screen.getByRole("button", { name: /Close graph view/ }));
    expect(onClose).toHaveBeenCalled();
  });
});
