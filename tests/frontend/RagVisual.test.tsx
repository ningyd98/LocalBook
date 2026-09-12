import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RagPanel } from "../../apps/web/src/components/RagPanel";
import type { RagEvidenceSummary, RagRetrievalStats, RagSearchHit } from "../../packages/protocol/src";

const visualContract: { evidence: RagEvidenceSummary; hit: RagSearchHit; stats: RagRetrievalStats } = {
  evidence: { source_count: 1, paths: ["architecture.md"], context_tokens: 30, candidate_count: 2, grounded: true },
  hit: { rank: 1, source_id: "S1", chunk_id: "chunk-1", path: "architecture.md", excerpt: "RAG retrieves source chunks.", score: 0.75, start_line: 8, end_line: 16 },
  stats: { fts_candidates: 2, vector_candidates: 1, link_candidates: 1, fused_candidates: 2, reranked: false, context_chunks: 1, context_tokens: 30, retrieval_ms: 12, embedding_ms: 1, rerank_ms: 0, generation_ms: 2, degraded: [] },
};
void visualContract;
import { render } from "./render";

const status = { enabled: true, status: "ready", embedding_provider: "test", embedding_model: "test", embedding_dimension: 1, embedding_version: "v1", embedding_degraded: false, vector_store: "memory", vector_kernel: "test", indexed_notes: 2, chunks: 2, embedded_chunks: 2, pending: 0, failed: 0, last_indexed: null, chunk_target_tokens: 100, chunk_max_tokens: 200, message: "" };
const response = { query: "architecture", answer: "The architecture is documented [S1].", sources: [{ id: "S1", path: "architecture.md", heading_path: "Design > RAG", start_line: 8, end_line: 16, excerpt: "RAG retrieves source chunks.", score: 0.75 }], retrieval_stats: { fts_candidates: 2, vector_candidates: 1, fused_candidates: 2, reranked: false, context_chunks: 1, context_tokens: 30, retrieval_ms: 12, embedding_ms: 1, rerank_ms: 0, generation_ms: 2, degraded: [] }, model: "test-model", prompt_version: "test", degraded: [], invalid_citations: [], generated_at: "2026-01-01T00:00:00Z" };

beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = input.toString();
    const body = url.includes("/rag/query") ? response : status;
    return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
  }));
});

describe("Rag visual evidence", () => {
  it("keeps the visual API contract fields type-safe", () => {
    expect(visualContract.evidence.source_count).toBe(1);
    expect(visualContract.hit.rank).toBe(1);
    expect(visualContract.hit.source_id).toBe("S1");
    expect(visualContract.stats.link_candidates).toBe(1);
  });

  it("renders a score-driven evidence map and only opens returned sources", async () => {
    const open = vi.fn();
    render(<RagPanel onOpenNote={open} />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/Ask the knowledge base/), "architecture");
    await user.click(screen.getByRole("button", { name: /Retrieve & answer/ }));
    expect(await screen.findByRole("list", { name: /Evidence relevance/ })).toBeInTheDocument();
    expect(screen.getByText("0.75")).toBeInTheDocument();
    expect(screen.getByText(/Model:/)).toHaveTextContent("test-model");
    await user.click(screen.getByRole("button", { name: /\[S1\]/ }));
    expect(open).toHaveBeenCalledWith("architecture.md");
  });

  it("shows loading while the server is retrieving", async () => {
    let resolveQuery!: (value: Response) => void;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => input.toString().includes("/rag/query") ? new Promise<Response>((resolve) => { resolveQuery = resolve; }) : Promise.resolve(new Response(JSON.stringify(status), { headers: { "Content-Type": "application/json" } }))));
    render(<RagPanel />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/Ask the knowledge base/), "architecture");
    await user.click(screen.getByRole("button", { name: /Retrieve & answer/ }));
    expect(screen.getByRole("status")).toHaveTextContent(/Retrieving and generating/);
    resolveQuery(new Response(JSON.stringify(response), { headers: { "Content-Type": "application/json" } }));
    await screen.findByText("0.75");
  });

  it("renders a clear empty evidence state", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => new Response(JSON.stringify(input.toString().includes("/rag/query") ? { ...response, answer: "No evidence.", sources: [] } : status), { headers: { "Content-Type": "application/json" } })));
    render(<RagPanel />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/Ask the knowledge base/), "unknown");
    await user.click(screen.getByRole("button", { name: /Retrieve & answer/ }));
    expect(await screen.findByText("No citable note passages were found.")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: /Evidence relevance/ })).not.toBeInTheDocument();
  });

  it("keeps evidence visible while reporting degraded generation", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => new Response(JSON.stringify(input.toString().includes("/rag/query") ? { ...response, answer: "", degraded: ["generation_unavailable"] } : status), { headers: { "Content-Type": "application/json" } })));
    render(<RagPanel />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/Ask the knowledge base/), "architecture");
    await user.click(screen.getByRole("button", { name: /Retrieve & answer/ }));
    expect(await screen.findByTestId("rag-degraded")).toHaveTextContent("generation_unavailable");
    expect(screen.getByRole("button", { name: /\[S1\]/ })).toBeInTheDocument();
  });

  it("renders API errors as an alert", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => input.toString().includes("/rag/query") ? new Response(JSON.stringify({ error: { code: "rag_unavailable", message: "RAG index unavailable" } }), { status: 503, headers: { "Content-Type": "application/json" } }) : new Response(JSON.stringify(status), { headers: { "Content-Type": "application/json" } })));
    render(<RagPanel />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/Ask the knowledge base/), "architecture");
    await user.click(screen.getByRole("button", { name: /Retrieve & answer/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/enable RAG in Settings|RAG index unavailable/);
  });

  it("labels an unscored server citation without inventing a relevance value", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => new Response(JSON.stringify(input.toString().includes("/rag/query") ? { ...response, sources: [{ ...response.sources[0], score: null }] } : status), { headers: { "Content-Type": "application/json" } })));
    render(<RagPanel />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/Ask the knowledge base/), "architecture");
    await user.click(screen.getByRole("button", { name: /Retrieve & answer/ }));
    await waitFor(() => expect(screen.getByText("unscored")).toBeInTheDocument());
  });
});
