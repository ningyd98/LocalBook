# @localnote/editor

Controlled CodeMirror 6 Markdown source editor used by the M2+ workspace.

`CodeMirrorEditor` requires `value`, `onChange`, and `theme`; it also accepts
optional `readOnly`, `onSave`, and `ariaLabel` props. The defaults are editable
mode and `ariaLabel="Markdown source"`.

- Ctrl-S and Meta-S prevent the browser default and call `onSave` when supplied.
- External value, theme, and read-only changes are reflected in the editor.
- The package owns CodeMirror setup/cleanup and Markdown mode only; persistence,
  hashes, conflicts, and autosave queues belong to the workspace layer.

## Keymap, headings, attachments and live preview (M9–M12)

- A standard keymap is registered (`defaultKeymap` + `historyKeymap` +
  `indentWithTab`); without it Enter/Backspace/arrows/undo silently did nothing.
- `Ctrl/⌘+1…6` set an ATX heading, `Ctrl/⌘+0` clears it; applying the level a
  line already has toggles it back to body text. `setHeadingLevel(level)` is the
  shared command behind both the shortcuts and the H1–H6 toolbar buttons.
- `onDropFiles` / `onPasteImages` / `toolbar` / `handleRef` /
  `onRegisterCaretInsert` support attachment upload (editor drop, clipboard
  paste, toolbar picker) and let the workspace insert a reference at the caret.
- `livePreview` (from `livePreviewExt.ts`) enables the single-column WYSIWYG
  layer: heading sizes, emphasis, inline code, clickable links/wikilinks, inline
  image widgets, list/quote rules. The caret's line always keeps its raw
  Markdown; the document bytes are never rewritten.
