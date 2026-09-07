import type { GraphResponse } from "@localnote/protocol";
import { graphStats } from "./model";

/**
 * Operational fallback shown when WebGL is unavailable or Sigma cannot
 * initialise (PLAN-M5 §6.2 — the fallback is a first-class success path, not
 * an empty screen): statistics plus scrollable, clickable note/tag lists.
 */
export function GraphFallback({
  response,
  reason,
  onOpenNote,
  onFilterTag,
}: {
  response: GraphResponse;
  reason: string | null;
  onOpenNote: (path: string) => void;
  onFilterTag: (tag: string) => void;
}) {
  const stats = graphStats(response);
  const notes = response.nodes.filter((node) => node.type === "note");
  const tags = response.nodes.filter((node) => node.type === "tag" && node.tag_folded);
  return (
    <div className="graph-fallback" role="region" aria-label="Graph fallback view">
      {reason && <p className="graph-fallback-reason">{reason}</p>}
      <dl className="graph-stats">
        <div><dt>Notes</dt><dd>{stats.nodes - stats.tags}</dd></div>
        <div><dt>Tags</dt><dd>{stats.tags}</dd></div>
        <div><dt>Edges</dt><dd>{stats.edges}</dd></div>
        <div><dt>Broken</dt><dd>{stats.broken}</dd></div>
        <div><dt>Ambiguous</dt><dd>{stats.ambiguous}</dd></div>
      </dl>
      {response.page.truncated && (
        <p className="graph-fallback-hint">Graph truncated — only the first {response.nodes.length} nodes of {response.page.total_nodes} are listed.</p>
      )}
      <div className="graph-fallback-columns">
        <section aria-label="Note nodes">
          <h4>Notes ({notes.length})</h4>
          {notes.length === 0 ? (
            <p className="graph-fallback-empty">No note nodes in this view.</p>
          ) : (
            <ul className="graph-fallback-list">
              {notes.slice(0, 200).map((node) => (
                <li key={node.id}>
                  <button
                    type="button"
                    className="graph-fallback-link"
                    onClick={() => node.path && onOpenNote(node.path)}
                    disabled={!node.path}
                  >
                    {node.label}
                  </button>
                  {node.path && <code>{node.path}</code>}
                </li>
              ))}
            </ul>
          )}
        </section>
        <section aria-label="Tag nodes">
          <h4>Tags ({tags.length})</h4>
          {tags.length === 0 ? (
            <p className="graph-fallback-empty">No tag nodes in this view.</p>
          ) : (
            <ul className="graph-fallback-list">
              {tags.map((node) => (
                <li key={node.id}>
                  <button
                    type="button"
                    className="graph-fallback-tag"
                    onClick={() => node.tag && onFilterTag(node.tag)}
                    disabled={!node.tag}
                  >
                    {node.label}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
