import { Button } from "@localnote/ui";

export type RibbonTool = "files" | "search" | "graph" | "ai" | "history";

const TOOLS: Array<{ label: string; id: RibbonTool }> = [
  { label: "Files", id: "files" },
  { label: "Search", id: "search" },
  { label: "Graph", id: "graph" },
  { label: "AI", id: "ai" },
  { label: "History", id: "history" },
];

export function Ribbon({ activeTool, onSelect }: { activeTool: RibbonTool; onSelect: (tool: RibbonTool) => void }) {
  return (
    <nav className="ribbon" aria-label="Workspace tools">
      {TOOLS.map(({ label, id }) => (
        <Button key={id} aria-pressed={activeTool === id} onClick={() => onSelect(id)}>
          {label}
        </Button>
      ))}
    </nav>
  );
}
