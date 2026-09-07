import { useEffect, useState } from "react";
import { Button } from "@localnote/ui";
import { useWorkspaceStore } from "@localnote/workspace";
import { DiffView } from "./DiffView";
import { ConfirmDialog } from "./ConfirmDialog";
import { OrganizerActions } from "./OrganizerActions";

type ConfirmTarget = { kind: "accept_all" } | { kind: "undo" } | { kind: "accept_one"; actionId: string };

export function AIHistoryPanel({ onClose }: { onClose: () => void }) {
  const history = useWorkspaceStore((s) => s.history);
  const load = useWorkspaceStore((s) => s.loadHistory);
  const select = useWorkspaceStore((s) => s.selectHistory);
  const accept = useWorkspaceStore((s) => s.acceptJob);
  const reject = useWorkspaceStore((s) => s.rejectJob);
  const undo = useWorkspaceStore((s) => s.undoHistory);
  const create = useWorkspaceStore((s) => s.createJob);
  const [confirming, setConfirming] = useState<ConfirmTarget | null>(null);
  useEffect(() => {
    void load();
  }, [load]);
  const detail = history.selected;
  const pendingCount =
    detail?.diff?.filter((entry) => entry.status === "proposed").length ?? 0;
  const runConfirmed = async () => {
    if (!detail || !confirming) return;
    if (confirming.kind === "undo") {
      await undo(detail.job_id);
    } else if (confirming.kind === "accept_all") {
      await accept(detail.job_id, { confirm: true });
    } else {
      await accept(detail.job_id, { action_ids: [confirming.actionId], confirm: true });
    }
    setConfirming(null);
  };
  return (
    <aside className="history-panel" aria-label="AI history">
      <header className="history-header">
        <h2>AI History</h2>
        <Button onClick={onClose}>Close</Button>
      </header>
      <OrganizerActions
        disabled={history.busy}
        onCreate={(request) => void create(request)}
      />
      {history.status === "loading" && <p role="status">Loading history…</p>}
      {history.error && (
        <p role="alert" className="history-error">
          {history.error.message}
        </p>
      )}
      {history.status === "ready" && !history.page?.items.length && (
        <p className="history-empty">No AI jobs yet.</p>
      )}
      <ul className="history-list">
        {history.page?.items.map((item) => (
          <li key={item.job_id}>
            <button type="button" onClick={() => void select(item.job_id)}>
              <strong>{item.task_type}</strong>
              <span>{item.status}</span>
              <small>{item.start_time ?? item.job_id}</small>
            </button>
          </li>
        ))}
      </ul>
      {detail && (
        <section className="history-detail">
          <h3>
            {detail.task_type} <small>{detail.status}</small>
          </h3>
          <DiffView
            diff={detail.diff}
            disabled={history.busy}
            onAccept={
              detail.status === "awaiting_confirmation"
                ? (actionId) => setConfirming({ kind: "accept_one", actionId })
                : undefined
            }
          />
          <div className="history-actions">
            {detail.status === "awaiting_confirmation" && (
              <>
                <Button
                  disabled={history.busy || pendingCount === 0}
                  onClick={() => setConfirming({ kind: "accept_all" })}
                >
                  Accept All ({pendingCount})
                </Button>
                <Button
                  disabled={history.busy || pendingCount === 0}
                  onClick={() => void reject(detail.job_id)}
                >
                  Reject All
                </Button>
              </>
            )}
            {detail.status === "committed" && (
              <Button disabled={history.busy} onClick={() => setConfirming({ kind: "undo" })}>
                Undo
              </Button>
            )}
          </div>
          {detail.status === "conflict" ||
          detail.status === "rolled_back" ||
          detail.status === "rollback_failed" ? (
            <p role="alert" className="history-error">
              {typeof detail.error === "object" && detail.error
                ? String(detail.error.message ?? "")
                : `${detail.status}: review required`}
            </p>
          ) : null}
        </section>
      )}
      <ConfirmDialog
        open={confirming !== null}
        title={confirming?.kind === "undo" ? "Undo job" : "Confirm changes"}
        message={
          confirming?.kind === "undo"
            ? "Restore all files to their pre-job state?"
            : confirming?.kind === "accept_one"
              ? "Accept this proposed change and write it to the note?"
              : "Accept all proposed changes and write them to the notes?"
        }
        busy={history.busy}
        onCancel={() => setConfirming(null)}
        onConfirm={() => void runConfirmed()}
      />
    </aside>
  );
}
