import {useI18n} from "../i18n";
import {useTaskLabels,formatTaskTime} from "../i18n/labels";
import { useEffect, useState } from "react";
import { Button } from "@localnote/ui";
import { useWorkspaceStore } from "@localnote/workspace";
import { DiffView } from "./DiffView";
import { ConfirmDialog } from "./ConfirmDialog";
import { OrganizerActions } from "./OrganizerActions";

type ConfirmTarget = { kind: "accept_all" } | { kind: "undo" } | { kind: "accept_one"; actionId: string };

export function AIHistoryPanel({ onClose }: { onClose: () => void }) {
  const {tr,locale,errorText}=useI18n(); const label=useTaskLabels();
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
    <aside className="history-panel" aria-label={tr("AI 操作历史","AI history")}>
      <header className="history-header">
        <h2>{tr("操作历史","AI History")}</h2>
        <Button onClick={onClose}>{tr("关闭","Close")}</Button>
      </header>
      <OrganizerActions
        disabled={history.busy}
        onCreate={(request) => void create(request)}
      />
      {history.status === "loading" && <p role="status">{tr("正在加载历史记录…","Loading history\u2026")}</p>}
      {history.error && (
        <p role="alert" className="history-error">
          {errorText(history.error)}
        </p>
      )}
      {history.status === "ready" && !history.page?.items.length && (
        <p className="history-empty">{tr("还没有操作记录。可以先生成一份整理建议。","No AI jobs yet.")}</p>
      )}
      <ul className="history-list">
        {history.page?.items.map((item) => (
          <li key={item.job_id}>
            <button type="button" aria-pressed={detail?.job_id===item.job_id} onClick={() => void select(item.job_id)}>
              <strong>{label(item.task_type)}</strong>
              <span>{label(item.status)}</span>
              <small>{item.start_time ? formatTaskTime(item.start_time,locale) : item.job_id}</small>
            </button>
          </li>
        ))}
      </ul>
      {detail && (
        <section className="history-detail">
          <h3>
            {label(detail.task_type)} <small>{label(detail.status)}</small>
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
                  {tr("全部接受","Accept All")} ({pendingCount})
                </Button>
                <Button
                  disabled={history.busy || pendingCount === 0}
                  onClick={() => void reject(detail.job_id)}
                >
                  {tr("全部拒绝", "Reject All")}
                </Button>
              </>
            )}
            {detail.status === "committed" && (
              <Button disabled={history.busy} onClick={() => setConfirming({ kind: "undo" })}>
                {tr("撤销", "Undo")}
              </Button>
            )}
          </div>
          {detail.status === "conflict" ||
          detail.status === "rolled_back" ||
          detail.status === "rollback_failed" ? (
            <p role="alert" className="history-error">
              {typeof detail.error === "object" && detail.error
                ? errorText(detail.error)
                : tr(`${label(detail.status)}，请检查后处理。`, `${label(detail.status)}: review required`)}
            </p>
          ) : null}
        </section>
      )}
      <ConfirmDialog
        open={confirming !== null}
        title={confirming?.kind === "undo" ? tr("撤销任务","Undo job") : tr("确认变更","Confirm changes")}
        message={
          confirming?.kind === "undo"
            ? tr("将本次任务修改的文件恢复到执行前的状态？","Restore all files to their pre-job state?")
            : confirming?.kind === "accept_one"
              ? tr("接受这项建议并写入笔记？","Accept this proposed change and write it to the note?")
              : tr("接受全部建议并写入相关笔记？","Accept all proposed changes and write them to the notes?")
        }
        busy={history.busy}
        onCancel={() => setConfirming(null)}
        onConfirm={() => void runConfirmed()}
      />
    </aside>
  );
}
