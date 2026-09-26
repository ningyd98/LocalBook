import { beforeEach, describe, expect, it, vi } from "vitest";
import { fetchVaultFile, fetchVaultFiles, getVaultRoot, getVaultSession, patchVaultFile, setVaultSession } from "../../apps/web/src/api/client";
import { configureWorkspaceApi, toBase64, useWorkspaceStore } from "../../packages/workspace/src";

/**
 * A backend restart rotates the in-memory vault session id while the vault
 * itself is unchanged, so a page that was opened before the restart holds a
 * session the server no longer knows. These specs pin the client contract that
 * such a page rebinds the new session in place and replays the request that was
 * rejected — while a page bound to another vault is never retargeted, and a
 * request the server already applied is never replayed.
 */
const serviceSettings = (session: string, root = "/notes") => ({
  revision: 2, vault_session_id: session, changing: false, version: "1.3.0",
  vault: { root, status: "ready" },
  ai: { enabled: true, base_url: null, chat_model: "auto" },
});
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
const failure = (status: number, code: string) => new Response(JSON.stringify({ error: { code, message: code, path: null } }), { status });
const mutation = { path: "same.md", sha256: "sha256:new", byte_length: 11, operation: "updated" as const };
const fileRead = { path: "same.md", content_base64: toBase64("original"), byte_length: 8, sha256: "sha256:base", content_type: "text/markdown" };
const write = () => patchVaultFile({ path: "same.md", contentBase64: toBase64("local draft"), expectedSha256: "sha256:base" });
const header = (init: RequestInit | undefined) => new Headers(init?.headers).get("X-LocalNote-Vault-Session");

beforeEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); useWorkspaceStore.getState().resetVault(); setVaultSession(null); });

describe("Backend restart with an unchanged vault", () => {
  it("rebinds the rotated session and completes the write it had rejected", async () => {
    const calls: Array<RequestInit | undefined> = [];
    let writes = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/settings")) return json(serviceSettings("session-b"));
      if (url.endsWith("/vault/file")) { calls.push(init); writes += 1; return writes === 1 ? failure(428, "vault_session_required") : json(mutation); }
      return failure(404, "not_found");
    }));
    setVaultSession("session-a", "/notes");
    await expect(write()).resolves.toMatchObject({ sha256: "sha256:new" });
    expect(writes).toBe(2);
    expect(header(calls[0])).toBe("session-a");
    expect(header(calls[1])).toBe("session-b");
    expect(getVaultSession()).toBe("session-b");
    expect(getVaultRoot()).toBe("/notes");
  });
  it("retries a read the restarted backend rejected", async () => {
    let reads = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/settings")) return json(serviceSettings("session-b"));
      if (url.includes("/vault/files")) { reads += 1; return reads === 1 ? failure(428, "vault_session_changed") : json({ entries: [{ path: "same.md", kind: "file" }] }); }
      return failure(404, "not_found");
    }));
    setVaultSession("session-a", "/notes");
    await expect(fetchVaultFiles()).resolves.toMatchObject({ entries: [{ path: "same.md" }] });
    expect(reads).toBe(2);
  });
  it("notifies the workspace so the status bar and settings reload", async () => {
    const listener = vi.fn();
    window.addEventListener("localnote-vault-changed", listener);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/settings")) return json(serviceSettings("session-b"));
      if (url.endsWith("/vault/file")) return getVaultSession() === "session-b" ? json(mutation) : failure(428, "vault_session_required");
      return failure(404, "not_found");
    }));
    setVaultSession("session-a", "/notes");
    await write();
    window.removeEventListener("localnote-vault-changed", listener);
    expect(listener).toHaveBeenCalled();
  });
  it("shares one rebind round-trip between concurrent rejected reads", async () => {
    let settingsCalls = 0; const seen: Array<string | null> = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/settings")) { settingsCalls += 1; await Promise.resolve(); return json(serviceSettings("session-b")); }
      if (url.includes("/vault/files")) { seen.push(header(init)); return header(init) === "session-b" ? json({ entries: [] }) : failure(428, "vault_session_changed"); }
      return failure(404, "not_found");
    }));
    setVaultSession("session-a", "/notes");
    await Promise.all([fetchVaultFiles(), fetchVaultFiles()]);
    expect(settingsCalls).toBe(1);
    expect(seen).toEqual(["session-a", "session-a", "session-b", "session-b"]);
  });
  it("keeps an editor save alive across a restart", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/settings")) return json(serviceSettings("session-b"));
      if (url.startsWith("/api/v1/vault/file")) {
        if ((init?.method ?? "GET") === "PATCH") return header(init) === "session-b" ? json(mutation) : failure(428, "vault_session_required");
        return json(fileRead);
      }
      if (url.includes("/vault/files")) return json({ entries: [] });
      return failure(404, "not_found");
    }));
    setVaultSession("session-a", "/notes");
    configureWorkspaceApi({ fetchVaultFiles, fetchVaultFile, patchVaultFile });
    await useWorkspaceStore.getState().openFile("same.md");
    useWorkspaceStore.getState().updateContent("same.md", "local draft");
    await useWorkspaceStore.getState().save("same.md", "manual");
    expect(useWorkspaceStore.getState().sessions["same.md"]).toMatchObject({ dirty: false, saveState: "saved" });
    expect(getVaultSession()).toBe("session-b");
  });
});

