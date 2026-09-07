import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  acceptJob,
  createJob,
  listHistory,
  undoHistory,
  ApiError,
} from "../../apps/web/src/api/client";
import type { JobDetailDTO } from "../../packages/protocol/src";

const detail: JobDetailDTO = {
  job_id: "job-1",
  task_type: "daily_organizer",
  status: "awaiting_confirmation",
  start_time: "2026-01-01T00:00:00+00:00",
  files_read: ["notes/a.md"],
  proposed_actions: [
    {
      action_id: "action-1",
      action: "add_tags",
      permission_level: 1,
      file: "notes/a.md",
      tags: ["inbox"],
      reason: "test",
    },
  ],
  executed_actions: [],
  diff: [],
  before_hash: {},
  after_hash: {},
};

function stubFetch(body: unknown, status = 200) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
    }) as Response),
  );
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("M7 API client", () => {
  it("creates a job with the right method/path/body", async () => {
    stubFetch(detail);
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    const job = await createJob({
      task_type: "daily_organizer",
      permission_level: 1,
      scope: { paths: ["notes/a.md"], max_files: 5 },
      execute: false,
    });
    expect(job.job_id).toBe("job-1");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/jobs");
    expect(init.method).toBe("POST");
    const body = JSON.parse(String(init.body));
    expect(body.task_type).toBe("daily_organizer");
    expect(body.scope.paths).toEqual(["notes/a.md"]);
  });

  it("accepts a subset of actions with confirmation", async () => {
    stubFetch({ ...detail, status: "committed" });
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    await acceptJob("job-1", { action_ids: ["action-1"], confirm: true });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/jobs/job-1/accept");
    expect(JSON.parse(String(init.body))).toEqual({
      action_ids: ["action-1"],
      confirm: true,
    });
  });

  it("maps domain error bodies into ApiError with code/meta", async () => {
    stubFetch(
      {
        error: { code: "policy_denied", message: "Policy denied", path: null },
        meta: { rules: ["deny_precedence"] },
      },
      403,
    );
    await expect(createJob({ task_type: "manual", permission_level: 1, scope: { paths: [] } } as never)).rejects.toMatchObject({
      status: 403,
      code: "policy_denied",
    });
  });

  it("network failures become network_error ApiError", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("offline"); }));
    const error = await createJob({ task_type: "manual", permission_level: 1, scope: { paths: [] } } as never).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).code).toBe("network_error");
    expect((error as ApiError).status).toBe(0);
  });

  it("lists history and undoes a committed job", async () => {
    stubFetch({ items: [{ job_id: "job-1", task_type: "manual", status: "committed", start_time: null, action_count: 0 }], total: 1, limit: 20, offset: 0 });
    const page = await listHistory();
    expect(page.total).toBe(1);
    stubFetch({ job_id: "job-1", status: "undone", restored: ["notes/a.md"], error: null });
    const undone = await undoHistory("job-1");
    expect(undone.status).toBe("undone");
  });
});
