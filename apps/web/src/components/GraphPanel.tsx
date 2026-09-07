import { useEffect } from "react";
import { Button } from "@localnote/ui";
import { legend, SigmaGraph, graphStats } from "@localnote/graph";
import { useWorkspaceStore } from "@localnote/workspace";
import { GraphControls } from "./GraphControls";
import type { GraphControlsOverrides } from "./GraphControls";

/**
 * M5 Graph tool (PLAN-M5 §6.3). Reads the workspace graph slice; every fetch
 * is mocked in tests — no backend/network/filesystem here.
 *
 * - Note clicks open the note and return to Files (via onOpenNote).
 * - Tag clicks become the active tag filter (re-applied to the current
 *   scope); in Tag scope they simply reload the same pivot.
 * - WebGL absence/construction failure is handled inside SigmaGraph and
 *   renders the interactive fallback list.
 */
export function GraphPanel({
  onOpenNote,
  onClose,
}: {
  onOpenNote: (path: string) => void;
  onClose: () => void;
}) {
  const graph = useWorkspaceStore((s) => s.graph);
  const activePath = useWorkspaceStore((s) => s.activePath);
  const theme = useWorkspaceStore((s) => s.theme);
  const loadGraph = useWorkspaceStore((s) => s.loadGraph);
  const clearGraph = useWorkspaceStore((s) => s.clearGraph);

  useEffect(() => {
    // First mount of the tool: load the persisted view, or a fresh global one.
    if (graph.status === "idle" && graph.response === null) {
      void loadGraph({ scope: "global", limit: 500, offset: 0 });
    }
    return () => {
      // Keep state across tool switches but drop stale payloads.
      clearGraph();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const applyControls = (overrides: GraphControlsOverrides) => {
    void loadGraph(overrides);
  };

  const handleNodeClick = (target: { type: "note" | "tag"; path: string | null; tag: string | null }) => {
    if (target.type === "note" && target.path) {
      onOpenNote(target.path);
      return;
    }
    if (target.type === "tag" && target.tag) {
      void loadGraph({ tag: target.tag, offset: 0 });
    }
  };

  const stats = graph.response ? graphStats(graph.response) : null;
  const nextOffset = graph.response?.page.next_offset ?? null;
  const legendItems = legend(theme === "dark" ? "dark" : "light");

  return (
    <section className="graph-panel" aria-label="Knowledge graph">
      <header className="graph-panel-header">
        <h2>Graph</h2>
        <span className="graph-panel-root">
          {graph.scope === "local" && graph.response?.root
            ? `root: ${graph.response.root}`
            : graph.scope === "tag" && graph.tag
              ? `tag: ${graph.tag}`
              : "global note/tag graph"}
        </span>
        <Button onClick={onClose} aria-label="Close graph view">
          ×
        </Button>
      </header>

      <GraphControls
        initial={graph}
        activeNote={activePath}
        loading={graph.status === "loading"}
        onApply={applyControls}
      />

      {graph.status === "loading" && <p role="status" className="graph-state">Loading graph…</p>}
      {graph.status === "error" && (
        <p role="alert" className="graph-error">
          {graph.error?.message ?? "Unable to load the graph."}
          <Button onClick={() => applyControls({ scope: graph.scope, note: graph.note, tag: graph.tag, depth: graph.depth, direction: graph.direction, includeBroken: graph.includeBroken, limit: graph.limit, offset: graph.offset })}>
            Retry
          </Button>
        </p>
      )}
      {graph.status === "unavailable" && (
        <p role="alert" className="graph-error graph-error-unavailable">
          The derived index is unavailable — rebuild it on the server, then retry. ({graph.error?.message})
          <Button onClick={() => applyControls({ scope: graph.scope, note: graph.note, tag: graph.tag, depth: graph.depth, direction: graph.direction, includeBroken: graph.includeBroken, limit: graph.limit, offset: graph.offset })}>
            Retry
          </Button>
        </p>
      )}
      {graph.status === "empty" && (
        <p className="graph-state">No notes or tags match this view.</p>
      )}
      {graph.status === "ready" && graph.response && (
        <>
          <div className="graph-meta">
            <span role="status" aria-live="polite">
              {stats && `${stats.nodes} nodes · ${stats.edges} edges${stats.broken > 0 ? ` · ${stats.broken} broken` : ""}${stats.ambiguous > 0 ? ` · ${stats.ambiguous} ambiguous` : ""}`}
            </span>
            {graph.response.page.truncated && nextOffset !== null && (
              <Button
                className="graph-load-more"
                onClick={() => void loadGraph({ offset: nextOffset })}
              >
                Load more ({graph.response.nodes.length} of {graph.response.page.total_nodes} nodes shown)
              </Button>
            )}
            <Button
              className="graph-refresh"
              onClick={() => void loadGraph({})}
            >
              Refresh
            </Button>
          </div>
          <ul className="graph-legend" aria-label="Graph legend">
            {legendItems.map((item) => (
              <li key={item.key} className="graph-legend-item">
                <span
                  className={`graph-swatch graph-swatch-${item.key}`}
                  style={{ background: item.color }}
                  aria-hidden="true"
                />
                {item.label}
              </li>
            ))}
          </ul>
          <SigmaGraph
            response={graph.response}
            theme={theme === "dark" ? "dark" : "light"}
            onNodeClick={handleNodeClick}
          />
        </>
      )}
      {graph.status === "idle" && graph.response === null && (
        <p className="graph-state">Choose a view and press Apply.</p>
      )}
    </section>
  );
}
