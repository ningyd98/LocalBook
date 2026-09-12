/**
 * M14 RAG settings section: it reads the derived-index status, saves an
 * embedding endpoint that is independent from the chat model, and rebuilds the
 * index on demand. All API calls are mocked — no backend/network.
 */
import { screen, waitFor } from "@testing-library/react";
import { render } from "./render";
import userEvent from "@testing-library/user-event";
import { Profiler } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RagSettingsSection } from "../../apps/web/src/components/RagSettings";
import { fallbackRagLike } from "../../apps/web/src/api/ragDefaults";
import { RAG_PATCH_KEYS, ragPatchPayload } from "../../apps/web/src/api/settings";
import { SettingsPanel } from "../../apps/web/src/components/SettingsPanel";
import type { RagConfiguration, ServiceSettings } from "../../apps/web/src/api/settings";
import type { RagIndexStatusResponse } from "../../packages/protocol/src";

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
  pending: 3,
  failed: 0,
  last_indexed: "2026-09-10T10:00:00Z",
  chunk_target_tokens: 800,
  chunk_max_tokens: 1200,
  message: "",
};

function stubFetch(handlers: Record<string, (init?: RequestInit) => Response>) {
  const calls: Array<{ method: string; url: string; body: string }> = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    calls.push({ method: init?.method ?? "GET", url, body: String(init?.body ?? "") });
    for (const [suffix, handler] of Object.entries(handlers)) {
      if (url.includes(suffix)) return handler(init);
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

function settingsFixture(rag: RagConfiguration): ServiceSettings {
  return {
    revision: 7,
    vault_session_id: "session",
    changing: false,
    version: "1.0.0",
    vault: { root: "/tmp/vault", status: "ready" },
    ai: { enabled: true, base_url: "http://127.0.0.1:1234/v1", api_key_set: false, chat_model: "auto" },
    rag,
  };
}

const baseRag: RagConfiguration = fallbackRagLike();

beforeEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("RagSettingsSection", () => {
  it("shows the derived index status", async () => {
    stubFetch({ "/rag/index/status": () => json(STATUS) });
    render(
      <RagSettingsSection
        rag={baseRag}
        current={settingsFixture(baseRag)}
        busy={false}
        onSaved={vi.fn()}
        run={async operation => { await operation(); }}
        say={vi.fn()}
      />,
    );
    await waitFor(() => expect(screen.getByText("128")).toBeInTheDocument());
    // Chunks and embedded chunks are both 1921 here, so query all matches.
    expect(screen.getAllByText("1921").length).toBeGreaterThan(0);
    expect(screen.getByText("bge-m3")).toBeInTheDocument();
    expect(screen.getAllByText("3").length).toBeGreaterThan(0);
  });

  it("explains the local fallback embedder when degraded", async () => {
    stubFetch({
      "/rag/index/status": () => json({ ...STATUS, embedding_degraded: true, embedding_model: "local-hash" }),
    });
    render(
      <RagSettingsSection
        rag={{ ...baseRag, embedding_provider: "hash", embedding_model: "local-hash" }}
        current={settingsFixture(baseRag)}
        busy={false}
        onSaved={vi.fn()}
        run={async operation => { await operation(); }}
        say={vi.fn()}
      />,
    );
    await waitFor(() =>
      expect(screen.getByText(/semantic quality is limited/i)).toBeInTheDocument(),
    );
  });

  it("saves an independent embedding endpoint", async () => {
    const calls = stubFetch({
      "/rag/index/status": () => json(STATUS),
      "/settings/rag": () =>
        json(
          settingsFixture({
            ...baseRag,
            embedding_provider: "openai_compatible",
            embedding_base_url: "http://127.0.0.1:1234/v1",
            embedding_model: "bge-m3",
          }),
        ),
    });
    const onSaved = vi.fn();
    render(
      <RagSettingsSection
        rag={baseRag}
        current={settingsFixture(baseRag)}
        busy={false}
        onSaved={onSaved}
        run={async operation => { await operation(); }}
        say={vi.fn()}
      />,
    );
    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText(/Embedding provider/), "openai_compatible");
    await user.type(screen.getByLabelText(/Embedding base URL/), "http://127.0.0.1:1234/v1");
    await user.clear(screen.getByLabelText(/Embedding model/));
    await user.type(screen.getByLabelText(/Embedding model/), "bge-m3");
    await user.click(screen.getByRole("button", { name: /Save RAG settings/ }));

    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    const patch = calls.find(call => call.method === "PATCH");
    expect(patch).toBeDefined();
    const body = JSON.parse(patch!.body);
    expect(body.expected_revision).toBe(7);
    expect(body.rag.embedding_provider).toBe("openai_compatible");
    expect(body.rag.embedding_model).toBe("bge-m3");
    // The chat model is never reused as the embedding model.
    expect(body.rag.embedding_model).not.toBe("auto");
  });

  it("rebuilds the index and reports the counts", async () => {
    const calls = stubFetch({
      "/rag/index/status": () => json(STATUS),
      "/rag/index/rebuild": () =>
        json({
          indexed_documents: 128, indexed_chunks: 1921, embedded_chunks: 1921,
          skipped_documents: 0, failed_documents: 0, duration_ms: 4210.5,
          ready: true, degraded: false, degraded_reason: null, status: STATUS,
        }),
    });
    const say = vi.fn();
    render(
      <RagSettingsSection
        rag={baseRag}
        current={settingsFixture(baseRag)}
        busy={false}
        onSaved={vi.fn()}
        run={async operation => { await operation(); }}
        say={say}
      />,
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /Rebuild RAG index/ }));
    await waitFor(() => expect(say).toHaveBeenCalledWith(expect.stringMatching(/1921/)));
    expect(calls.some(call => call.method === "POST" && call.url.includes("/rag/index/rebuild"))).toBe(true);
  });

  it("keeps a stored embedding key when the field is untouched", async () => {
    const calls = stubFetch({
      "/rag/index/status": () => json(STATUS),
      "/settings/rag": () => json(settingsFixture(baseRag)),
    });
    render(
      <RagSettingsSection
        rag={{ ...baseRag, embedding_api_key_set: true }}
        current={settingsFixture({ ...baseRag, embedding_api_key_set: true })}
        busy={false}
        onSaved={vi.fn()}
        run={async operation => { await operation(); }}
        say={vi.fn()}
      />,
    );
    expect(screen.getByPlaceholderText(/Saved — leave blank to keep it/)).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /Save RAG settings/ }));
    await waitFor(() => expect(calls.some(call => call.method === "PATCH")).toBe(true));
    const body = JSON.parse(calls.find(call => call.method === "PATCH")!.body);
    expect("embedding_api_key" in body.rag).toBe(false);
  });

  it("reveals the HTTP reranker fields only when reranking is on", async () => {
    stubFetch({ "/rag/index/status": () => json(STATUS) });
    const { rerender } = render(
      <RagSettingsSection
        rag={baseRag}
        current={settingsFixture(baseRag)}
        busy={false}
        onSaved={vi.fn()}
        run={async operation => { await operation(); }}
        say={vi.fn()}
      />,
    );
    expect(screen.queryByLabelText(/Reranker base URL/)).not.toBeInTheDocument();
    rerender(
      <RagSettingsSection
        rag={{ ...baseRag, reranker_enabled: true, reranker_provider: "openai_compatible" }}
        current={settingsFixture(baseRag)}
        busy={false}
        onSaved={vi.fn()}
        run={async operation => { await operation(); }}
        say={vi.fn()}
      />,
    );
    expect(await screen.findByLabelText(/Reranker base URL/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Reranker model/)).toBeInTheDocument();
  });

  it("tests the rerank endpoint and reports an offline result", async () => {
    stubFetch({
      "/rag/index/status": () => json(STATUS),
      "/settings/rag/reranker/test": () =>
        json({ status: "offline", model: "bge-reranker-v2", message: "Reranker is unreachable", results: [] }),
    });
    const enabled: RagConfiguration = {
      ...baseRag,
      reranker_enabled: true,
      reranker_provider: "openai_compatible",
      reranker_base_url: "http://127.0.0.1:9997/v1",
      reranker_model: "bge-reranker-v2",
    };
    render(
      <RagSettingsSection
        rag={enabled}
        current={settingsFixture(enabled)}
        busy={false}
        onSaved={vi.fn()}
        run={async operation => { await operation(); }}
        say={vi.fn()}
      />,
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /Test rerank/ }));
    await waitFor(() =>
      expect(screen.getByText(/retrieval still works/)).toBeInTheDocument(),
    );
  });

  it("shows which vector kernel serves scans", async () => {
    stubFetch({ "/rag/index/status": () => json({ ...STATUS, vector_kernel: "python" }) });
    render(
      <RagSettingsSection
        rag={baseRag}
        current={settingsFixture(baseRag)}
        busy={false}
        onSaved={vi.fn()}
        run={async operation => { await operation(); }}
        say={vi.fn()}
      />,
    );
    await waitFor(() => expect(screen.getByText("python")).toBeInTheDocument());
  });

  it("surfaces a status failure instead of pretending the index is fine", async () => {
    stubFetch({
      "/rag/index/status": () =>
        json({ error: { code: "rag_unavailable", message: "RAG index is unavailable" } }, 503),
    });
    render(
      <RagSettingsSection
        rag={baseRag}
        current={settingsFixture(baseRag)}
        busy={false}
        onSaved={vi.fn()}
        run={async operation => { await operation(); }}
        say={vi.fn()}
      />,
    );
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  });

  // ------------------------------------------------------------------
  // Roadmap item ③: link/graph retrieval configuration surface
  // ------------------------------------------------------------------

  it("ships neutral link defaults in a stable frozen singleton", () => {
    const rag = fallbackRagLike();
    // Neutral by default: an unconfigured install behaves exactly as before.
    expect(rag.link_retrieval_enabled).toBe(false);
    expect(rag.link_top_k).toBe(20);
    expect(rag.wikilink_weight).toBe(1);
    expect(rag.backlink_weight).toBe(0.8);
    expect(rag.tag_weight).toBe(0.6);
    expect(rag.graph_weight).toBe(0.4);
    // Stable identity matters: the section re-syncs on stored-configuration
    // change, and a fresh object per call made every parent re-render look like
    // a stored change (silently discarding unsaved edits).
    expect(fallbackRagLike()).toBe(fallbackRagLike());
    expect(Object.isFrozen(fallbackRagLike())).toBe(true);
  });

  it("reveals the link weights only when link retrieval is on", async () => {
    stubFetch({ "/rag/index/status": () => json(STATUS) });
    const section = (rag: RagConfiguration) => (
      <RagSettingsSection
        rag={rag}
        current={settingsFixture(rag)}
        busy={false}
        onSaved={vi.fn()}
        run={async operation => { await operation(); }}
        say={vi.fn()}
      />
    );
    const { rerender } = render(section(baseRag));
    expect(screen.getByLabelText(/Enable link\/graph retrieval/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/Wikilink weight/)).not.toBeInTheDocument();
    rerender(
      section({ ...baseRag, link_retrieval_enabled: true }),
    );
    expect(await screen.findByLabelText(/Wikilink weight/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Backlink weight/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Same-tag weight/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Graph-neighbour weight/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Link top K/)).toBeInTheDocument();
  });

  it("sends only server-declared keys and the link settings when saving", async () => {
    const calls = stubFetch({
      "/rag/index/status": () => json(STATUS),
      "/settings/rag": () => json(settingsFixture(baseRag)),
    });
    const enabled: RagConfiguration = {
      ...baseRag,
      link_retrieval_enabled: true,
      link_top_k: 25,
      wikilink_weight: 2,
      backlink_weight: 1.5,
    };
    render(
      <RagSettingsSection
        rag={enabled}
        current={settingsFixture(enabled)}
        busy={false}
        onSaved={vi.fn()}
        run={async operation => { await operation(); }}
        say={vi.fn()}
      />,
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /Save RAG settings/ }));
    await waitFor(() => expect(calls.some(call => call.method === "PATCH")).toBe(true));
    const body = JSON.parse(calls.find(call => call.method === "PATCH")!.body);
    expect(body.rag.link_retrieval_enabled).toBe(true);
    expect(body.rag.link_top_k).toBe(25);
    expect(body.rag.wikilink_weight).toBe(2);
    expect(body.rag.backlink_weight).toBe(1.5);
    // Every key on the wire is one the server actually accepts: undeclared keys
    // make the server's extra="forbid" reject the whole save with 422.
    expect(Object.keys(body.rag).every(key => (RAG_PATCH_KEYS as readonly string[]).includes(key))).toBe(true);
    expect(body.rag.embedding_api_key_set).toBeUndefined();
    expect(body.rag.vector_min_score).toBeUndefined();
    // Status-only keys can never travel back in a settings PATCH.
    expect("link_retrieval" in body.rag).toBe(false);
  });

  it("keeps unsaved edits when the parent re-renders with equal content", async () => {
    stubFetch({ "/rag/index/status": () => json(STATUS) });
    const section = (rag: RagConfiguration) => (
      <RagSettingsSection
        rag={rag}
        current={settingsFixture(baseRag)}
        busy={false}
        onSaved={vi.fn()}
        run={async operation => { await operation(); }}
        say={vi.fn()}
      />
    );
    const { rerender } = render(section(baseRag));
    const user = userEvent.setup();
    const field = screen.getByLabelText(/Chunk target tokens/);
    await user.clear(field);
    await user.type(field, "900");
    expect(field).toHaveValue(900);
    // A fresh object with the SAME stored content and revision (what any
    // unrelated parent re-render produces) must not reset the draft.
    rerender(section({ ...baseRag }));
    await waitFor(() => expect(screen.getByLabelText(/Chunk target tokens/)).toHaveValue(900));
  });

  it("renders the link/status marker truthfully and stays silent when empty", async () => {
    stubFetch({ "/rag/index/status": () => json({ ...STATUS, link_retrieval: "" }) });
    const { unmount } = render(
      <RagSettingsSection
        rag={baseRag}
        current={settingsFixture(baseRag)}
        busy={false}
        onSaved={vi.fn()}
        run={async operation => { await operation(); }}
        say={vi.fn()}
      />,
    );
    await waitFor(() => expect(screen.getByText("128")).toBeInTheDocument());
    // "" means "the retriever did not report a link path" — nothing to show.
    expect(screen.queryByText(/Link retrieval/)).not.toBeInTheDocument();
    unmount();

    stubFetch({
      "/rag/index/status": () => json({ ...STATUS, link_retrieval: "link_unavailable" }),
    });
    render(
      <RagSettingsSection
        rag={baseRag}
        current={settingsFixture(baseRag)}
        busy={false}
        onSaved={vi.fn()}
        run={async operation => { await operation(); }}
        say={vi.fn()}
      />,
    );
    // A degraded reason is reported as-is, never softened into "enabled".
    await waitFor(() =>
      expect(screen.getByText(/Link retrieval is not active: link_unavailable/)).toBeInTheDocument(),
    );
  });

  it("shows the enabled marker without inventing a warning", async () => {
    stubFetch({ "/rag/index/status": () => json({ ...STATUS, link_retrieval: "enabled" }) });
    render(
      <RagSettingsSection
        rag={{ ...baseRag, link_retrieval_enabled: true }}
        current={settingsFixture(baseRag)}
        busy={false}
        onSaved={vi.fn()}
        run={async operation => { await operation(); }}
        say={vi.fn()}
      />,
    );
    await waitFor(() => expect(screen.getByText(/Link retrieval/)).toBeInTheDocument());
    expect(screen.getByText("enabled")).toBeInTheDocument();
    // The healthy state carries no warning.
    expect(screen.queryByText(/not active/)).not.toBeInTheDocument();
  });

  it("drops status-only keys from a PATCH body built from a status payload", () => {
    // A status payload bound to the settings state by mistake must not leak its
    // status fields into the wire body (the server rejects unknown keys).
    const payload = ragPatchPayload({
      ...fallbackRagLike(),
      link_retrieval: "enabled",
    } as RagConfiguration);
    expect("link_retrieval" in payload).toBe(false);
    expect(payload.link_retrieval_enabled).toBe(false);
    expect(payload.link_top_k).toBe(20);
  });

  it("settles when the parent snapshot has no rag block (pre-M14 server)", async () => {
    // The panel reads `current?.rag ?? fallbackRagLike()`; a fresh default object
    // per parent render would re-fire the draft sync on every unrelated render.
    // Counting commits proves the panel settles instead of looping.
    let commits = 0;
    const snapshotWithoutRag: ServiceSettings = {
      revision: 7, vault_session_id: "session", changing: false, version: "1.0.0",
      vault: { root: "/tmp/vault", status: "ready" },
      ai: { enabled: true, base_url: "http://127.0.0.1:1234/v1", api_key_set: false, chat_model: "auto" },
      // no `rag`: exactly what a pre-M14 server returns
    };
    stubFetch({
      "/rag/index/status": () => json(STATUS),
      "/settings": () => json(snapshotWithoutRag),
    });
    render(
      <Profiler id="settings" onRender={() => { commits += 1; }}>
        <SettingsPanel
          settings={snapshotWithoutRag}
          onClose={vi.fn()}
          onSaved={vi.fn()}
          onSwitch={vi.fn()}
          initialSection="rag"
        />
      </Profiler>,
    );
    expect(await screen.findByLabelText(/Enable link\/graph retrieval/)).toBeInTheDocument();
    const settled = commits;
    await new Promise(resolve => setTimeout(resolve, 50));
    expect(commits).toBe(settled);
    // Bounded, not merely "not growing in this window".
    expect(settled).toBeLessThan(40);
  });

  it("surfaces a failed save as role=alert (real settings panel, no rag block)", async () => {
    const snapshotWithoutRag: ServiceSettings = {
      revision: 7, vault_session_id: "session", changing: false, version: "1.0.0",
      vault: { root: "/tmp/vault", status: "ready" },
      ai: { enabled: true, base_url: "http://127.0.0.1:1234/v1", api_key_set: false, chat_model: "auto" },
    };
    stubFetch({
      "/settings/rag": () => json({ error: { code: "invalid_rag_settings", message: "rejected" } }, 422),
      "/rag/index/status": () => json(STATUS),
      "/settings": () => json(snapshotWithoutRag),
    });
    render(
      <SettingsPanel
        settings={snapshotWithoutRag}
        onClose={vi.fn()}
        onSaved={vi.fn()}
        onSwitch={vi.fn()}
        initialSection="rag"
      />,
    );
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Save RAG settings/ }));
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  });
});
