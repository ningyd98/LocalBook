import { useCallback, useEffect, useRef, useState } from "react";
import type { GraphNode, GraphResponse } from "@localnote/protocol";
import { GraphFallback } from "./fallback";
import { buildGraphologyGraph, protocolNodes } from "./model";
import { edgeColor, nodeColor, THEMES } from "./styles";
import type { GraphTheme } from "./styles";
import type { GraphClickTarget, GraphEdgeAttrs, GraphNodeAttrs } from "./types";

/**
 * Sigma/WebGL renderer with a mandatory fallback path.
 *
 * - Graphology/Sigma are imported **inside the mount effect** (dynamic
 *   import) so a broken WebGL context or a failed renderer construction can
 *   never blank the panel: it falls back to GraphFallback with the reason.
 * - The renderer is created in an effect and always ``kill()``ed on unmount;
 *   ResizeObserver and event listeners are cleaned up the same way
 *   (PLAN-M5 §6.2).
 * - Node/edge visuals come from the shared style tokens; line styles as well
 *   as colours distinguish link/tag/broken/ambiguous.
 * - Layout is UI-only memory state (force-atlas on typical scopes, circular
 *   above 1200 nodes) and is never persisted or sent back.
 */

interface SigmaLike {
  on: (event: string, listener: (payload: { node: string }) => void) => void;
  kill: () => void;
  refresh?: () => void;
}

interface ForceAtlasLike {
  assign?: (graph: unknown, options: Record<string, unknown>) => void;
  (graph: unknown, options: Record<string, unknown>): unknown;
}

/** Async loader of the Sigma module (real import by default; test seam). */
type RendererLoader = () => Promise<{ default: unknown }>;
const defaultLoader: RendererLoader = async () => import("sigma");

