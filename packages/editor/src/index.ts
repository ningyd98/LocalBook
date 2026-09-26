export { CodeMirrorEditor } from "./CodeMirrorEditor";
export type { CodeMirrorEditorProps, CodeMirrorEditorHandle } from "./CodeMirrorEditor";
export { livePreview, buildDecorations as buildLivePreviewDecorations } from "./livePreviewExt";
export type { LivePreviewOptions, LivePreviewResolver } from "./livePreviewExt";
export {
  DEFAULT_TABLE,
  TABLE_MAX_COLUMNS,
  TABLE_MAX_ROWS,
  TEXT_COLORS,
  applyTextColor,
  buildTableMarkdown,
  clearFormatting,
  insertTable,
  normalizeColor,
  toggleBold,
  toggleHighlight,
  toggleItalic,
  toggleWrap,
} from "./formatting";
export type { TextColorOption } from "./formatting";
