/**
 * M14 RagPanel matrix: the mode switch reaches the panel from the AI inspector,
 * asking a question calls the RAG endpoint (never the per-note AI endpoint),
 * the answer renders with clickable server-provided sources, degraded and error
 * states stay visible, and the rebuild button reports what happened.
 *
 * All API calls are mocked — no backend/network.
 */
import { screen, waitFor } from "@testing-library/react";
import { render } from "./render";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AIPanel } from "../../apps/web/src/components/AIPanel";
import { RagPanel } from "../../apps/web/src/components/RagPanel";
import type { RagIndexStatusResponse, RagQueryResponse } from "../../packages/protocol/src";
import { configureWorkspaceApi, useWorkspaceStore } from "../../packages/workspace/src";
import type { WorkspaceApi } from "../../packages/workspace/src";

const STATUS: RagIndexStatusResponse = {
  enabled: true,
  status: "ready",
  embedding_provider: "OpenAICompatibleEmbeddingProvider",
  embedding_model: "bge-m3",
  embedding_dimension: 1024,
  embedding_version: "v1",
  embedding_degraded: false,
  vector_store: "sqlite",
  vector_kernel: "python",
  indexed_notes: 128,
  chunks: 1921,
  embedded_chunks: 1921,
  pending: 0,
  failed: 0,
  last_indexed: "2026-09-10T10:00:00Z",
  chunk_target_tokens: 800,
  chunk_max_tokens: 1200,
  message: "",
};

function makeResponse(overrides: Partial<RagQueryResponse> = {}): RagQueryResponse {
  return {
    query: "我的笔记里有没有讨论 Obsidian 插件？",
    answer: "你主要在两处讨论过插件兼容 [S1]。",
    sources: [
      {
        id: "S1",
        path: "LocalBook设计.md",
        heading: "插件兼容",
        heading_path: "架构 > 插件兼容",
        start_line: 31,
        end_line: 48,
        excerpt: "插件兼容层需要保持核心能力独立于 Web UI。",
      },
    ],
    retrieval_stats: {
      fts_candidates: 12,
      vector_candidates: 9,
      fused_candidates: 14,
      reranked: false,
      context_chunks: 1,
      context_tokens: 320,
      retrieval_ms: 42.5,
      embedding_ms: 11.2,
      rerank_ms: 0,
      generation_ms: 900.1,
      degraded: [],
    },
    model: "Qwen3.5-4B",
    prompt_version: "rag_answer@m14.1",
    degraded: [],
    invalid_citations: [],
    generated_at: "2026-09-10T10:00:01Z",
    ...overrides,
  };
}

function stubFetch(handlers: Record<string, () => Response | Promise<Response>>) {
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    calls.push(`${init?.method ?? "GET"} ${url}`);
    for (const [suffix, handler] of Object.entries(handlers)) {
      if (url.includes(suffix)) return handler();
    }
    return new Response(JSON.stringify({ error: { code: "not_found", message: "no handler" } }), {
      status: 404,
      headers: { "Content-Type": "application/json" },
    });
  }));
  return calls;
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function baseApi(): WorkspaceApi {
  return {
    fetchVaultFiles: vi.fn<WorkspaceApi["fetchVaultFiles"]>(async () => ({ entries: [] })),
    fetchVaultFile: vi.fn<WorkspaceApi["fetchVaultFile"]>(async () => ({ path: "a.md", content_base64: "I3g=", byte_length: 3, sha256: "s", content_type: "text/markdown" })),
    patchVaultFile: vi.fn<WorkspaceApi["patchVaultFile"]>(async () => ({ path: "a.md", sha256: "s", byte_length: 3, operation: "updated" })),
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  configureWorkspaceApi(baseApi());
  useWorkspaceStore.setState({
    tree: { entries: [], expandedPaths: [], collapsedPaths: [], status: "idle", error: null },
    tabs: [], activePath: "notes/a.md", sessions: {},
    relations: { status: "idle", path: null, outgoing: null, backlinks: null, brokenCount: 0, error: null },
    search: { status: "idle", query: "", response: null, error: null },
    graph: { status: "idle", scope: "global", note: null, depth: 1, direction: "both", tag: null, includeBroken: true, limit: 500, offset: 0, response: null, error: null, requestVersion: 0 },
    ai: { status: "idle", action: null, notePath: null, response: null, error: null, requestVersion: 0 },
    theme: "light", splitRatio: 50,
  });
});

