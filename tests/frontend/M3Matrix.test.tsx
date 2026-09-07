/**
 * M3 frontend matrix: API client URL/error contract, Ribbon enablement and
 * workspace store relations/search actions. fetch and API doubles are fully
 * mocked — no backend/network/filesystem.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBacklinks, fetchLinks, fetchMetadata, rebuildIndex, searchNotes } from "../../apps/web/src/api/client";
import { Ribbon } from "../../apps/web/src/components/Ribbon";
import type { BacklinksResponse, FileMutationResponse, FileReadResponse, IndexRebuildResponse, NoteLinksResponse, SearchResponse } from "../../packages/protocol/src";
import { configureWorkspaceApi, useWorkspaceStore } from "../../packages/workspace/src";
import type { WorkspaceApi } from "../../packages/workspace/src";

function ok(payload: unknown) {
  return { ok: true, status: 200, json: async () => payload } as Response;
}
function errorResponse(status: number, code: string, message: string) {
  return { ok: false, status, json: async () => ({ error: { code, message, path: "notes/a.md" } }) } as Response;
}

function resetStore() {
  useWorkspaceStore.setState({
    tree: { entries: [], expandedPaths: [], status: "idle", error: null },
    tabs: [], activePath: null, sessions: {},
    relations: { status: "idle", path: null, outgoing: null, backlinks: null, brokenCount: 0, error: null },
    search: { status: "idle", query: "", response: null, error: null },
    theme: "light", splitRatio: 50,
  });
}

function baseApi(): WorkspaceApi {
  return {
    fetchVaultFiles: vi.fn<WorkspaceApi["fetchVaultFiles"]>(async () => ({ entries: [] })),
    fetchVaultFile: vi.fn<WorkspaceApi["fetchVaultFile"]>(async (): Promise<FileReadResponse> => ({ path: "x.md", content_base64: "I3g=", byte_length: 3, sha256: "sha256:x", content_type: "text/markdown" })),
    patchVaultFile: vi.fn<WorkspaceApi["patchVaultFile"]>(async (): Promise<FileMutationResponse> => ({ path: "x.md", sha256: "sha256:x", byte_length: 3, operation: "updated" })),
    fetchLinks: async (path: string): Promise<NoteLinksResponse> => ({ path, outgoing: [], broken_count: 0, generated_at: "" }),
    fetchBacklinks: async (path: string): Promise<BacklinksResponse> => ({ path, backlinks: [], count: 0, generated_at: "" }),
    searchNotes: async (query: string): Promise<SearchResponse> => ({ query, hits: [], total: 0, degraded: false, skipped_notes: 0, generated_at: "" }),
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  resetStore();
});

describe("M3 API client", () => {
  it("encodes note paths per segment and reads JSON", async () => {
    const mock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/metadata/")) return ok({ path: "中文 note.md" });
      if (url.includes("/api/v1/links/")) return ok({ path: "notes/a.md", outgoing: [], broken_count: 0 });
      if (url.includes("/api/v1/backlinks/")) return ok({ path: "notes/a.md", backlinks: [], count: 0 });
      if (url.includes("/api/v1/search")) return ok({ query: "你好", hits: [], total: 0 });
      return errorResponse(404, "not_found", "missing");
    });
    vi.stubGlobal("fetch", mock);

    await fetchMetadata("notes/中文 note.md");
    expect(String(mock.mock.calls[0]![0])).toContain("/api/v1/metadata/notes/%E4%B8%AD%E6%96%87%20note.md");

    await fetchLinks("notes/a.md");
    expect(String(mock.mock.calls[1]![0])).toBe("/api/v1/links/notes/a.md");

    await fetchBacklinks("notes/a.md");
    expect(String(mock.mock.calls[2]![0])).toBe("/api/v1/backlinks/notes/a.md");

    await searchNotes("你好 世界");
    const searchUrl = String(mock.mock.calls[3]![0]);
    expect(searchUrl).toContain("/api/v1/search?");
    expect(new URLSearchParams(searchUrl.split("?")[1]).get("q")).toBe("你好 世界");
  });

  it("POSTs rebuild with no body", async () => {
    const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/v1/index/rebuild");
      expect(init?.method).toBe("POST");
      return ok({ indexed: 10, skipped: 0, failed: 1, duration_ms: 3.2, ready: true, generated_at: "" } satisfies IndexRebuildResponse);
    });
    vi.stubGlobal("fetch", mock);
    const result = await rebuildIndex();
    expect(result.indexed).toBe(10);
    expect(result.ready).toBe(true);
  });

  it("preserves ApiError status/code/path from server error bodies", async () => {
    const mock = vi.fn(async () => errorResponse(503, "index_unavailable", "Derived index is unavailable"));
    vi.stubGlobal("fetch", mock);
    const failure = await fetchLinks("notes/a.md").then(
      () => null,
      (error: unknown) => error,
    );
    const apiError = failure as { status: number; code?: string; path?: string | null; message: string };
    expect(apiError.status).toBe(503);
    expect(apiError.code).toBe("index_unavailable");
    expect(apiError.path).toBe("notes/a.md");
    expect(apiError.message).toBe("Derived index is unavailable");
  });
});

describe("M3 workspace store relations + search", () => {
  it("loads outgoing + backlinks together and clears", async () => {
    const links: NoteLinksResponse = { path: "notes/a.md", outgoing: [{ target: "Ref A", raw: "[[Ref A]]", kind: "wikilink", display: null, section: null, block: null, resolved_path: "notes/Ref A.md", broken: false, ambiguous: false, candidates: [] }], broken_count: 0, generated_at: "" };
    const backlinks: BacklinksResponse = { path: "notes/Ref A.md", backlinks: [{ source_path: "notes/zeta.md", title: "Zeta", text: "see" }], count: 1, generated_at: "" };
    const api = baseApi();
    api.fetchLinks = vi.fn<(path: string) => Promise<NoteLinksResponse>>(async () => links);
    api.fetchBacklinks = vi.fn<(path: string) => Promise<BacklinksResponse>>(async () => backlinks);
    configureWorkspaceApi(api);

    await useWorkspaceStore.getState().loadRelations("notes/a.md");
    const relations = useWorkspaceStore.getState().relations;
    expect(relations.status).toBe("ready");
    expect(relations.outgoing?.[0]?.resolved_path).toBe("notes/Ref A.md");
    expect(relations.backlinks?.[0]?.source_path).toBe("notes/zeta.md");

    useWorkspaceStore.getState().clearRelations();
    expect(useWorkspaceStore.getState().relations.path).toBeNull();
  });

  it("runSearch trims, records errors and clearSearch resets", async () => {
    const api = baseApi();
    api.searchNotes = vi.fn<(query: string) => Promise<SearchResponse>>(async (q) => ({ query: q, hits: [{ path: "a.md", title: "A", snippet: "hit", matched_terms: ["x"], score: 1 }], total: 1, degraded: false, skipped_notes: 0, generated_at: "" }));
    configureWorkspaceApi(api);

    await useWorkspaceStore.getState().runSearch("   x  ");
    let search = useWorkspaceStore.getState().search;
    expect(search.status).toBe("ready");
    expect(search.query).toBe("x");
    expect(search.response?.hits).toHaveLength(1);
    expect(api.searchNotes).toHaveBeenCalledWith("x");

    const failingApi = baseApi();
    failingApi.searchNotes = vi.fn<(query: string) => Promise<SearchResponse>>(async () => {
      throw Object.assign(new Error("boom"), { status: 503, code: "index_unavailable" });
    });
    configureWorkspaceApi(failingApi);
    await useWorkspaceStore.getState().runSearch("y");
    search = useWorkspaceStore.getState().search;
    expect(search.status).toBe("error");
    expect(search.error?.status).toBe(503);
    expect(search.error?.code).toBe("index_unavailable");

    useWorkspaceStore.getState().clearSearch();
    expect(useWorkspaceStore.getState().search.status).toBe("idle");
  });

  it("runSearch with a blank query is a local no-op", async () => {
    const api = baseApi();
    api.searchNotes = vi.fn<(query: string) => Promise<SearchResponse>>();
    configureWorkspaceApi(api);
    await useWorkspaceStore.getState().runSearch("   ");
    expect(api.searchNotes).not.toHaveBeenCalled();
    expect(useWorkspaceStore.getState().search.status).toBe("idle");
  });
});

describe("Ribbon (M3 + M5)", () => {
  it("enables Search, Graph, and AI; Files toggles", async () => {
    const onSelect = vi.fn();
    render(<Ribbon activeTool="files" onSelect={onSelect} />);

    expect(screen.getByRole("button", { name: /Search/ })).toBeEnabled();
    // M5: Graph and M6: AI are enabled read-only tools.
    expect(screen.getByRole("button", { name: /Graph/ })).toBeEnabled();
    expect(screen.getByRole("button", { name: /AI/ })).toBeEnabled();

    await userEvent.click(screen.getByRole("button", { name: /Search/ }));
    expect(onSelect).toHaveBeenCalledWith("search");
    onSelect.mockClear();
    await userEvent.click(screen.getByRole("button", { name: /Graph/ }));
    expect(onSelect).toHaveBeenCalledWith("graph");
  });
});
