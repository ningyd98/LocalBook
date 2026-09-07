import type { GraphEdgeType, GraphNodeType } from "@localnote/protocol";

/** Graphology node attributes (backend GraphNode + styling carry-overs). */
export interface GraphNodeAttrs {
  kind: GraphNodeType;
  label: string;
  path: string | null;
  title: string | null;
  tag: string | null;
  tagFolded: string | null;
}

/** Graphology edge attributes (backend GraphEdge + styling carry-overs). */
export interface GraphEdgeAttrs {
  kind: GraphEdgeType;
  raw: string | null;
  broken: boolean;
  ambiguous: boolean;
  candidates: string[];
  section: string | null;
  block: string | null;
}

/** Result of clicking an interactive element. */
export interface GraphClickTarget {
  id: string;
  type: GraphNodeType;
  path: string | null;
  tag: string | null;
  label: string;
}

export interface GraphStats {
  notes: number;
  tags: number;
  links: number;
  broken: number;
  ambiguous: number;
  truncated: boolean;
}
