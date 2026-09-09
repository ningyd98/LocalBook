import { screen, waitFor } from "@testing-library/react";
import { render } from "./render";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SchedulerStatus } from "../../apps/web/src/components/SchedulerStatus";
import type { SchedulerStatusResponse } from "../../packages/protocol/src";

function okResponse(body: unknown) {
  return {
    ok: true,
    status: 200,
    json: async () => body,
  } as Response;
}

function makeStatus(overrides: Partial<SchedulerStatusResponse> = {}): SchedulerStatusResponse {
  return {
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
        last_message: null,
        last_finished_at: null,
      },
      {
        id: "weekly_review",
        enabled: true,
        trigger: "cron",
        next_run_at: "2026-08-23T20:00:00+00:00",
        last_status: null,
        last_message: null,
        last_finished_at: null,
      },
      {
        id: "index_consistency",
        enabled: false,
        trigger: "interval",
        next_run_at: null,
        last_status: null,
        last_message: null,
        last_finished_at: null,
      },
    ],
    active_runs: 0,
    recovery_required: 0,
    history_retention_days: 30,
    ...overrides,
  };
}

function installFetch(handler: (input: string) => Response) {
  const mock = vi.fn(async (input: RequestInfo | URL) => handler(String(input)));
  vi.stubGlobal("fetch", mock);
  return mock;
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SchedulerStatus", () => {
  it("shows running state, backend, next run and last status", async () => {
    installFetch(() => okResponse(makeStatus()));
    render(<SchedulerStatus />);
    expect(await screen.findByTestId("scheduler-state")).toHaveTextContent("Running");
    expect(screen.getByTestId("scheduler-meta")).toHaveTextContent(/apscheduler · UTC/);
    expect(screen.getByText("Daily organizer")).toBeInTheDocument();
    expect(screen.getByTestId("last-daily_organizer")).toHaveTextContent("Preview ready");
    expect(screen.getByRole("button", { name: "Run Daily organizer" })).toBeEnabled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows stopped state", async () => {
    installFetch(() => okResponse(makeStatus({ enabled: true, running: false })));
    render(<SchedulerStatus />);
    expect(await screen.findByTestId("scheduler-state")).toHaveTextContent("Stopped");
  });

  it("shows disabled state", async () => {
    installFetch(() => okResponse(makeStatus({ enabled: false })));
    render(<SchedulerStatus />);
    expect(await screen.findByTestId("scheduler-state")).toHaveTextContent("Disabled");
  });

  it("shows degraded state", async () => {
    installFetch(() =>
      okResponse(makeStatus({ degraded: true, degraded_reason: "x", running: false })),
    );
    render(<SchedulerStatus />);
    expect(await screen.findByTestId("scheduler-state")).toHaveTextContent("Degraded");
  });

  it("renders LAN exposure and recovery warnings", async () => {
    installFetch(() =>
      okResponse(
        makeStatus({
          network_exposure_warning: true,
          recovery_required: 2,
          active_runs: 1,
        }),
      ),
    );
    render(<SchedulerStatus />);
    expect(await screen.findByText(/LAN exposure/i)).toBeInTheDocument();
    expect(screen.getByText(/2 run\(s\) need explicit recovery/)).toBeInTheDocument();
  });

  it("shows a safe error when the status fetch fails", async () => {
    installFetch(() => ({ ok: false, status: 503, json: async () => ({}) }) as Response);
    render(<SchedulerStatus />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/unavailable|failed/i);
  });

  it("Run buttons trigger the scheduler pipeline (preview) and surface the agent job", async () => {
    const onPreview = vi.fn();
    const mock = installFetch((input) => {
      if (input.includes("/scheduler/status")) return okResponse(makeStatus());
      if (input.includes("/scheduler/run/daily_organizer")) {
        return okResponse({
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
        });
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });
    render(<SchedulerStatus onPreview={onPreview} />);
    const button = await screen.findByRole("button", { name: "Run Daily organizer" });
    await userEvent.click(button);
    await waitFor(() => expect(onPreview).toHaveBeenCalledTimes(1));
    expect(onPreview).toHaveBeenCalledWith("job-1", expect.objectContaining({ run_id: "run-1" }));
    const runCall = mock.mock.calls.find((call) =>
      String(call[0]).includes("/scheduler/run/daily_organizer"),
    );
    expect(runCall).toBeTruthy();
    const init = (runCall as unknown[])[1] as RequestInit | undefined;
    expect(JSON.parse(String(init?.body ?? "{}"))).toEqual({ confirm: true, auto_level2: false });
  });
});
