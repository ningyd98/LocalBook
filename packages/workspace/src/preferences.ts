export type EditorMode = "source" | "live" | "preview" | "split";
/** Font stacks offered in Settings → Appearance. All are local; no web fonts. */
export type FontFamily = "sans" | "serif" | "mono" | "rounded";
/** Which surfaces the chosen family applies to (sizes always scale globally). */
export type FontTarget = "all" | "note";

export interface WorkspacePreferences {
  theme: "light" | "dark";
  editorMode: EditorMode;
  sidebarWidth: number;
  inspectorWidth: number;
  sidebarOpen: boolean;
  autoSave: boolean;
  autoSaveDelay: number;
  splitRatio: number;
  /** Named stack used by the UI and/or the note body. */
  fontFamily: FontFamily;
  /** Global text scale in percent (`100` = the default 13px UI font). */
  fontScale: number;
  /** `all` re-fonts the whole app, `note` keeps the chrome and re-fonts notes. */
  fontTarget: FontTarget;
  /** Split view: keep the editor and the preview at the same position. */
  syncScroll: boolean;
}

/**
 * Font stacks. The CJK fallbacks matter here: a serif or mono choice must not
 * silently drop Chinese text to a browser default, so each stack lists system
 * CJK faces after the Latin ones.
 */
export const fontStacks: Record<FontFamily, string> = {
  sans: '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif',
  serif: 'Georgia, Cambria, "Times New Roman", "Songti SC", "Noto Serif CJK SC", "SimSun", serif',
  mono: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "PingFang SC", "Microsoft YaHei", monospace',
  rounded: 'ui-rounded, "SF Pro Rounded", "Segoe UI Variable", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif',
};
/** Kept for the editor when the chosen family targets notes only. */
export const editorStack = 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "PingFang SC", "Microsoft YaHei", monospace';

export const fontScaleRange = { min: 80, max: 160, step: 5 } as const;
/** Presets shown as buttons; the slider stays available for anything between. */
export const fontScalePresets = [90, 100, 115, 130] as const;

export const preferenceDefaults: WorkspacePreferences = { theme: "dark", editorMode: "source", sidebarWidth: 248, inspectorWidth: 320, sidebarOpen: true, autoSave: true, autoSaveDelay: 800, splitRatio: 50, fontFamily: "sans", fontScale: 100, fontTarget: "all", syncScroll: true };

/**
 * The CSS custom properties the stylesheet scales itself with. Every text size
 * in `styles.css` is one of these tokens, so changing `--font-scale` resizes the
 * whole interface — sidebar, tabs, panels, preview and editor alike.
 */
export function fontScaleVars(fontScale: number): Record<string, string> {
  const base = Math.max(fontScaleRange.min, Math.min(fontScaleRange.max, fontScale)) / 100;
  const px = (size: number) => `${Math.round(size * base * 100) / 100}px`;
  return {
    "--font-scale": String(base),
    "--fs-3xs": px(8),
    "--fs-2xs": px(9),
    "--fs-xs": px(10),
    "--fs-sm": px(11),
    "--fs-md": px(12),
    "--fs-base": px(13),
    "--fs-lg": px(14),
    "--fs-xl": px(15),
    "--fs-2xl": px(16),
    "--fs-3xl": px(18),
    "--fs-4xl": px(22),
    "--fs-5xl": px(29),
  };
}

export function validatePreferences(input: Partial<WorkspacePreferences>): WorkspacePreferences {
  const p = { ...preferenceDefaults, ...input };
  const bounded = (v: number, min: number, max: number, fallback: number) => Number.isFinite(v) ? Math.max(min, Math.min(max, Math.round(v))) : fallback;
  return { theme: p.theme === "light" ? "light" : "dark", editorMode: ["source", "live", "preview", "split"].includes(p.editorMode) ? p.editorMode : "source", sidebarOpen: p.sidebarOpen !== false, autoSave: p.autoSave !== false, autoSaveDelay: bounded(p.autoSaveDelay, 100, 5000, 800), sidebarWidth: bounded(p.sidebarWidth, 200, 420, 248), inspectorWidth: bounded(p.inspectorWidth, 280, 480, 320), splitRatio: bounded(p.splitRatio, 20, 80, 50), fontFamily: (["sans", "serif", "mono", "rounded"] as const).includes(p.fontFamily) ? p.fontFamily : "sans", fontScale: bounded(p.fontScale, fontScaleRange.min, fontScaleRange.max, 100), fontTarget: p.fontTarget === "note" ? "note" : "all", syncScroll: p.syncScroll !== false };
}
/**
 * Seed font preferences from the URL (`?fontScale=130&fontFamily=serif`).
 *
 * Only the font trio is accepted: a link can share "how big I read" without
 * being able to rewrite vault, theme or autosave state, and out-of-range values
 * fall back to the stored preferences. Called once at startup, before the store
 * is created, so the first render already uses the requested scale.
 */
export function applyFontQuery(preferences: WorkspacePreferences, search: string): WorkspacePreferences {
  const query = new URLSearchParams(search);
  const scale = query.get("fontScale");
  const family = query.get("fontFamily");
  const target = query.get("fontTarget");
  const next: Partial<WorkspacePreferences> = {};
  if (scale !== null && Number.isFinite(Number(scale))) next.fontScale = Number(scale);
  if (family && ["sans", "serif", "mono", "rounded"].includes(family)) next.fontFamily = family as FontFamily;
  if (target === "note" || target === "all") next.fontTarget = target;
  return Object.keys(next).length ? validatePreferences({ ...preferences, ...next }) : preferences;
}

export function readPreferences(): WorkspacePreferences {
  try { return validatePreferences(JSON.parse(localStorage.getItem("localnote-preferences") ?? "{}")); }
  catch { return { ...preferenceDefaults }; }
}
export function persistPreferences(value: WorkspacePreferences) {
  try { localStorage.setItem("localnote-preferences", JSON.stringify(validatePreferences(value))); } catch { /* private browsing and full storage still allow editing */ }
}
