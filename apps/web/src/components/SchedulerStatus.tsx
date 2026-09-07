import { useCallback, useEffect, useState } from "react";
import { Button } from "@localnote/ui";
import { ApiError, fetchSchedulerStatus, runSchedulerTask } from "../api/client";
import type { SchedulerRunDTO, SchedulerStatusResponse } from "../api/types";

type AgentTaskId = "daily_organizer" | "weekly_review";

/**
 * M8 scheduler status + manual trigger strip (PLAN-M8 §6.1/§8.2).
 *
 * Read-only status display plus "run now" buttons that reuse the exact
 * scheduler → M7 pipeline.  There is deliberately NO timer here: the strip
 * loads once and refreshes on demand; nothing in the frontend schedules,
 * and previews always flow through the existing History/Diff/Confirm UI.
 */
export function SchedulerStatus({
  active = true,
  onPreview,
}: {
  active?: boolean;
  onPreview?: (agentJobId: string, run: SchedulerRunDTO) => void;
}) {
  const [status, setStatus] = useState<SchedulerStatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [runningTask, setRunningTask] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setStatus(await fetchSchedulerStatus());
    } catch (caught) {
      const message =
        caught instanceof ApiError ? caught.message : "Scheduler is unavailable";
      setStatus(null);
      setError(message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (active) void load();
  }, [active, load]);

  const runNow = useCallback(
    async (task: AgentTaskId) => {
      setRunningTask(task);
      setError(null);
      try {
        const run = await runSchedulerTask(task, { confirm: true, auto_level2: false });
        if (run.status === "previewed" && run.agent_job_id && onPreview) {
          onPreview(run.agent_job_id, run);
        }
      } catch (caught) {
        setError(caught instanceof ApiError ? caught.message : "Scheduler run failed");
      } finally {
        setRunningTask(null);
        if (active) void load();
      }
    },
    [active, onPreview, load],
  );

  const running = status?.running ?? false;
  const stateLabel = !status
    ? "Unavailable"
    : !status.enabled
      ? "Disabled"
      : status.degraded
        ? "Degraded"
        : running
          ? "Running"
          : "Stopped";

  const agentJobs = (status?.jobs ?? []).filter(
    (job) => job.enabled && (job.id === "daily_organizer" || job.id === "weekly_review"),
  ) as Array<{ enabled: boolean; id: AgentTaskId }>;

  return (
    <article className="scheduler-status" aria-label="Scheduler">
      <header className="scheduler-header">
        <h2 className="status-label">Scheduler</h2>
        <span className="scheduler-state" data-testid="scheduler-state">
          {loading ? "Loading…" : stateLabel}
        </span>
        <span className="scheduler-meta" data-testid="scheduler-meta">
          {status ? `${status.backend} · ${status.timezone}` : "—"}
        </span>
      </header>
      {status?.network_exposure_warning && (
        <p role="alert" className="scheduler-warning">
          LAN exposure: the API has no auth/HTTPS. Set an explicit CORS origin and
          firewall the port.
        </p>
      )}
      {!!status?.recovery_required && (
        <p role="alert" className="scheduler-warning">
          {status.recovery_required} run(s) need explicit recovery.
        </p>
      )}
      {error && (
        <p role="alert" className="scheduler-error">
          {error}
        </p>
      )}
      <ul className="scheduler-jobs">
        {(status?.jobs ?? []).map((job) => (
          <li key={job.id}>
            <strong>{job.id}</strong>
            <span className={job.enabled ? "" : "scheduler-disabled"}>
              {job.enabled ? "enabled" : "disabled"}
            </span>
            <small>
              next: {job.next_run_at ? job.next_run_at.slice(0, 19).replace("T", " ") : "—"}
            </small>
            {job.last_status ? (
              <small data-testid={`last-${job.id}`}>last: {job.last_status}</small>
            ) : null}
          </li>
        ))}
      </ul>
      <div className="scheduler-actions">
        {agentJobs.map((job) => (
          <Button
            key={job.id}
            disabled={!status?.enabled || !running || loading || runningTask !== null}
            onClick={() => void runNow(job.id)}
          >
            {runningTask === job.id ? "Running…" : `Run ${job.id}`}
          </Button>
        ))}
      </div>
    </article>
  );
}

export default SchedulerStatus;
