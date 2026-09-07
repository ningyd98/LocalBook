/**
 * M5 GraphData frontend matrix: REST client URL/error contract, pure
 * DTO->Graphology conversion (stable keys, attributes, dangling skip) and
 * style tokens. fetch is fully mocked — no backend/network.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchGraph, fetchLocalGraph, fetchTagGraph } from "../../apps/web/src/api/client";
import { buildGraphologyGraph, graphStats } from "../../packages/graph/src";
import { edgeColor, edgeLineStyle, legend, nodeColor, THEMES } from "../../packages/graph/src/styles";
import type { GraphEdge, GraphNode, GraphResponse } from "../../packages/protocol/src";

function ok(payload: unknown) {
  return { ok: true, status: 200, json: async () => payload } as Response;
}
function errorResponse(status: number, code: string, message: string, path: string | null) {
  return { ok: false, status, json: async () => ({ error: { code, message, path } }) } as Response;
}

function note(path: string, title: string): GraphNode {
  return { id: `note:${encodeURIComponent(path)}`, type: "note", label: title, path, title, tag: null, tag_folded: null };
}
function tagNode(folded: string, label: string): GraphNode {
  return { id: `tag:${folded}`, type: "tag", label, path: null, title: null, tag: label, tag_folded: folded };
}
function linkEdge(id: string, source: string, target: string, broken = false, ambiguous = false, candidates: string[] = []): GraphEdge {
  return { id, source, target, type: "link", directed: true, raw: "[[x]]", resolved_path: null, section: null, block: null, broken, ambiguous, candidates, context: null };
}

function response(overrides: Partial<GraphResponse> = {}): GraphResponse {
  const a = note("notes/a.md", "A");
  const b = note("notes/Ref A.md", "Ref A");
  const tag = tagNode("工作", "工作");
  return {
    model: "note-tag-v1",
    scope: "global",
    root: null,
    nodes: [a, b, tag],
    edges: [
      linkEdge("link:#0", a.id, b.id),
      linkEdge("link:#1", a.id, b.id, false, true, ["notes/Alpha.md", "notes/Ref A.md"]),
      linkEdge("link:#2", a.id, "", true), // dangling broken — no endpoint
    ],
    page: { limit: 500, offset: 0, next_offset: null, total_nodes: 3, total_edges: 3, truncated: false },
    generated_at: "",
    ...overrides,
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("Graph REST client", () => {
  it("builds global query params through URLSearchParams", async () => {
    const mock = vi.fn(async (input: RequestInfo | URL) => { void input; return ok(response()); });
    vi.stubGlobal("fetch", mock);
    await fetchGraph({ limit: 100, offset: 5, tag: "工作", include_broken: false });
    const url = String(mock.mock.calls[0]![0]);
    expect(url.startsWith("/api/v1/graph?")).toBe(true);
    const params = new URLSearchParams(url.split("?")[1]);
    expect(params.get("limit")).toBe("100");
    expect(params.get("offset")).toBe("5");
    expect(params.get("tag")).toBe("工作");
    expect(params.get("include_broken")).toBe("false");
  });

  it("omits undefined query params", async () => {
    const mock = vi.fn(async (input: RequestInfo | URL) => { void input; return ok(response()); });
    vi.stubGlobal("fetch", mock);
    await fetchGraph();
    expect(String(mock.mock.calls[0]![0])).toBe("/api/v1/graph");
  });

  it("encodes local note path per segment and forwards depth/direction", async () => {
    const mock = vi.fn(async (input: RequestInfo | URL) => { void input; return ok(response({ scope: "local", root: "中文 note.md" })); });
    vi.stubGlobal("fetch", mock);
    await fetchLocalGraph("中文 note.md", { depth: 2, direction: "outgoing" });
    const url = String(mock.mock.calls[0]![0]);
    expect(url).toBe("/api/v1/graph/local/%E4%B8%AD%E6%96%87%20note.md?depth=2&direction=outgoing");
  });

  it("encodes tag paths per segment", async () => {
    const mock = vi.fn(async (input: RequestInfo | URL) => { void input; return ok(response({ scope: "tag" })); });
    vi.stubGlobal("fetch", mock);
    await fetchTagGraph("工作");
    const url = String(mock.mock.calls[0]![0]);
    expect(url).toBe("/api/v1/graph/tag/%E5%B7%A5%E4%BD%9C");
  });

  it("preserves ApiError status/code/path", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => { void input; return errorResponse(503, "index_unavailable", "Derived index is unavailable", null); }));
    const failure = await fetchGraph().then(
      () => null,
      (error: unknown) => error,
    );
    expect(failure).toBeInstanceOf(ApiError);
    const apiError = failure as ApiError;
    expect(apiError.status).toBe(503);
    expect(apiError.code).toBe("index_unavailable");
  });
});

describe("Graphology model (pure conversion)", () => {
  it("uses stable backend ids and keeps duplicate edges distinct", () => {
    const payload = response();
    const graph = buildGraphologyGraph(payload);
    expect(graph.order).toBe(3); // two notes + one tag
    // dangling broken edge has no endpoint -> not added, no virtual node
    expect(graph.size).toBe(2);
    expect(graph.hasNode("note:notes%2Fa.md")).toBe(true);
    expect(graph.hasNode("note:notes%2FRef%20A.md")).toBe(true);
    expect(graph.hasEdge("link:#0")).toBe(true);
    expect(graph.getEdgeAttribute("link:#0", "kind")).toBe("link");
    expect(graph.getEdgeAttribute("link:#1", "ambiguous")).toBe(true);
    expect(graph.getEdgeAttribute("link:#1", "candidates")).toEqual(["notes/Alpha.md", "notes/Ref A.md"]);
    expect(graph.getNodeAttribute("note:notes%2Fa.md", "kind")).toBe("note");
    expect(graph.getNodeAttribute("tag:工作", "kind")).toBe("tag");
  });

  it("never duplicates repeated response nodes/edges (defensive)", () => {
    const payload = response();
    const duplicated = buildGraphologyGraph({
      ...payload,
      nodes: [...payload.nodes, payload.nodes[0]!],
      edges: [...payload.edges, payload.edges[0]!],
    });
    expect(duplicated.order).toBe(3);
    expect(duplicated.size).toBe(2);
  });

  it("graphStats counts dangling broken/ambiguous edges", () => {
    const stats = graphStats(response());
    expect(stats.nodes).toBe(3);
    expect(stats.tags).toBe(1);
    expect(stats.edges).toBe(3);
    expect(stats.broken).toBe(1);
    expect(stats.ambiguous).toBe(1);
  });
});

describe("Graph styles", () => {
  it("exposes light/dark tokens and legend entries", () => {
    expect(THEMES.dark.note).toBeTruthy();
    const entries = legend("light");
    expect(entries.map((entry) => entry.key)).toContain("broken");
    expect(entries.map((entry) => entry.key)).toContain("ambiguous");
  });

  it("distinguishes note/tag and broken/ambiguous/backlink by color and line style", () => {
    expect(nodeColor("note", "light")).toBe(THEMES.light.note);
    expect(nodeColor("tag", "light")).toBe(THEMES.light.tag);
    expect(edgeColor({ kind: "tag", raw: null, broken: false, ambiguous: false, candidates: [], section: null, block: null }, "light")).toBe(THEMES.light.tagEdge);
    expect(edgeColor({ kind: "link", raw: null, broken: true, ambiguous: false, candidates: [], section: null, block: null }, "light")).toBe(THEMES.light.broken);
    expect(edgeColor({ kind: "link", raw: null, broken: false, ambiguous: true, candidates: [], section: null, block: null }, "light")).toBe(THEMES.light.ambiguous);
    expect(edgeLineStyle({ kind: "link", raw: null, broken: false, ambiguous: true, candidates: [], section: null, block: null })).toBe("dotted");
    expect(edgeLineStyle({ kind: "tag", raw: null, broken: false, ambiguous: false, candidates: [], section: null, block: null })).toBe("dashed");
    expect(edgeLineStyle({ kind: "link", raw: null, broken: false, ambiguous: false, candidates: [], section: null, block: null })).toBe("solid");
  });
});