export function SigmaGraph({
  response,
  theme,
  locale = "en-US",
  onNodeClick,
  className,
  loadRenderer = defaultLoader,
}: {
  response: GraphResponse;
  locale?: "zh-CN" | "en-US";
  theme: GraphTheme;
  onNodeClick?: (target: GraphClickTarget) => void;
  className?: string;
  loadRenderer?: RendererLoader;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [fallbackReason, setFallbackReason] = useState<string | null>(null);
  const nodesById = useRef<Map<string, GraphNode>>(new Map());
  nodesById.current = protocolNodes(response);

  const openByNode = useCallback(
    (node: GraphNode) => {
      onNodeClick?.({
        id: node.id,
        type: node.type,
        path: node.path,
        tag: node.tag,
        label: node.label,
      });
    },
    [onNodeClick],
  );
  const openNoteByPath = useCallback(
    (path: string) => {
      const node = [...nodesById.current.values()].find((item) => item.path === path);
      if (node) openByNode(node);
    },
    [openByNode],
  );
  const filterTag = useCallback(
    (tag: string) => {
      const node = [...nodesById.current.values()].find((item) => item.tag === tag);
      if (node) openByNode(node);
    },
    [openByNode],
  );
  const openNode = useCallback(
    (id: string) => {
      const node = nodesById.current.get(id);
      if (node) openByNode(node);
    },
    [openByNode],
  );

  useEffect(() => {
    setFallbackReason(null);
    let disposed = false;
    let observer: ResizeObserver | null = null;
    let renderer: SigmaLike | null = null;
    const container = containerRef.current;

    if (!container || response.nodes.length === 0) {
      // Empty graphs are handled by the parent's empty state; nothing to draw.
      return () => undefined;
    }
    if (!webglAvailable()) {
      setFallbackReason(
        "WebGL is not available in this browser — showing the interactive list fallback.",
      );
      return () => undefined;
    }

    void (async () => {
      try {
        const sigmaModule = await loadRenderer();
        const SigmaCtor = sigmaModule.default as unknown as new (
          graph: unknown,
          container: HTMLElement,
          options?: Record<string, unknown>,
        ) => SigmaLike;
        const layoutModule = (await import("graphology-layout-forceatlas2")) as unknown as {
          default?: ForceAtlasLike;
          assign?: (graph: unknown, options: Record<string, unknown>) => void;
        };
        const forceAtlas2: ForceAtlasLike = (layoutModule.default ??
          layoutModule) as ForceAtlasLike;

        if (disposed) return;
        const graph = buildGraphologyGraph(response);
        
        // P2-2: Handle single-node graphs explicitly (center at origin)
        if (graph.order === 1) {
          const nodeId = graph.nodes()[0];
          if (nodeId) {
            graph.setNodeAttribute(nodeId, 'x', 0);
            graph.setNodeAttribute(nodeId, 'y', 0);
          }
        } else if (graph.order > 1) {
          // Compact, deterministic seeds prevent disconnected notes from
          // dominating the fitted viewport before the force layout settles.
          const ordered = graph.nodes().sort();
          ordered.forEach((id, index) => {
            const angle = index / ordered.length * Math.PI * 2;
            const radius = Math.sqrt(ordered.length);
            graph.mergeNodeAttributes(id, {x: Math.cos(angle) * radius, y: Math.sin(angle) * radius});
          });
          // Multi-node graphs: run layout
          if (graph.order <= 1200) {
            const assign = forceAtlas2.assign;
            if (assign) {
              assign(graph, {
                iterations: 120,
                settings: { strongGravityMode: true, gravity: 0.2, scalingRatio: 10, slowDown: 1 + Math.log(graph.order), barnesHutOptimize: graph.order > 500 },
              });
            }
          } else {
            const circularModule = (await import("graphology-layout")) as {
              default?: unknown;
              circular?: { assign: (graph: unknown) => void };
            };
            const circular = (circularModule.circular ??
              circularModule.default) as { assign: (graph: unknown) => void } | undefined;
            circular?.assign(graph);
          }
        }
        // else: graph.order === 0, empty graph (shouldn't reach here due to early return)
        if (disposed) return;
        const nodesTotal = response.nodes.length;
        const palette = theme;
        renderer = new SigmaCtor(graph, container, {
          backgroundColor: THEMES[palette].background,
          labelColor: {color: THEMES[palette].label},
          labelRenderedSizeThreshold: nodesTotal > 800 ? 18 : 0,
          labelSize: 12,
          labelFont: "system-ui, sans-serif",
          labelDensity: 1,
          labelGridCellSize: 90,
          stagePadding: 55,
          labelsOnHover: nodesTotal <= 1000,
          hideEdgesOnMove: nodesTotal > 1000,
          nodeReducer: (_nodeId: string, data: Record<string, unknown>) => {
            const attrs = data as unknown as GraphNodeAttrs;
            return {
              ...data,
              color: nodeColor(attrs.kind, palette),
              label: attrs.label,
              size: attrs.kind === "tag" ? 4.5 : 6,
            };
          },
          edgeReducer: (_edgeId: string, data: Record<string, unknown>) => {
            const attrs = data as unknown as GraphEdgeAttrs;
            return {
              ...data,
              color: edgeColor(attrs, palette),
              size: 0.6,
              type: "line",
            };
          },
        } as Record<string, unknown>);
        renderer.on("clickNode", ({ node }) => openNode(node));
        if (typeof ResizeObserver !== "undefined") {
          observer = new ResizeObserver(() => renderer?.refresh?.());
          observer.observe(container);
        }
      } catch (error) {
        if (disposed) return;
        setFallbackReason(
          error instanceof Error
            ? `Interactive graph could not start: ${error.message}`
            : "Interactive graph could not start",
        );
      }
    })();

    return () => {
      disposed = true;
      observer?.disconnect();
      renderer?.kill();
      renderer = null;
    };
  }, [response, theme, openNode, loadRenderer]);

  return (
    <div className={`graph-canvas-wrap ${className ?? ""}`}>
      <div
        ref={containerRef}
        className="graph-canvas"
        aria-label={locale === "zh-CN" ? `知识图谱：${response.nodes.length} 个节点，${response.edges.length} 条连线` : `Graph view of ${response.nodes.length} nodes and ${response.edges.length} edges`}
      />
      {fallbackReason !== null && (
        <GraphFallback locale={locale}
          response={response}
          reason={fallbackReason}
          onOpenNote={openNoteByPath}
          onFilterTag={filterTag}
        />
      )}
    </div>
  );
}

function webglAvailable(): boolean {
  if (typeof document === "undefined" || typeof window === "undefined") return false;
  try {
    const canvas = document.createElement("canvas");
    const gl =
      canvas.getContext("webgl2") ||
      canvas.getContext("webgl") ||
      canvas.getContext("experimental-webgl");
    return gl !== null;
  } catch {
    return false;
  }
}