describe("Guards that must survive the rebind", () => {
  it("never retargets a page whose bound vault root changed", async () => {
    let writes = 0; let settingsCalls = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/settings")) { settingsCalls += 1; return json(serviceSettings("session-b", "/other")); }
      if (url.endsWith("/vault/file")) { writes += 1; return failure(428, "vault_session_required"); }
      return failure(404, "not_found");
    }));
    setVaultSession("session-a", "/notes");
    await expect(write()).rejects.toMatchObject({ code: "vault_session_required" });
    expect(settingsCalls).toBe(1);
    expect(writes).toBe(1);
    expect(getVaultSession()).toBe("session-a");
    expect(getVaultRoot()).toBe("/notes");
  });
  it("retries once and then surfaces the session error", async () => {
    let writes = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/settings")) return json(serviceSettings("session-b"));
      if (url.endsWith("/vault/file")) { writes += 1; return failure(428, "vault_session_required"); }
      return failure(404, "not_found");
    }));
    setVaultSession("session-a", "/notes");
    await expect(write()).rejects.toMatchObject({ code: "vault_session_required" });
    expect(writes).toBe(2);
  });
  it("accepts a late response while the vault is unchanged and rejects it after a switch", async () => {
    let resolveResponse!: (value: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(resolve => { resolveResponse = resolve; })));
    setVaultSession("session-a", "/notes");
    const rotated = fetchVaultFile("same.md");
    setVaultSession("session-b", "/notes");
    resolveResponse(json(fileRead));
    await expect(rotated).resolves.toMatchObject({ sha256: "sha256:base" });

    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(resolve => { resolveResponse = resolve; })));
    const switched = fetchVaultFile("same.md");
    setVaultSession("session-c", "/other");
    resolveResponse(json(fileRead));
    await expect(switched).rejects.toMatchObject({ code: "stale_response" });
  });
  it("never replays a write the server already applied", async () => {
    let writes = 0; let resolveWrite!: (value: Response) => void;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/vault/file")) { writes += 1; return new Promise<Response>(resolve => { resolveWrite = resolve; }); }
      return Promise.resolve(json(serviceSettings("session-b")));
    }));
    setVaultSession("session-a", "/notes");
    const pending = write();
    setVaultSession("session-b", "/notes");
    resolveWrite(json(mutation));
    await expect(pending).resolves.toMatchObject({ sha256: "sha256:new" });
    expect(writes).toBe(1);
  });
  it("leaves a page that never bound a vault untouched", async () => {
    let rebinds = 0; let writes = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/settings")) { rebinds += 1; return json(serviceSettings("session-b")); }
      if (url.endsWith("/vault/file")) { writes += 1; return failure(428, "vault_session_required"); }
      return failure(404, "not_found");
    }));
    await expect(write()).rejects.toMatchObject({ code: "vault_session_required" });
    expect(rebinds).toBe(0);
    expect(writes).toBe(1);
  });
});
