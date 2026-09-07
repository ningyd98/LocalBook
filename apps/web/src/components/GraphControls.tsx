import { useState } from "react";
import { Button } from "@localnote/ui";
import type { GraphScope } from "@localnote/protocol";
import type { GraphStateBox } from "@localnote/workspace";

export interface GraphControlsOverrides {
  scope: GraphScope;
  note: string | null;
  tag: string | null;
  depth: number;
  direction: "both" | "outgoing" | "incoming";
  includeBroken: boolean;
  limit: number;
  offset: number;
}

/**
 * M5 graph controls (PLAN-M5 §6.3): scope switch + depth/direction/limit/
 * broken/filter inputs. Local draft state is committed through ``onApply``;
 * the store owns the last applied values (the ``initial`` snapshot).
 */
export function GraphControls({
  initial,
  activeNote,
  loading,
  onApply,
}: {
  initial: GraphStateBox;
  activeNote: string | null;
  loading: boolean;
  onApply: (overrides: GraphControlsOverrides) => void;
}) {
  const [scope, setScope] = useState<GraphScope>(initial.scope);
  const [note, setNote] = useState<string>(initial.note ?? activeNote ?? "");
  const [tag, setTag] = useState<string>(initial.tag ?? "");
  const [depth, setDepth] = useState<number>(initial.depth);
  const [direction, setDirection] = useState<"both" | "outgoing" | "incoming">(initial.direction);
  const [includeBroken, setIncludeBroken] = useState<boolean>(initial.includeBroken);
  const [limit, setLimit] = useState<number>(initial.limit);

  const submit = (event?: { preventDefault: () => void }) => {
    event?.preventDefault();
    onApply({
      scope,
      note: scope === "local" && note.trim() ? note.trim() : null,
      tag: tag.trim() || null,
      depth,
      direction,
      includeBroken,
      limit,
      offset: 0,
    });
  };

  return (
    <form className="graph-controls" onSubmit={submit} aria-label="Graph controls">
      <label className="graph-control">
        <span>Scope</span>
        <select
          aria-label="Graph scope"
          value={scope}
          onChange={(event) => setScope(event.target.value as GraphScope)}
        >
          <option value="global">Global</option>
          <option value="local">Local (around a note)</option>
          <option value="tag">Tag</option>
        </select>
      </label>
      {scope === "local" && (
        <label className="graph-control graph-control-wide">
          <span>Note path (defaults to the open note)</span>
          <input
            aria-label="Local graph root note"
            value={note}
            placeholder={activeNote ?? "notes/example.md"}
            onChange={(event) => setNote(event.target.value)}
          />
        </label>
      )}
      {scope === "local" && (
        <>
          <label className="graph-control">
            <span>Depth</span>
            <select
              aria-label="Local graph depth"
              value={depth}
              onChange={(event) => setDepth(Number(event.target.value))}
            >
              {[0, 1, 2, 3].map((value) => (
                <option key={value} value={value}>{value}</option>
              ))}
            </select>
          </label>
          <label className="graph-control">
            <span>Direction</span>
            <select
              aria-label="Local graph direction"
              value={direction}
              onChange={(event) => setDirection(event.target.value as "both" | "outgoing" | "incoming")}
            >
              <option value="both">Both</option>
              <option value="outgoing">Outgoing</option>
              <option value="incoming">Incoming</option>
            </select>
          </label>
        </>
      )}
      {scope !== "tag" && (
        <label className="graph-control">
          <span>Tag filter</span>
          <input
            aria-label="Tag filter"
            value={tag}
            placeholder="工作"
            onChange={(event) => setTag(event.target.value)}
          />
        </label>
      )}
      {scope === "tag" && (
        <label className="graph-control">
          <span>Tag</span>
          <input
            aria-label="Tag graph tag"
            value={tag}
            placeholder="工作"
            onChange={(event) => setTag(event.target.value)}
          />
        </label>
      )}
      <label className="graph-control">
        <span>Limit</span>
        <select
          aria-label="Graph node limit"
          value={limit}
          onChange={(event) => setLimit(Number(event.target.value))}
        >
          {[100, 500, 1000, 2000].map((value) => (
            <option key={value} value={value}>{value}</option>
          ))}
        </select>
      </label>
      <label className="graph-control graph-control-check">
        <input
          type="checkbox"
          aria-label="Include broken links"
          checked={includeBroken}
          onChange={(event) => setIncludeBroken(event.target.checked)}
        />
        <span>Include broken</span>
      </label>
      <Button type="submit" disabled={loading} className="graph-control-apply">
        {loading ? "Loading…" : "Apply"}
      </Button>
    </form>
  );
}
