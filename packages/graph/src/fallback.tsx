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
  locale = "en-US",
  onOpenNote,
  onFilterTag,
}: {
  response: GraphResponse;
  locale?: "zh-CN" | "en-US";
  reason: string | null;
  onOpenNote: (path: string) => void;
  onFilterTag: (tag: string) => void;
}) {
  const tr = (zh: string,en:string) => locale === "zh-CN" ? zh : en;
  const stats = graphStats(response);
  const notes = response.nodes.filter((node) => node.type === "note");
  const tags = response.nodes.filter((node) => node.type === "tag" && node.tag_folded);
  return (
    <div className="graph-fallback" role="region" aria-label={tr("图谱列表视图","Graph fallback view")}>
      {reason && <p className="graph-fallback-reason">{tr("当前浏览器无法显示交互图谱，已切换为可点击的列表视图。",reason)}</p>}
      <dl className="graph-stats">
        <div><dt>{tr("笔记","Notes")}</dt><dd>{stats.nodes - stats.tags}</dd></div>
        <div><dt>{tr("标签","Tags")}</dt><dd>{stats.tags}</dd></div>
        <div><dt>{tr("连线","Edges")}</dt><dd>{stats.edges}</dd></div>
        <div><dt>{tr("失效","Broken")}</dt><dd>{stats.broken}</dd></div>
        <div><dt>{tr("不明确","Ambiguous")}</dt><dd>{stats.ambiguous}</dd></div>
      </dl>
      {response.page.truncated && (
        <p className="graph-fallback-hint">{tr(`当前显示 ${response.nodes.length} / ${response.page.total_nodes} 个节点。`, `Graph truncated — only the first ${response.nodes.length} nodes of ${response.page.total_nodes} are listed.`)}</p>
      )}
      <div className="graph-fallback-columns">
        <section aria-label={tr("笔记节点","Note nodes")}>
          <h4>{tr("笔记","Notes")} ({notes.length})</h4>
          {notes.length === 0 ? (
            <p className="graph-fallback-empty">{tr("当前视图没有笔记节点。","No note nodes in this view.")}</p>
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
        <section aria-label={tr("标签节点","Tag nodes")}>
          <h4>{tr("标签","Tags")} ({tags.length})</h4>
          {tags.length === 0 ? (
            <p className="graph-fallback-empty">{tr("当前视图没有标签节点。","No tag nodes in this view.")}</p>
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
