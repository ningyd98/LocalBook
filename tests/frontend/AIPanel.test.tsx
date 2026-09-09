/**
 * M6 AIPanel matrix: actions pass the current note + question, response
 * states render safely, offline/error states degrade, related clicks open an
 * existing note. All API calls are mocked — no backend/network.
 */
import { screen, waitFor } from "@testing-library/react";
import { render } from "./render";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AIPanel } from "../../apps/web/src/components/AIPanel";
import type {
  AIChatResponse,
  AIRelatedResponse,
  AITagsResponse,
} from "../../packages/protocol/src";
import type { AIStateBox } from "../../packages/workspace/src";
import { configureWorkspaceApi, useWorkspaceStore } from "../../packages/workspace/src";
import type { WorkspaceApi } from "../../packages/workspace/src";

const MODEL = "Qwen3.5-4B-Instruct-4bit";

function baseApi(): WorkspaceApi {
  return {
    fetchVaultFiles: vi.fn<WorkspaceApi["fetchVaultFiles"]>(async () => ({ entries: [] })),
    fetchVaultFile: vi.fn<WorkspaceApi["fetchVaultFile"]>(async () => ({ path: "a.md", content_base64: "I3g=", byte_length: 3, sha256: "s", content_type: "text/markdown" })),
    patchVaultFile: vi.fn<WorkspaceApi["patchVaultFile"]>(async () => ({ path: "a.md", sha256: "s", byte_length: 3, operation: "updated" })),
  };
}

function resetStore(path: string | null = "notes/a.md") {
  useWorkspaceStore.setState({
    tree: { entries: [], expandedPaths: [], status: "idle", error: null },
    tabs: [], activePath: path, sessions: {},
    relations: { status: "idle", path: null, outgoing: null, backlinks: null, brokenCount: 0, error: null },
    search: { status: "idle", query: "", response: null, error: null },
    graph: { status: "idle", scope: "global", note: null, depth: 1, direction: "both", tag: null, includeBroken: true, limit: 500, offset: 0, response: null, error: null, requestVersion: 0 },
    ai: { status: "idle", action: null, notePath: null, response: null, error: null, requestVersion: 0 },
    theme: "light", splitRatio: 50,
  });
}

function setAI(partial: Partial<AIStateBox>) {
  useWorkspaceStore.setState((s) => ({ ai: { ...s.ai, ...partial } }));
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  configureWorkspaceApi(baseApi());
  resetStore();
});

describe("AIPanel", () => {
  it("shows a hint and no actions when no note is open", () => {
    resetStore(null);
    render(<AIPanel onClose={vi.fn()} />);
    expect(screen.getByText("Open a Markdown note to use AI.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Summarize/ })).not.toBeInTheDocument();
  });

  it("Ask passes the current note and question, then renders the answer", async () => {
    const api = baseApi();
    const chatMock = vi.fn<NonNullable<WorkspaceApi["aiChat"]>>(async (): Promise<AIChatResponse> => ({
      answer: "The three conclusions…", citations: [], prompt_version: "chat@m6.1", model: MODEL, degraded: false,
    }));
    api.aiChat = chatMock;
    configureWorkspaceApi(api);
    render(<AIPanel onClose={vi.fn()} />);
    await userEvent.type(screen.getByLabelText("Ask this note"), "conclusions?");
    await userEvent.click(screen.getByRole("button", { name: /Ask/ }));
    await waitFor(() => expect(chatMock).toHaveBeenCalledWith({ note_path: "notes/a.md", question: "conclusions?" }));
    await waitFor(() => expect(screen.getByText("The three conclusions…")).toBeInTheDocument());
  });

  it("Summarize renders summary and key points", async () => {
    const api = baseApi();
    api.aiSummarize = vi.fn<NonNullable<WorkspaceApi["aiSummarize"]>>(async () => ({
      note_path: "notes/a.md", summary: "Short version", key_points: ["one", "two"],
      prompt_version: "summarize_note@m6.1", model: MODEL, degraded: false,
    }));
    configureWorkspaceApi(api);
    render(<AIPanel onClose={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /Summarize/ }));
    await waitFor(() => expect(screen.getByText("Short version")).toBeInTheDocument());
    expect(screen.getByText("one")).toBeInTheDocument();
    expect(screen.getByText("two")).toBeInTheDocument();
  });

  it("Tags renders suggestions as plain text (no HTML injection)", async () => {
    const api = baseApi();
    api.aiTags = vi.fn<NonNullable<WorkspaceApi["aiTags"]>>(async (): Promise<AITagsResponse> => ({
      note_path: "notes/a.md", tags: [{ name: "<b>bold</b>", reason: "grounded" }],
      prompt_version: "generate_tags@m6.1", model: MODEL, degraded: false,
    }));
    configureWorkspaceApi(api);
    render(<AIPanel onClose={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /Generate Tags/ }));
    await waitFor(() => expect(screen.getByText("Suggested tags")).toBeInTheDocument());
    expect(document.querySelector("b")).toBeNull(); // name rendered as text, never HTML
    expect(screen.getByText("<b>bold</b>")).toBeInTheDocument();
  });

  it("Related renders suggestions and clicking one opens that note", async () => {
    const openNote = vi.fn();
    const api = baseApi();
    api.aiRelated = vi.fn<NonNullable<WorkspaceApi["aiRelated"]>>(async (): Promise<AIRelatedResponse> => ({
      note_path: "notes/a.md",
      related: [{ path: "notes/b.md", title: "Beta", reason: "shared terms", score: 0.8 }],
      candidates_considered: 1, prompt_version: "suggest_links@m6.1", model: MODEL, degraded: false,
    }));
    configureWorkspaceApi(api);
    render(<AIPanel onClose={vi.fn()} onOpenNote={openNote} />);
    await userEvent.click(screen.getByRole("button", { name: /Suggest Related/ }));
    await waitFor(() => expect(screen.getByText("Beta")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /Beta/ }));
    expect(openNote).toHaveBeenCalledWith("notes/b.md");
  });

  it("shows a loading state while a request is in flight", () => {
    resetStore();
    setAI({ status: "loading", action: "ask", notePath: "notes/a.md" });
    render(<AIPanel onClose={vi.fn()} />);
    expect(screen.getByRole("status")).toHaveTextContent("Thinking…");
  });

  it("shows an offline message for a 503/AI-offline state", () => {
    resetStore();
    setAI({ status: "offline", action: "ask", notePath: "notes/a.md" });
    render(<AIPanel onClose={vi.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent("AI is offline or unavailable.");
  });

  it("shows the surfaced error message", () => {
    resetStore();
    setAI({
      status: "error", action: "ask", notePath: "notes/a.md",
      error: { kind: "api", code: "ai_invalid_output", status: 502, message: "AI returned invalid structured output", path: "notes/a.md" },
    });
    render(<AIPanel onClose={vi.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent("AI returned invalid structured output");
  });

  it("buttons carry accessible labels", () => {
    resetStore();
    render(<AIPanel onClose={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Close/ })).toBeEnabled();
    expect(screen.getByRole("textbox", { name: "Ask this note" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Ask/ })).toBeDisabled(); // empty question
  });
});
