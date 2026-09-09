import {useI18n} from "../i18n";
import {useTaskLabels} from "../i18n/labels";
import type { DiffEntryDTO } from "@localnote/protocol";
import { Button } from "@localnote/ui";

interface DiffViewProps {
  diff: DiffEntryDTO[];
  /** Show a per-entry Accept button for proposed diffs (Level 1 confirm). */
  onAccept?: (actionId: string) => void;
  disabled?: boolean;
}

/**
 * Safe textual diff renderer. Diff text is rendered as React text nodes only
 * — never through dangerouslySetInnerHTML — so malformed/HTML content inside a
 * unified diff cannot execute or inject markup.
 */
export function DiffView({ diff, onAccept, disabled = false }: DiffViewProps) {
  const {tr}=useI18n(); const label=useTaskLabels();
  if (!diff.length) return <p className="history-empty">{tr("没有建议变更。","No changes proposed.")}</p>;
  return (
    <div className="diff-view" aria-label={tr("建议变更","Proposed changes")}>
      {diff.map((entry) => (
        <article key={entry.action_id ?? entry.path} className="diff-entry">
          <h4>
            <code>{entry.path}</code>
            <span className="diff-status">{label(entry.status ?? "pending")}</span>
          </h4>
          <pre>{entry.unified_diff ? entry.unified_diff.split("\n").map((line,index)=><span key={index} className={line.startsWith("+") && !line.startsWith("+++") ? "diff-added" : line.startsWith("-") && !line.startsWith("---") ? "diff-removed" : line.startsWith("@@") ? "diff-location" : undefined}>{line}{"\n"}</span>) : `${label(entry.operation)}: ${entry.before_size} → ${entry.after_size} ${tr("字节", "bytes")}`}</pre>
          {onAccept && entry.status === "proposed" && entry.action_id ? (
            <Button
              className="diff-accept"
              disabled={disabled}
              onClick={() => onAccept(String(entry.action_id))}
            >
              {tr("接受","Accept")} {label(entry.operation)}
            </Button>
          ) : null}
        </article>
      ))}
    </div>
  );
}
