export type EditorMode = "source" | "live" | "preview" | "split";
export interface WorkspacePreferences {
  theme: "light" | "dark";
  editorMode: EditorMode;
  sidebarWidth: number;
  inspectorWidth: number;
  sidebarOpen: boolean;
  autoSave: boolean;
  autoSaveDelay: number;
  splitRatio: number;
}
export const preferenceDefaults: WorkspacePreferences = { theme: "dark", editorMode: "source", sidebarWidth: 248, inspectorWidth: 320, sidebarOpen: true, autoSave: true, autoSaveDelay: 800, splitRatio: 50 };
export function validatePreferences(input: Partial<WorkspacePreferences>): WorkspacePreferences {
  const p = { ...preferenceDefaults, ...input };
  const bounded = (v: number, min: number, max: number, fallback: number) => Number.isFinite(v) ? Math.max(min, Math.min(max, Math.round(v))) : fallback;
  return { theme: p.theme === "light" ? "light" : "dark", editorMode: ["source", "live", "preview", "split"].includes(p.editorMode) ? p.editorMode : "source", sidebarOpen: p.sidebarOpen !== false, autoSave: p.autoSave !== false, autoSaveDelay: bounded(p.autoSaveDelay, 100, 5000, 800), sidebarWidth: bounded(p.sidebarWidth, 200, 420, 248), inspectorWidth: bounded(p.inspectorWidth, 280, 480, 320), splitRatio: bounded(p.splitRatio, 20, 80, 50) };
}
export function readPreferences(): WorkspacePreferences {
  try { return validatePreferences(JSON.parse(localStorage.getItem("localnote-preferences") ?? "{}")); }
  catch { return { ...preferenceDefaults }; }
}
export function persistPreferences(value: WorkspacePreferences) {
  try { localStorage.setItem("localnote-preferences", JSON.stringify(validatePreferences(value))); } catch { /* private browsing and full storage still allow editing */ }
}
