/**
 * Home page smoke tests (PLAN 8.2 frontend matrix) + M2 Vault status linkage.
 *
 * fetch is fully mocked per test — no real backend, no oMLX, no filesystem.
 * The Vault status card now reflects the workspace tree state loaded through
 * the local Vault REST API, so these tests wait for the tree load and assert
 * the state linkage explicitly:
 *   successful tree load        -> "Vault: Connected"
 *   vault_not_configured error  -> "Vault: Not configured"
 *   HTTP 503                    -> "Vault: Unavailable"
 *   network / API errors        -> "Vault: Error"
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "../../apps/web/src/App";
import type { AIStatusResponse } from "../../apps/web/src/api/types";
import { useWorkspaceStore } from "../../packages/workspace/src";

function okResponse(payload: unknown) {
  return { ok: true, status: 200, json: async () => payload } as Response;
}

/** Failing HTTP response carrying the server error contract. */
function errorResponse(status: number, code: string, message: string) {
  return {
    ok: false,
    status,
    json: async () => ({ error: { code, message, path: null } }),
  } as Response;
}

function installFetch(health: unknown, ai: unknown, vault: Response = okResponse({ entries: [] })) {
  const mock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/api/v1/health")) return okResponse(health);
    if (url.endsWith("/api/v1/ai/status")) return okResponse(ai);
    if (url.includes("/api/v1/vault/files")) return vault;
    return { ok: false, status: 404, json: async () => ({}) } as Response;
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}

function aiStatus(overrides: Partial<AIStatusResponse>): AIStatusResponse {
  return {
    status: "not_configured",
    provider: "omlx",
    endpoint: null,
    qwen_model: null,
    models: [],
    capabilities: { chat: false, embedding: false, rerank: false },
    error_code: "not_configured",
    message: "AI endpoint is not configured",
    checked_at: null,
    ...overrides,
  };
}

async function waitForTreeStatus(status: string) {
  await waitFor(() => {
    const tree = useWorkspaceStore.getState().tree;
    if (tree.status !== status) throw new Error(`tree status ${tree.status}, want ${status}`);
  });
}

beforeEach(() => {
  vi.restoreAllMocks();
  useWorkspaceStore.setState({ tree: { entries: [], expandedPaths: [], status: "idle", error: null }, tabs: [], activePath: null, sessions: {}, theme: "light", splitRatio: 50 });
});

