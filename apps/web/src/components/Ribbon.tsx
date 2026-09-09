import { Icon, IconButton } from "@localnote/ui";
import { useI18n } from "../i18n";
export type RibbonTool = "files" | "search" | "graph" | "ai" | "history" | "settings";
export function Ribbon({ activeTool, onSelect }: { activeTool: RibbonTool; onSelect: (tool: RibbonTool) => void }) {
  const { t, tr } = useI18n();
  return <nav className="ribbon" aria-label={tr("工作区工具", "Workspace tools")}>
    <div className="brand-mark" aria-label="LocalNote"><svg viewBox="0 0 32 38" width="25" height="30" fill="none" aria-hidden="true"><path d="m17 2 12 10-5 22-11 2L3 23 8 7z" fill="currentColor" opacity=".23"/><path d="m17 2-3 16 10 16M8 7l6 11L3 23m11-5 15-6" stroke="currentColor" strokeWidth="1.6"/><path d="m17 2 12 10-5 22-11 2L3 23 8 7z" stroke="currentColor" strokeWidth="1.5"/></svg></div>
    <div className="ribbon-tools">{(["files", "search", "graph", "ai", "history"] as const).map(id => <IconButton key={id} title={id === "history" ? tr("任务与历史", "Tasks & history") : t.ribbon[id]} aria-label={t.ribbon[id]} aria-pressed={activeTool === id} onClick={() => onSelect(id)}><Icon name={id}/><span>{id === "history" ? tr("任务", "Tasks") : t.ribbon[id]}</span></IconButton>)}</div>
    <IconButton className="ribbon-settings" title={t.settings.title} aria-label={t.settings.title} aria-pressed={activeTool === "settings"} onClick={() => onSelect("settings")}><Icon name="settings"/><span>{t.settings.title}</span></IconButton>
  </nav>;
}