describe("RagPanel", () => {
  it("shows the current index state", async () => {
    stubFetch({ "/rag/index/status": () => json(STATUS) });
    render(<RagPanel />);
    await waitFor(() => expect(screen.getByText(/128 notes/)).toBeInTheDocument());
    expect(screen.getByText(/1921 chunks/)).toBeInTheDocument();
  });

  it("asks the vault endpoint and renders the grounded answer with sources", async () => {
    const calls = stubFetch({
      "/rag/index/status": () => json(STATUS),
      "/rag/query": () => json(makeResponse()),
    });
    const onOpenNote = vi.fn();
    render(<RagPanel onOpenNote={onOpenNote} />);
    const user = userEvent.setup();
    await user.type(
      screen.getByLabelText(/Ask the knowledge base/),
      "我的笔记里有没有讨论 Obsidian 插件？",
    );
    await user.click(screen.getByRole("button", { name: /Retrieve & answer/ }));

    const sourceButton = await screen.findByRole("button", { name: /\[S1\]/ });
    expect(sourceButton).toBeInTheDocument();
    expect(sourceButton.textContent).toContain("LocalBook设计.md");
    expect(calls.some(call => call.includes("POST /api/v1/rag/query"))).toBe(true);
    // The per-note AI endpoint must not be touched by the vault mode.
    expect(calls.some(call => call.includes("/ai/"))).toBe(false);
    expect(screen.getByText("你主要在两处讨论过插件兼容 [S1]。")).toBeInTheDocument();
    expect(screen.getByText(/lines 31-48/)).toBeInTheDocument();

    await user.click(sourceButton);
    expect(onOpenNote).toHaveBeenCalledWith("LocalBook设计.md");
  });

  it("renders no source list when the answer has no evidence", async () => {
    stubFetch({
      "/rag/index/status": () => json(STATUS),
      "/rag/query": () =>
        json(
          makeResponse({
            answer: "根据当前知识库内容，没有找到足够证据回答这个问题。",
            sources: [],
          }),
        ),
    });
    render(<RagPanel />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/Ask the knowledge base/), "完全不相关的问题");
    await user.click(screen.getByRole("button", { name: /Retrieve & answer/ }));
    await waitFor(() =>
      expect(screen.getByText(/没有找到足够证据/)).toBeInTheDocument(),
    );
    expect(screen.queryByText("Sources")).not.toBeInTheDocument();
  });

  it("reports degraded retrieval without hiding the answer", async () => {
    stubFetch({
      "/rag/index/status": () => json(STATUS),
      "/rag/query": () => json(makeResponse({ degraded: ["vector_unavailable"] })),
    });
    render(<RagPanel />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/Ask the knowledge base/), "云边协同");
    await user.click(screen.getByRole("button", { name: /Retrieve & answer/ }));
    // React splits `Degraded: vector_unavailable` across text nodes, so the
    // assertion matches on the element's combined text via a role query.
    await waitFor(() =>
      expect(screen.getByTestId("rag-degraded")).toHaveTextContent("vector_unavailable"),
    );
    expect(screen.getByRole("button", { name: /\[S1\]/ })).toBeInTheDocument();
  });

  it("shows a clear message when the RAG index is unavailable", async () => {
    stubFetch({
      "/rag/index/status": () =>
        json({ error: { code: "rag_unavailable", message: "RAG index is unavailable" } }, 503),
      "/rag/query": () =>
        json({ error: { code: "rag_unavailable", message: "RAG index is unavailable" } }, 503),
    });
    render(<RagPanel />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/Ask the knowledge base/), "云边协同");
    await user.click(screen.getByRole("button", { name: /Retrieve & answer/ }));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/enable RAG in Settings/i),
    );
  });

  it("rebuilds the index and reports the counts", async () => {
    const calls = stubFetch({
      "/rag/index/status": () => json(STATUS),
      "/rag/index/rebuild": () =>
        json({
          indexed_documents: 128,
          indexed_chunks: 1921,
          embedded_chunks: 1921,
          skipped_documents: 0,
          failed_documents: 0,
          duration_ms: 4210.5,
          ready: true,
          degraded: false,
          degraded_reason: null,
          status: STATUS,
        }),
    });
    render(<RagPanel />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /Rebuild RAG index/ }));
    await waitFor(() =>
      expect(screen.getByText(/Rebuilt: 128 note\(s\), 1921 chunk\(s\)/)).toBeInTheDocument(),
    );
    expect(calls.some(call => call.includes("POST /api/v1/rag/index/rebuild"))).toBe(true);
  });

  it("surfaces a rebuild failure as a role=alert message", async () => {
    stubFetch({
      "/rag/index/status": () => json(STATUS),
      "/rag/index/rebuild": () =>
        json({ error: { code: "rag_unavailable", message: "RAG index is unavailable" } }, 503),
    });
    render(<RagPanel />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /Rebuild RAG index/ }));
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  });
});

describe("AIPanel modes", () => {
  it("switches from current-note mode to the knowledge base", async () => {
    stubFetch({ "/rag/index/status": () => json(STATUS) });
    render(<AIPanel onClose={vi.fn()} />);
    expect(screen.getByText("Current note:")).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "Vault RAG" }));
    expect(screen.getByLabelText(/Ask the knowledge base/)).toBeInTheDocument();
    expect(screen.queryByText("Current note:")).not.toBeInTheDocument();
    // Existing M6 actions stay available in the default mode.
    await user.click(screen.getByRole("tab", { name: "Current note" }));
    expect(screen.getByRole("button", { name: /Summarize/ })).toBeInTheDocument();
  });
});
