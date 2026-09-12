/**
 * M5 GraphStore matrix: workspace graph slice loading per scope, request
 * version race guard, empty/unavailable/error states and load-more offsets.
 * All fetch paths are API doubles — no backend/network/filesystem.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { GraphQuery, GraphResponse } from "../../packages/protocol/src";
import { configureWorkspaceApi, useWorkspaceStore } from "../../packages/workspace/src";
import type { WorkspaceApi } from "../../packages/workspace/src";

function globalResponse(): GraphResponse {
  return {
    model: "note-tag-v1",
    scope: "global",
    root: null,
    nodes: [
      { id: "note:a", type: "note", label: "A", path: "notes/a.md", title: "A", tag: null, tag_folded: null },
      { id: "tag:work", type: "tag", label: "工作", path: null, title: null, tag: "工作", tag_folded: "工作" },
    ],
    edges: [{ id: "tag:1", source: "note:a", target: "tag:work", type: "tag", directed: true, raw: null, resolved_path: null, section: null, block: null, broken: false, ambiguous: false, candidates: [], context: null }],
    page: { limit: 500, offset: 0, next_offset: null, total_nodes: 2, total_edges: 1, truncated: false },
    generated_at: "2026-01-01T00:00:00Z",
  };
}

function resetStore() {
  useWorkspaceStore.setState({
    tree: { entries: [], expandedPaths: [], collapsedPaths: [], status: "idle", error: null },
    tabs: [], activePath: null, sessions: {},
    relations: { status: "idle", path: null, outgoing: null, backlinks: null, brokenCount: 0, error: null },
    search: { status: "idle", query: "", response: null, error: null },
    graph: { status: "idle", scope: "global", note: null, depth: 1, direction: "both", tag: null, includeBroken: true, limit: 500, offset: 0, response: null, error: null, requestVersion: 0 },
    theme: "light", splitRatio: 50,
  });
}

function baseApi(): WorkspaceApi {
  return {
    fetchVaultFiles: vi.fn(async () => ({ entries: [] })),
    fetchVaultFile: vi.fn(async () => { throw new Error("unused"); }),
    patchVaultFile: vi.fn(async () => { throw new Error("unused"); }),
    fetchGraph: vi.fn(async (query?: GraphQuery) => ({ ...globalResponse(), page: { limit: query?.limit ?? 500, offset: query?.offset ?? 0, next_offset: null, total_nodes: 2, total_edges: 1, truncated: false } })),
    fetchLocalGraph: vi.fn(async (note: string, _query?: GraphQuery): Promise<GraphResponse> => ({ ...globalResponse(), scope: "local", root: note })),
    fetchTagGraph: vi.fn(async (tag: string, _query?: GraphQuery): Promise<GraphResponse> => ({ ...globalResponse(), scope: "tag", nodes: [{ id: "tag:work", type: "tag", label: tag, path: null, title: null, tag, tag_folded: tag }] })),
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  resetStore();
});

describe("workspace graph slice", () => {
  it("loads the global graph and reaches ready", async () => {
    const api = baseApi();
    configureWorkspaceApi(api);
    await useWorkspaceStore.getState().loadGraph({ scope: "global" });
    const graph = useWorkspaceStore.getState().graph;
    expect(graph.status).toBe("ready");
    expect(graph.scope).toBe("global");
    expect(graph.response?.page.total_nodes).toBe(2);
    expect(api.fetchGraph).toHaveBeenCalledWith({ limit: 500, offset: 0, include_broken: true });
  });

  it("loads local around the requested note (or falls back to activePath)", async () => {
    const api = baseApi();
    configureWorkspaceApi(api);
    useWorkspaceStore.setState({ activePath: "notes/open.md" });
    await useWorkspaceStore.getState().loadGraph({ scope: "local", depth: 2, direction: "incoming" });
    const graph = useWorkspaceStore.getState().graph;
    expect(graph.status).toBe("ready");
    expect(graph.note).toBeNull(); // fell back to activePath for the request
    expect(graph.response?.root).toBe("notes/open.md");
    expect(api.fetchLocalGraph).toHaveBeenCalledWith("notes/open.md", { limit: 500, offset: 0, include_broken: true, depth: 2, direction: "incoming", tag: undefined });
  });

  it("loads a tag graph", async () => {
    const api = baseApi();
    configureWorkspaceApi(api);
    await useWorkspaceStore.getState().loadGraph({ scope: "tag", tag: "工作" });
    const graph = useWorkspaceStore.getState().graph;
    expect(graph.status).toBe("ready");
    expect(graph.response?.nodes?.[0]?.tag_folded).toBe("工作");
    expect(api.fetchTagGraph).toHaveBeenCalledWith("工作", { limit: 500, offset: 0, include_broken: true });
  });

  it("drops stale responses via the request version guard", async () => {
    let resolveOld!: (value: GraphResponse) => void;
    let resolveNew!: (value: GraphResponse) => void;
    const api = baseApi();
    api.fetchGraph = vi.fn((query?: GraphQuery) => {
      if ((query?.tag ?? null) === "工作") {
        return new Promise<GraphResponse>((resolve) => { resolveNew = resolve; });
      }
      return new Promise<GraphResponse>((resolve) => { resolveOld = resolve; });
    });
    configureWorkspaceApi(api);
    const first = useWorkspaceStore.getState().loadGraph({ scope: "global", tag: null });
    const second = useWorkspaceStore.getState().loadGraph({ scope: "global", tag: "工作" });
    // Old request resolves last; its payload must be discarded.
    resolveNew({ ...globalResponse(), scope: "global", nodes: [], edges: [], page: { ...globalResponse().page, total_nodes: 0 } });
    await second;
    resolveOld(globalResponse());
    await first;
    const graph = useWorkspaceStore.getState().graph;
    // requestVersion is the module-level counter value captured by the
    // second (winning) request — never regressed by the stale first response.
    expect(graph.requestVersion).toBeGreaterThan(0);
    expect(graph.status).toBe("empty"); // new (second) response won the race
    expect(graph.tag).toBe("工作");
  });

  it("maps 503 to unavailable and network/api errors to error", async () => {
    const api = baseApi();
    api.fetchGraph = vi.fn(async () => {
      throw Object.assign(new Error("Derived index is unavailable"), { status: 503, code: "index_unavailable" });
    });
    configureWorkspaceApi(api);
    await useWorkspaceStore.getState().loadGraph({ scope: "global" });
    const graph = useWorkspaceStore.getState().graph;
    expect(graph.status).toBe("unavailable");
    expect(graph.error?.code).toBe("index_unavailable");
  });

  it("reports empty graphs as empty", async () => {
    const api = baseApi();
    api.fetchTagGraph = vi.fn(async (_tag: string, _query?: GraphQuery): Promise<GraphResponse> => ({ ...globalResponse(), scope: "tag", nodes: [], edges: [], page: { ...globalResponse().page, total_nodes: 0, total_edges: 0 } }));
    configureWorkspaceApi(api);
    await useWorkspaceStore.getState().loadGraph({ scope: "tag", tag: "从不存在的标签" });
    expect(useWorkspaceStore.getState().graph.status).toBe("empty");
  });

  it("keeps the offset for load-more and resets it on semantic change", async () => {
    const api = baseApi();
    const calls: Array<{ query: GraphQuery | undefined }> = [];
    api.fetchGraph = vi.fn(async (query?: GraphQuery) => {
      calls.push({ query });
      return { ...globalResponse(), page: { limit: query?.limit ?? 500, offset: query?.offset ?? 0, next_offset: (query?.offset ?? 0) === 0 ? 2 : null, total_nodes: 4, total_edges: 1, truncated: (query?.offset ?? 0) === 0 } };
    });
    configureWorkspaceApi(api);
    await useWorkspaceStore.getState().loadGraph({ scope: "global", limit: 2, offset: 0 });
    expect(useWorkspaceStore.getState().graph.offset).toBe(0);
    await useWorkspaceStore.getState().loadGraph({ offset: 2 }); // load more — same semantics
    expect(useWorkspaceStore.getState().graph.offset).toBe(2);
    await useWorkspaceStore.getState().loadGraph({ limit: 5 }); // semantic change -> offset 0
    expect(useWorkspaceStore.getState().graph.offset).toBe(0);
    expect(calls.at(-1)?.query?.offset).toBe(0);
    expect(calls.at(-1)?.query?.limit).toBe(5);
  });

  it("clearGraph resets the slice", async () => {
    configureWorkspaceApi(baseApi());
    await useWorkspaceStore.getState().loadGraph({ scope: "global" });
    useWorkspaceStore.getState().clearGraph();
    const graph = useWorkspaceStore.getState().graph;
    expect(graph.status).toBe("idle");
    expect(graph.response).toBeNull();
    expect(graph.requestVersion).toBe(0);
  });
});
