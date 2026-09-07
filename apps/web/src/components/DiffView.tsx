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
  if (!diff.length) return <p className="history-empty">No changes proposed.</p>;
  return (
    <div className="diff-view" aria-label="Proposed changes">
      {diff.map((entry) => (
        <article key={entry.path} className="diff-entry">
          <h4>
            <code>{entry.path}</code>
            <span className="diff-status">{entry.status ?? "pending"}</span>
          </h4>
          <pre>{entry.unified_diff ?? `${entry.operation}: ${entry.before_size} → ${entry.after_size} bytes`}</pre>
          {onAccept && entry.status === "proposed" && entry.action_id ? (
            <Button
              className="diff-accept"
              disabled={disabled}
              onClick={() => onAccept(String(entry.action_id))}
            >
              Accept {entry.operation}
            </Button>
          ) : null}
        </article>
      ))}
    </div>
  );
}
