import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  fetchSchedulerStatus,
  listSchedulerRuns,
  runSchedulerTask,
  schedulerRecovery,
  ApiError,
} from "../../apps/web/src/api/client";
import type {
  SchedulerRunDTO,
  SchedulerStatusResponse,
} from "../../packages/protocol/src";

const status: SchedulerStatusResponse = {
  enabled: true,
  running: true,
  backend: "apscheduler",
  degraded: false,
  degraded_reason: null,
  timezone: "UTC",
  network_exposure_warning: false,
  jobs: [
    {
      id: "daily_organizer",
      enabled: true,
      trigger: "cron",
      next_run_at: "2026-08-16T23:00:00+00:00",
      last_status: "previewed",
    },
    {
      id: "weekly_review",
      enabled: true,
      trigger: "cron",
      next_run_at: "2026-08-23T20:00:00+00:00",
      last_status: null,
    },
    {
      id: "index_consistency",
      enabled: false,
      trigger: "interval",
      next_run_at: null,
      last_status: null,
    },
  ],
  active_runs: 0,
  recovery_required: 0,
  history_retention_days: 30,
};

function stubFetch(body: unknown, statusCode = 200) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: statusCode >= 200 && statusCode < 300,
      status: statusCode,
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

describe("M8 API client", () => {
  it("fetches scheduler status from /scheduler/status", async () => {
    stubFetch(status);
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    const result = await fetchSchedulerStatus();
    expect(result.enabled).toBe(true);
    expect(result.backend).toBe("apscheduler");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/scheduler/status");
    expect(init?.method ?? "GET").toBe("GET");
  });

  it("runs a task with a bounded body (never auto_level2 by default)", async () => {
    const run: SchedulerRunDTO = {
      run_id: "run-1",
      task: "daily_organizer",
      status: "previewed",
      trigger: "manual",
      agent_job_id: "job-1",
      policy: {
        decision: "confirm",
        level: 1,
        action_type: "add_tags",
        matched_rules: ["level1_requires_confirmation"],
        reasons: [],
      },
    };
    stubFetch(run);
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    const result = await runSchedulerTask("daily_organizer", { confirm: true });
    expect(result.agent_job_id).toBe("job-1");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/scheduler/run/daily_organizer");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ confirm: true });
  });

  it("lists runs with task filter", async () => {
    stubFetch({
      items: [
        {
          run_id: "run-1",
          task: "daily_organizer",
          status: "previewed",
          trigger: "scheduled",
          agent_job_id: "job-1",
        },
      ],
      total: 1,
      limit: 20,
      offset: 0,
    });
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    const page = await listSchedulerRuns({ task: "daily_organizer" });
    expect(page.total).toBe(1);
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/scheduler/runs?task=daily_organizer");
  });

  it("posts recovery actions", async () => {
    stubFetch({ run_id: "run-1", task: "daily_organizer", diagnosis: null });
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    await schedulerRecovery("run-1", "diagnose");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/scheduler/recovery/run-1");
    expect(JSON.parse(String(init.body))).toEqual({ action: "diagnose" });
  });

  it("maps scheduler domain errors into ApiError with code/meta", async () => {
    stubFetch(
      {
        error: { code: "scheduler_disabled", message: "Scheduler is disabled", path: null },
        meta: { task: "daily_organizer" },
      },
      409,
    );
    const error = await runSchedulerTask("daily_organizer").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(409);
    expect((error as ApiError).code).toBe("scheduler_disabled");
    expect((error as ApiError).meta).toEqual({ task: "daily_organizer" });
  });
});