describe("LocalNote home page", () => {
  it("renders all four required status texts on success (health ok + AI not_configured)", async () => {
    installFetch({ status: "ok" }, aiStatus({}));

    render(<App />);

    expect(
      screen.getByRole("heading", { name: "LocalNote Server" }),
    ).toBeInTheDocument();
    expect(await screen.findByText("Server: Connected")).toBeInTheDocument();
    // successful tree load -> Vault: Connected
    await waitForTreeStatus("ready");
    expect(screen.getByText("Vault: Connected")).toBeInTheDocument();
    expect(screen.getByText("AI: Qwen3.5-4B / Not configured")).toBeInTheDocument();
  });

  it("shows AI Offline while keeping the server Connected", async () => {
    installFetch(
      { status: "ok" },
      aiStatus({
        status: "offline",
        endpoint: "http://127.0.0.1:8000/v1",
        error_code: "timeout",
        message: "oMLX endpoint did not respond before timeout",
      }),
    );

    render(<App />);

    expect(await screen.findByText("Server: Connected")).toBeInTheDocument();
    expect(screen.getByText("AI: Qwen3.5-4B / Offline")).toBeInTheDocument();
  });

  it("shows Connected and the discovered Qwen id when qwen_model is present", async () => {
    const model = {
      id: "Qwen3.5-4B-Instruct-4bit",
      owned_by: "omlx",
      capabilities: { chat: true, embedding: false, rerank: false },
    };
    installFetch(
      { status: "ok" },
      aiStatus({
        status: "connected",
        endpoint: "http://127.0.0.1:8000/v1",
        qwen_model: "Qwen3.5-4B-Instruct-4bit",
        models: [model],
        capabilities: { chat: true, embedding: false, rerank: false },
        error_code: null,
        message: null,
      }),
    );

    render(<App />);

    expect(await screen.findByText("AI: Qwen3.5-4B / Connected")).toBeInTheDocument();
    expect(screen.getByText(/Qwen3.5-4B-Instruct-4bit/)).toBeInTheDocument();
  });

  it("shows Not detected when connected but no Qwen model was found", async () => {
    installFetch(
      { status: "ok" },
      aiStatus({
        status: "connected",
        endpoint: "http://127.0.0.1:8000/v1",
        error_code: "no_matching_model",
        message: "oMLX endpoint reachable, but no Qwen3.5-4B model was discovered",
      }),
    );

    render(<App />);

    expect(await screen.findByText("AI: Qwen3.5-4B / Not detected")).toBeInTheDocument();
  });

  it("does not crash and shows Disconnected when the backend is down", async () => {
    const mock = vi.fn<
      (...args: [input: RequestInfo | URL, init?: RequestInit]) => Promise<Response>
    >(async (_input: RequestInfo | URL) => {
      throw new TypeError("Failed to fetch");
    });
    vi.stubGlobal("fetch", mock);

    render(<App />);

    expect(
      await screen.findByText("Server: Disconnected"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "LocalNote Server" }),
    ).toBeInTheDocument();
    // network error -> Vault: Error
    await waitForTreeStatus("error");
    expect(screen.getByText("Vault: Error")).toBeInTheDocument();
    expect(screen.getByText("AI: Qwen3.5-4B / Unavailable")).toBeInTheDocument();
  });

  it("shows Vault: Not configured when the tree load reports vault_not_configured", async () => {
    installFetch(
      { status: "ok" },
      aiStatus({}),
      errorResponse(503, "vault_not_configured", "Vault is not configured"),
    );

    render(<App />);

    expect(await screen.findByText("Server: Connected")).toBeInTheDocument();
    await waitForTreeStatus("not_configured");
    expect(screen.getByText("Vault: Not configured")).toBeInTheDocument();
  });

  it("shows Vault: Unavailable on an HTTP 503 tree load", async () => {
    installFetch(
      { status: "ok" },
      aiStatus({}),
      errorResponse(503, "vault_unavailable", "Vault backend is unavailable"),
    );

    render(<App />);

    expect(await screen.findByText("Server: Connected")).toBeInTheDocument();
    await waitForTreeStatus("unavailable");
    expect(screen.getByText("Vault: Unavailable")).toBeInTheDocument();
  });

  it("shows Vault: Error on a generic API error (HTTP 500)", async () => {
    installFetch(
      { status: "ok" },
      aiStatus({}),
      errorResponse(500, "internal_error", "Boom"),
    );

    render(<App />);

    expect(await screen.findByText("Server: Connected")).toBeInTheDocument();
    await waitForTreeStatus("error");
    expect(screen.getByText("Vault: Error")).toBeInTheDocument();
  });

  it("re-fetches status when the refresh button is clicked", async () => {
    const mock = vi.fn<
      (...args: [input: RequestInfo | URL, init?: RequestInit]) => Promise<Response>
    >(async (_input: RequestInfo | URL) => {
      throw new TypeError("Failed to fetch");
    });
    vi.stubGlobal("fetch", mock);

    render(<App />);
    expect(await screen.findByText("Server: Disconnected")).toBeInTheDocument();

    // Backend comes back up.
    mock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/health")) return okResponse({ status: "ok" });
      return okResponse(aiStatus({}));
    });

    await userEvent.click(screen.getByRole("button", { name: "Refresh status" }));
    expect(await screen.findByText("Server: Connected")).toBeInTheDocument();
  });
});
