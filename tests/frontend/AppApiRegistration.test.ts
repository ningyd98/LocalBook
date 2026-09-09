/**
 * Registration contract: every client function the workspace store may call
 * must actually be handed to `configureWorkspaceApi` by App.tsx.
 *
 * The feature tests mock the workspace API directly, so a missing registration
 * (the store then short-circuits with "Attachment API is not configured")
 * would otherwise pass the whole suite while the UI is broken end to end.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const REQUIRED = [
  // Vault (M1/M2)
  "fetchVaultFiles",
  "fetchVaultFile",
  "patchVaultFile",
  "createVaultFile",
  "createVaultDirectory",
  "moveVaultFile",
  // Attachments (ATT-10/ATT-11)
  "uploadAttachmentBase64",
  "uploadAttachmentMultipart",
  // M3 relations/search
  "fetchLinks",
  "fetchBacklinks",
  "searchNotes",
  // M5 graph
  "fetchGraph",
  "fetchLocalGraph",
  "fetchTagGraph",
  // M6 AI
  "aiChat",
  "aiSummarize",
  "aiTags",
  "aiRelated",
  "aiExtractTodos",
  "aiClassify",
  // M7 history/jobs
  "listHistory",
  "getHistory",
  "createJob",
  "acceptJob",
  "rejectJob",
  "undoHistory",
] as const;

beforeEach(() => {
  vi.resetModules();
  vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 200 })));
});

describe("App API registration contract", () => {
  it("registers every client function the store depends on", async () => {
    const workspace = await import("../../packages/workspace/src/store");
    const spy = vi.spyOn(workspace, "configureWorkspaceApi");
    await import("../../apps/web/src/App");

    expect(spy).toHaveBeenCalled();
    const registered = spy.mock.calls.at(-1)?.[0] ?? {};
    const missing = REQUIRED.filter((key) => typeof (registered as Record<string, unknown>)[key] !== "function");
    expect(missing).toEqual([]);
  });

  it("registers only functions the client module actually exports", async () => {
    const client = await import("../../apps/web/src/api/client");
    const workspace = await import("../../packages/workspace/src/store");
    const spy = vi.spyOn(workspace, "configureWorkspaceApi");
    await import("../../apps/web/src/App");

    const registered = spy.mock.calls.at(-1)?.[0] ?? {};
    const notExported = Object.keys(registered).filter(
      (key) => typeof (registered as Record<string, unknown>)[key] !== typeof (client as Record<string, unknown>)[key],
    );
    expect(notExported).toEqual([]);
  });
});
