/**
 * M6 isolation matrix: AI suggestions never write to the Vault, Ribbon AI
 * stays read-only, related clicks reuse openFile, and stale AI responses
 * never overwrite newer ones (requestVersion guard).
 */
import { screen, waitFor } from "@testing-library/react";
import { render } from "./render";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AIPanel } from "../../apps/web/src/components/AIPanel";
import { Ribbon } from "../../apps/web/src/components/Ribbon";
import type { AIChatResponse, AISummarizeResponse } from "../../packages/protocol/src";
import { configureWorkspaceApi, useWorkspaceStore } from "../../packages/workspace/src";
import type { WorkspaceApi } from "../../packages/workspace/src";

function baseApi(): WorkspaceApi {
  return {
    fetchVaultFiles: vi.fn<WorkspaceApi["fetchVaultFiles"]>(async () => ({ entries: [] })),
    fetchVaultFile: vi.fn<WorkspaceApi["fetchVaultFile"]>(async () => ({ path: "a.md", content_base64: "I3g=", byte_length: 3, sha256: "s", content_type: "text/markdown" })),
    patchVaultFile: vi.fn<WorkspaceApi["patchVaultFile"]>(async () => ({ path: "a.md", sha256: "s", byte_length: 3, operation: "updated" })),
  };
}

function resetStore() {
  useWorkspaceStore.setState({
    tree: { entries: [], expandedPaths: [], collapsedPaths: [], status: "idle", error: null },
    tabs: [], activePath: "notes/a.md", sessions: {},
    relations: { status: "idle", path: null, outgoing: null, backlinks: null, brokenCount: 0, error: null },
    search: { status: "idle", query: "", response: null, error: null },
    graph: { status: "idle", scope: "global", note: null, depth: 1, direction: "both", tag: null, includeBroken: true, limit: 500, offset: 0, response: null, error: null, requestVersion: 0 },
    ai: { status: "idle", action: null, notePath: null, response: null, error: null, requestVersion: 0 },
    theme: "light", splitRatio: 50,
  });
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  configureWorkspaceApi(baseApi());
  resetStore();
});

describe("AI isolation", () => {
  it("Ribbon AI is enabled and leaves Files/Search/Graph enabled", async () => {
    const onSelect = vi.fn();
    render(<Ribbon activeTool="files" onSelect={onSelect} />);
    await userEvent.click(screen.getByRole("button", { name: /AI/ }));
    expect(onSelect).toHaveBeenCalledWith("ai");
    for (const label of [/Files/, /Search/, /Graph/, /AI/]) {
      expect(screen.getByRole("button", { name: label })).toBeEnabled();
    }
  });

  it("summarize/tags/related suggestions never PATCH the Vault", async () => {
    const api = baseApi();
    const patch = vi.fn<WorkspaceApi["patchVaultFile"]>();
    api.patchVaultFile = patch;
    api.aiSummarize = vi.fn<NonNullable<WorkspaceApi["aiSummarize"]>>(async () => ({
      note_path: "notes/a.md", summary: "short summary", key_points: [], prompt_version: "summarize_note@m6.1", model: "m", degraded: false,
    }));
    api.aiTags = vi.fn<NonNullable<WorkspaceApi["aiTags"]>>(async () => ({
      note_path: "notes/a.md", tags: [{ name: "suggestion-tag", reason: "r" }], prompt_version: "generate_tags@m6.1", model: "m", degraded: false,
    }));
    api.aiRelated = vi.fn<NonNullable<WorkspaceApi["aiRelated"]>>(async () => ({
      note_path: "notes/a.md", related: [{ path: "notes/b.md", title: "Related-Note", reason: "r", score: 1 }],
      candidates_considered: 1, prompt_version: "suggest_links@m6.1", model: "m", degraded: false,
    }));
    configureWorkspaceApi(api);
    render(<AIPanel onClose={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /Summarize/ }));
    await waitFor(() => expect(screen.getByText("short summary")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /Generate Tags/ }));
    await waitFor(() => expect(screen.getByText("suggestion-tag")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /Suggest Related/ }));
    await waitFor(() => expect(screen.getByText("Related-Note")).toBeInTheDocument());
    expect(patch).not.toHaveBeenCalled();
    expect(useWorkspaceStore.getState().ai.status).toBe("ready");
  });

  it("chat never PATCHes the Vault either", async () => {
    const api = baseApi();
    const patch = vi.fn<WorkspaceApi["patchVaultFile"]>();
    api.patchVaultFile = patch;
    api.aiChat = vi.fn<NonNullable<WorkspaceApi["aiChat"]>>(async () => ({
      answer: "plain answer", citations: [], prompt_version: "chat@m6.1", model: "m", degraded: false,
    }));
    configureWorkspaceApi(api);
    render(<AIPanel onClose={vi.fn()} />);
    await userEvent.type(screen.getByLabelText("Ask this note"), "q?");
    await userEvent.click(screen.getByRole("button", { name: /Ask/ }));
    await waitFor(() => expect(screen.getByText("plain answer")).toBeInTheDocument());
    expect(patch).not.toHaveBeenCalled();
  });

  it("stale AI responses never overwrite a newer request (requestVersion)", async () => {
    let resolveFirst!: (v: AIChatResponse) => void;
    let resolveSecond!: (v: AISummarizeResponse) => void;
    const api = baseApi();
    api.aiChat = vi.fn<NonNullable<WorkspaceApi["aiChat"]>>(async () => new Promise((resolve) => { resolveFirst = resolve; }));
    api.aiSummarize = vi.fn<NonNullable<WorkspaceApi["aiSummarize"]>>(async () => new Promise((resolve) => { resolveSecond = resolve; }));
    configureWorkspaceApi(api);
    const store = useWorkspaceStore;
    const first = store.getState().runAI("ask", { note_path: "notes/a.md", question: "q" });
    const second = store.getState().runAI("summarize", { note_path: "notes/a.md" });
    resolveSecond({
      note_path: "notes/a.md", summary: "newer", key_points: [], prompt_version: "summarize_note@m6.1", model: "m", degraded: false,
    });
    await second;
    expect(store.getState().ai.response).toMatchObject({ summary: "newer" });
    // The first (stale) chat response arrives late and must be dropped.
    resolveFirst({ answer: "stale answer", citations: [], prompt_version: "chat@m6.1", model: "m", degraded: false });
    await first;
    expect(store.getState().ai.response).toMatchObject({ summary: "newer" });
    expect(store.getState().ai.status).toBe("ready");
    expect(JSON.stringify(store.getState().ai.response)).not.toContain("stale answer");
  });

  it("offline/503 AI errors never flip Vault or tree state", async () => {
    const api = baseApi();
    api.aiTags = vi.fn<NonNullable<WorkspaceApi["aiTags"]>>(async () => {
      throw Object.assign(new Error("AI is offline"), { status: 503, code: "ai_unavailable" });
    });
    configureWorkspaceApi(api);
    render(<AIPanel onClose={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /Generate Tags/ }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("AI is offline"));
    const state = useWorkspaceStore.getState();
    expect(state.ai.status).toBe("offline");
    expect(state.tree.status).toBe("idle");
    expect(state.sessions).toEqual({});
  });
});
