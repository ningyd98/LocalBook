import {useI18n} from "../i18n";
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
  const {tr} = useI18n();
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
    <form className="graph-controls" onSubmit={submit} aria-label={tr("图谱筛选","Graph controls")}>
      <label className="graph-control">
        <span>{tr("范围","Scope")}</span>
        <select
          aria-label={tr("图谱范围","Graph scope")}
          value={scope}
          onChange={(event) => setScope(event.target.value as GraphScope)}
        >
          <option value="global">{tr("全部笔记","Global")}</option>
          <option value="local">{tr("当前笔记周边","Local (around a note)")}</option>
          <option value="tag">{tr("标签","Tag")}</option>
        </select>
      </label>
      {scope === "local" && (
        <label className="graph-control graph-control-wide">
          <span>{tr("笔记路径（默认为当前笔记）","Note path (defaults to the open note)")}</span>
          <input
            aria-label={tr("局部图谱根笔记","Local graph root note")}
            value={note}
            placeholder={activeNote ?? "notes/example.md"}
            onChange={(event) => setNote(event.target.value)}
          />
        </label>
      )}
      {scope === "local" && (
        <>
          <label className="graph-control">
            <span>{tr("层级","Depth")}</span>
            <select
              aria-label={tr("局部图谱深度","Local graph depth")}
              value={depth}
              onChange={(event) => setDepth(Number(event.target.value))}
            >
              {[0, 1, 2, 3].map((value) => (
                <option key={value} value={value}>{value}</option>
              ))}
            </select>
          </label>
          <label className="graph-control">
            <span>{tr("链接方向","Direction")}</span>
            <select
              aria-label={tr("局部图谱链接方向","Local graph direction")}
              value={direction}
              onChange={(event) => setDirection(event.target.value as "both" | "outgoing" | "incoming")}
            >
              <option value="both">{tr("双向","Both")}</option>
              <option value="outgoing">{tr("引用","Outgoing")}</option>
              <option value="incoming">{tr("被引用","Incoming")}</option>
            </select>
          </label>
        </>
      )}
      {scope !== "tag" && (
        <label className="graph-control">
          <span>{tr("标签筛选","Tag filter")}</span>
          <input
            aria-label={tr("标签筛选","Tag filter")}
            value={tag}
            placeholder="工作"
            onChange={(event) => setTag(event.target.value)}
          />
        </label>
      )}
      {scope === "tag" && (
        <label className="graph-control">
          <span>{tr("标签","Tag")}</span>
          <input
            aria-label={tr("图谱标签","Tag graph tag")}
            value={tag}
            placeholder="工作"
            onChange={(event) => setTag(event.target.value)}
          />
        </label>
      )}
      <label className="graph-control">
        <span>{tr("节点上限","Limit")}</span>
        <select
          aria-label={tr("图谱节点上限","Graph node limit")}
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
          aria-label={tr("包含失效链接","Include broken links")}
          checked={includeBroken}
          onChange={(event) => setIncludeBroken(event.target.checked)}
        />
        <span>{tr("包含失效链接","Include broken")}</span>
      </label>
      <Button type="submit" disabled={loading} className="graph-control-apply">
        {loading ? tr("加载中…","Loading…") : tr("应用筛选","Apply")}
      </Button>
    </form>
  );
}
