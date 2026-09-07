import { describe, expect, it, vi } from "vitest";
import { fetchAIStatus } from "../../apps/web/src/api/client";

describe("AI client safety contracts", () => {
  it("uses mocked fetch and preserves safe API errors", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      error: { code: "ai_unavailable", message: "offline", path: null },
    }), { status: 503, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(fetchAIStatus()).rejects.toMatchObject({ status: 503, code: "ai_unavailable", path: null });
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/ai/status", undefined);
  });

  it("parses server meta (prompt_version/model) from AI errors (S2)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      error: { code: "ai_invalid_output", message: "AI returned invalid structured output", path: null },
      meta: { prompt_version: "summarize_note@m6.1", model: "Qwen3.5-4B-Instruct-4bit" },
    }), { status: 502, headers: { "Content-Type": "application/json" } })));
    await expect(fetchAIStatus()).rejects.toMatchObject({
      status: 502,
      code: "ai_invalid_output",
      meta: { prompt_version: "summarize_note@m6.1", model: "Qwen3.5-4B-Instruct-4bit" },
    });
  });

  it("does not expose credentials from an error payload", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      error: { code: "ai_unavailable", message: "offline", path: null },
      secret: "token-must-not-be-shown",
    }), { status: 503 })));
    await expect(fetchAIStatus()).rejects.toThrow("offline");
    await expect(fetchAIStatus()).rejects.not.toThrow("token-must-not-be-shown");
  });
});
