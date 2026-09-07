import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SchedulerStatus } from "../../apps/web/src/components/SchedulerStatus";

function okResponse(body: unknown) {
  return { ok: true, status: 200, json: async () => body } as Response;
}

const STATUS = {
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
      next_run_at: null,
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

describe("M8 isolation", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("does not poll: only the initial status fetch happens", async () => {
    const mock = vi.fn(async () => okResponse(STATUS));
    vi.stubGlobal("fetch", mock);
    render(<SchedulerStatus />);
    await waitFor(() =>
      expect(screen.getByTestId("scheduler-state")).toHaveTextContent("Running"),
    );
    expect(mock).toHaveBeenCalledTimes(1);
    // give any hypothetical poll timer room to fire — nothing should arrive
    await new Promise((resolve) => setTimeout(resolve, 150));
    expect(mock).toHaveBeenCalledTimes(1);
  });

  it("run action never auto-accepts a job — it only previews via the scheduler API", async () => {
    const fetchCalls: string[] = [];
    const mock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      fetchCalls.push(url);
      if (url.includes("/scheduler/status")) return okResponse(STATUS);
      if (url.includes("/scheduler/run/")) {
        return okResponse({
          run_id: "run-1",
          task: "daily_organizer",
          status: "previewed",
          trigger: "manual",
          agent_job_id: "job-1",
        });
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });
    vi.stubGlobal("fetch", mock);
    render(<SchedulerStatus onPreview={vi.fn()} />);
    const button = await screen.findByRole("button", { name: "Run daily_organizer" });
    await userEvent.click(button);
    await waitFor(() =>
      expect(fetchCalls.some((url) => url.includes("/scheduler/run/"))).toBe(true),
    );
    // the component only ever talks to the scheduler endpoints
    expect(fetchCalls.every((url) => url.includes("/scheduler/"))).toBe(true);
    expect(fetchCalls.some((url) => url.includes("/jobs/"))).toBe(false);
    expect(screen.queryByText(/committed/)).not.toBeInTheDocument();
  });

  it("does not read local storage for the scheduler strip", async () => {
    const storageSpy = vi.spyOn(Storage.prototype, "getItem");
    const mock = vi.fn(async () => okResponse(STATUS));
    vi.stubGlobal("fetch", mock);
    render(<SchedulerStatus />);
    await waitFor(() => expect(screen.getByTestId("scheduler-state")).toBeInTheDocument());
    expect(storageSpy).not.toHaveBeenCalled();
  });
});
