import Graph from "graphology";
import type { GraphEdge, GraphNode, GraphResponse } from "@localnote/protocol";
import type { GraphEdgeAttrs, GraphNodeAttrs } from "./types";

/**
 * Pure DTO -> Graphology conversion (PLAN-M5 §6.2).
 *
 * - Node keys are the backend's opaque stable IDs, so Sigma click events map
 *   straight back to protocol nodes/edges.
 * - ``multi: true`` keeps every backend edge distinct; edge keys are the
 *   backend edge IDs (link IDs embed the M4 ``seq`` so duplicated links from
 *   one note never collapse).
 * - Dangling edges (``target === ""`` — broken/ambiguous refs with no
 *   resolved note) have no second endpoint and **cannot** be added to a
 *   graph; they are skipped here and surfaced through
 *   :func:`graphStats`/the fallback list instead.  No virtual node is ever
 *   fabricated.
 * - P2-2 fix: Every node receives deterministic initial x/y coordinates
 *   (hash-based) so Sigma can render even when layout libraries fail or are
 *   skipped (single-node graphs).
 */
export function buildGraphologyGraph(response: GraphResponse): Graph<GraphNodeAttrs, GraphEdgeAttrs> {
  const graph = new Graph<GraphNodeAttrs, GraphEdgeAttrs>({ multi: true });
  for (const node of response.nodes) addNode(graph, node);
  for (const edge of response.edges) addEdge(graph, edge);
  return graph;
}

/** Simple string hash for deterministic coordinate generation. */
function simpleHash(str: string): number {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    hash = ((hash << 5) - hash) + str.charCodeAt(i);
    hash = hash & hash; // Convert to 32bit integer
  }
  return Math.abs(hash);
}

function addNode(graph: Graph<GraphNodeAttrs, GraphEdgeAttrs>, node: GraphNode): void {
  if (graph.hasNode(node.id)) return; // a repeated id is a server bug; never crash
  
  // P2-2: Generate deterministic initial coordinates based on node ID
  const hash = simpleHash(node.id);
  const x = (hash % 1000) - 500;  // Range: [-500, 500]
  const y = ((hash >> 10) % 1000) - 500;
  
  graph.addNode(node.id, {
    kind: node.type,
    label: node.label,
    path: node.path,
    title: node.title,
    tag: node.tag,
    tagFolded: node.tag_folded,
    x,
    y,
  });
}

function addEdge(graph: Graph<GraphNodeAttrs, GraphEdgeAttrs>, edge: GraphEdge): void {
  if (edge.target === "") return; // dangling (broken/ambiguous) — no endpoint
  if (!graph.hasNode(edge.source) || !graph.hasNode(edge.target)) return;
  if (graph.hasEdge(edge.id)) return; // duplicate id is a server bug; never crash
  graph.addEdgeWithKey(edge.id, edge.source, edge.target, {
    kind: edge.type,
    raw: edge.raw,
    broken: edge.broken,
    ambiguous: edge.ambiguous,
    candidates: edge.candidates,
    section: edge.section,
    block: edge.block,
  });
}

export function protocolNodes(response: GraphResponse): Map<string, GraphNode> {
  const byId = new Map<string, GraphNode>();
  for (const node of response.nodes) byId.set(node.id, node);
  return byId;
}

/** Counts shown in the legend / fallback / stats line (incl. dangling). */
export function graphStats(response: GraphResponse): {
  nodes: number;
  edges: number;
  broken: number;
  ambiguous: number;
  tags: number;
} {
  let broken = 0;
  let ambiguous = 0;
  let tags = 0;
  for (const node of response.nodes) if (node.type === "tag") tags += 1;
  for (const edge of response.edges) {
    if (edge.type === "tag") continue;
    if (edge.broken) broken += 1;
    if (edge.ambiguous) ambiguous += 1;
  }
  return {
    nodes: response.nodes.length,
    edges: response.edges.length,
    broken,
    ambiguous,
    tags,
  };
}
