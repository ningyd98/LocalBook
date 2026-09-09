import type { GraphEdgeAttrs, GraphNodeAttrs } from "./types";

export type GraphTheme = "light" | "dark";

export interface ThemeColors {
  background: string;
  note: string;
  tag: string;
  link: string;
  tagEdge: string;
  backlink: string;
  broken: string;
  ambiguous: string;
  label: string;
  halo: string;
}

export const THEMES: Record<GraphTheme, ThemeColors> = {
  light: {
    background: "#fafafd",
    note: "#7750c9",
    tag: "#7c3aed",
    link: "#64748b",
    tagEdge: "#a78bfa",
    backlink: "#0d9488",
    broken: "#dc2626",
    ambiguous: "#d97706",
    label: "#292933",
    halo: "#ffffff",
  },
  dark: {
    background: "#17171c",
    note: "#aaa2c8",
    tag: "#a78bfa",
    link: "#64748b",
    tagEdge: "#a78bfa",
    backlink: "#2dd4bf",
    broken: "#f87171",
    ambiguous: "#fbbf24",
    label: "#e7e7ee",
    halo: "#17171c",
  },
};

export interface LegendEntry {
  key: string;
  label: string;
  color: string;
  lineStyle?: "solid" | "dashed" | "dotted";
  marker?: "circle" | "square";
}

/** Legend entries are exported so the UI can render the key outside canvas. */
export function legend(theme: GraphTheme): LegendEntry[] {
  const colors = THEMES[theme];
  return [
    { key: "note", label: "Note", color: colors.note, marker: "circle" },
    { key: "tag", label: "Tag", color: colors.tag, marker: "square" },
    { key: "link", label: "Wikilink", color: colors.link, lineStyle: "solid" },
    { key: "tag-edge", label: "Tag edge", color: colors.tagEdge, lineStyle: "dashed" },
    { key: "ambiguous", label: "Ambiguous link", color: colors.ambiguous, lineStyle: "dotted" },
    { key: "broken", label: "Broken link (dangling)", color: colors.broken, lineStyle: "dashed" },
  ];
}

export function nodeColor(kind: GraphNodeAttrs["kind"], theme: GraphTheme): string {
  const colors = THEMES[theme];
  return kind === "tag" ? colors.tag : colors.note;
}

export function edgeColor(attrs: GraphEdgeAttrs, theme: GraphTheme): string {
  const colors = THEMES[theme];
  if (attrs.ambiguous) return colors.ambiguous;
  if (attrs.broken) return colors.broken;
  if (attrs.kind === "tag") return colors.tagEdge;
  if (attrs.kind === "backlink") return colors.backlink;
  return colors.link;
}

export function edgeLineStyle(attrs: GraphEdgeAttrs): "solid" | "dashed" | "dotted" {
  if (attrs.ambiguous) return "dotted";
  if (attrs.broken) return "dashed";
  if (attrs.kind === "tag") return "dashed";
  return "solid";
}
