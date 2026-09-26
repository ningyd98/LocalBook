import {useI18n} from "../i18n";
import { useEffect, useMemo, useState } from "react";
import { Button } from "@localnote/ui";
import { connectedGraph, legend, SigmaGraph, graphStats } from "@localnote/graph";
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
  const [showAllNodes, setShowAllNodes] = useState(false);

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
  const focusedResponse = useMemo(
    () => graph.response ? connectedGraph(graph.response) : null,
    [graph.response],
  );
  const connectedIds = useMemo(
    () => new Set(focusedResponse?.nodes.map((node) => node.id) ?? []),
    [focusedResponse],
  );
  const isolatedNotes = graph.response?.nodes.filter(
    (node) => node.type === "note" && !connectedIds.has(node.id),
  ).length ?? 0;
  const canFocus = graph.scope === "global" && isolatedNotes > 0;
  const displayedResponse = canFocus && !showAllNodes ? focusedResponse : graph.response;
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
          <Button onClick={() => applyControls({ scope: graph.scope, note: graph.note, tag: graph.tag, depth: graph.depth, direction: graph.direction, includeBroken: graph.includeBroken, includeSemantic: graph.includeSemantic, limit: graph.limit, offset: graph.offset })}>
            {tr("重试", "Retry")}
          </Button>
        </p>
      )}
      {graph.status === "unavailable" && (
        <p role="alert" className="graph-error graph-error-unavailable">
          {tr("派生索引暂不可用，请在设置中重建索引后重试。", "The derived index is unavailable — rebuild it on the server, then retry.")} 
          <Button onClick={() => applyControls({ scope: graph.scope, note: graph.note, tag: graph.tag, depth: graph.depth, direction: graph.direction, includeBroken: graph.includeBroken, includeSemantic: graph.includeSemantic, limit: graph.limit, offset: graph.offset })}>
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
            {stats && stats.semantic > 0 && <span>{stats.semantic} {tr("条内容候选", "content similarities")}</span>}
            {canFocus && (
              <Button className="graph-view-toggle" onClick={() => setShowAllNodes((value) => !value)}>
                {showAllNodes
                  ? tr(`只看有关联的节点（${focusedResponse?.nodes.length ?? 0}）`, `Show connected notes (${focusedResponse?.nodes.length ?? 0})`)
                  : tr(`显示全部笔记（${graph.response.nodes.length}）`, `Show all notes (${graph.response.nodes.length})`)}
              </Button>
            )}
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
          <p className="graph-insight">
            {canFocus && `${isolatedNotes} ${tr("篇笔记没有可绘制的关联。", "isolated notes have no drawable relation. ")}`}
            {graph.includeSemantic === false
              ? tr("内容关联已关闭；当前只显示明确引用和标签。", "Content similarities are off; only explicit links and tags are shown.")
              : graph.response.semantic_status === "ready"
                ? tr("青色连线是根据笔记内容生成的相近候选，不代表已确认的引用。", "Teal lines are content similarity suggestions, not confirmed references.")
                : graph.response.semantic_status === "outdated"
                  ? tr("内容向量已过期，请先更新知识库索引。", "Content vectors are outdated; update the knowledge index to show similarities.")
                  : graph.response.semantic_status === "degraded"
                    ? tr("当前内容向量质量不足，暂不绘制内容关联。", "Content vectors are degraded, so content similarities are hidden.")
                    : graph.response.semantic_status === "limited"
                      ? tr("当前页面超过内容分析上限，请缩小节点范围。", "This page exceeds the content analysis limit; narrow the graph scope.")
                      : tr("尚无可用的内容向量；当前只显示明确引用和标签。", "No usable content vectors are available; only explicit links and tags are shown.")}
          </p>
          <ul className="graph-legend" aria-label={tr("图谱图例","Graph legend")}>
            {legendItems.map((item) => (
              <li key={item.key} className="graph-legend-item">
                <span
                  className={`graph-swatch graph-swatch-${item.key}`}
                  style={{ background: item.color }}
                  aria-hidden="true"
                />
                {locale === "zh-CN" ? ({note:"笔记",tag:"标签",link:"笔记链接",semantic:"内容相近候选","tag-edge":"标签关联",ambiguous:"不明确的链接",broken:"失效链接"} as Record<string,string>)[item.key] ?? item.label : item.label}
              </li>
            ))}
          </ul>
          {displayedResponse && displayedResponse.nodes.length > 0
            ? <SigmaGraph locale={locale}
                response={displayedResponse}
                theme={theme === "dark" ? "dark" : "light"}
                onNodeClick={handleNodeClick}
              />
            : <p className="graph-state">{tr("暂无可绘制的关联；可显示全部笔记查看节点。", "No drawable relations yet. Show all notes to inspect the nodes.")}</p>}
        </>
      )}
      {graph.status === "idle" && graph.response === null && (
        <p className="graph-state">{tr("选择图谱范围，然后应用筛选。","Choose a view and press Apply.")}</p>
      )}
    </section>
  );
}
