import {useI18n} from "../i18n";
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
  const {tr,locale,errorText} = useI18n();
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
    <section className="graph-panel" aria-label={tr("知识图谱","Knowledge graph")}>
      <header className="graph-panel-header">
        <h2>{tr("知识图谱","Graph")}</h2>
        <span className="graph-panel-root">
          {graph.scope === "local" && graph.response?.root
            ? `${tr("中心笔记", "Root")}: ${graph.response.root}`
            : graph.scope === "tag" && graph.tag
              ? `${tr("标签", "Tag")}: ${graph.tag}`
              : tr("探索笔记之间的连接","global note/tag graph")}
        </span>
        <Button onClick={onClose} aria-label={tr("关闭图谱视图","Close graph view")}>
          ×
        </Button>
      </header>

      <details className="graph-filters" open><summary>{tr("视图与筛选","View & filters")}</summary><GraphControls
        initial={graph}
        activeNote={activePath}
        loading={graph.status === "loading"}
        onApply={applyControls}
      /></details>

      {graph.status === "loading" && <p role="status" className="graph-state">{tr("正在加载知识图谱…","Loading graph\u2026")}</p>}
      {graph.status === "error" && (
        <p role="alert" className="graph-error">
          {errorText(graph.error)}
          <Button onClick={() => applyControls({ scope: graph.scope, note: graph.note, tag: graph.tag, depth: graph.depth, direction: graph.direction, includeBroken: graph.includeBroken, limit: graph.limit, offset: graph.offset })}>
            {tr("重试", "Retry")}
          </Button>
        </p>
      )}
      {graph.status === "unavailable" && (
        <p role="alert" className="graph-error graph-error-unavailable">
          {tr("派生索引暂不可用，请在设置中重建索引后重试。", "The derived index is unavailable — rebuild it on the server, then retry.")} 
          <Button onClick={() => applyControls({ scope: graph.scope, note: graph.note, tag: graph.tag, depth: graph.depth, direction: graph.direction, includeBroken: graph.includeBroken, limit: graph.limit, offset: graph.offset })}>
            {tr("重试", "Retry")}
          </Button>
        </p>
      )}
      {graph.status === "empty" && (
        <p className="graph-state">{tr("当前视图中没有匹配的笔记或标签。","No notes or tags match this view.")}</p>
      )}
      {graph.status === "ready" && graph.response && (
        <>
          <div className="graph-meta">
            <span role="status" aria-live="polite">
              {stats && `${stats.nodes} ${tr("节点","nodes")} · ${stats.edges} ${tr("连线","edges")}${stats.broken > 0 ? ` · ${stats.broken} ${tr("失效","broken")}` : ""}${stats.ambiguous > 0 ? ` · ${stats.ambiguous} ${tr("不明确","ambiguous")}` : ""}`}
            </span>
            {graph.response.page.truncated && nextOffset !== null && (
              <Button
                className="graph-load-more"
                onClick={() => void loadGraph({ offset: nextOffset })}
              >
                {tr(`加载更多（${graph.response.nodes.length} / ${graph.response.page.total_nodes} 节点）`, `Load more (${graph.response.nodes.length} of ${graph.response.page.total_nodes} nodes shown)`)}
              </Button>
            )}
            <Button
              className="graph-refresh"
              onClick={() => void loadGraph({})}
            >
              {tr("刷新", "Refresh")}
            </Button>
          </div>
          <ul className="graph-legend" aria-label={tr("图谱图例","Graph legend")}>
            {legendItems.map((item) => (
              <li key={item.key} className="graph-legend-item">
                <span
                  className={`graph-swatch graph-swatch-${item.key}`}
                  style={{ background: item.color }}
                  aria-hidden="true"
                />
                {locale === "zh-CN" ? ({note:"笔记",tag:"标签",link:"笔记链接","tag-edge":"标签关联",ambiguous:"不明确的链接",broken:"失效链接"} as Record<string,string>)[item.key] ?? item.label : item.label}
              </li>
            ))}
          </ul>
          <SigmaGraph locale={locale}
            response={graph.response}
            theme={theme === "dark" ? "dark" : "light"}
            onNodeClick={handleNodeClick}
          />
        </>
      )}
      {graph.status === "idle" && graph.response === null && (
        <p className="graph-state">{tr("选择图谱范围，然后应用筛选。","Choose a view and press Apply.")}</p>
      )}
    </section>
  );
}
