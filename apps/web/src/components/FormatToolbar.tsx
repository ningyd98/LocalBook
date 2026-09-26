import { useEffect, useRef, useState } from "react";
import { DEFAULT_TABLE, TABLE_MAX_COLUMNS, TABLE_MAX_ROWS, TEXT_COLORS } from "@localnote/editor";
import { useI18n } from "../i18n";

/**
 * The formatting surface exposed by a mounted editor. The editor pane owns the
 * CodeMirror handle, so the toolbar and the right-click menu talk to the live
 * view through this small command object instead of reaching into the editor.
 */
export interface EditorFormatCommands {
  toggleBold: () => boolean;
  toggleItalic: () => boolean;
  toggleHighlight: () => boolean;
  applyTextColor: (color: string) => boolean;
  clearFormatting: () => boolean;
  insertTable: (rows: number, columns: number) => boolean;
}

/** Bounds of the picker grid offered in the menus (also a mis-click guard). */
const PICKER_ROWS = 6;
const PICKER_COLUMNS = 6;

/**
 * Inline-format toolbar: bold, italic, highlight, colour and clear.
 *
 * Every button acts on the current selection (or inserts the pair at the
 * caret), matching the Ctrl-B / Ctrl-I / Ctrl-Shift-H shortcuts.
 */
export function FormatToolbar({ commands, disabled = false }: { commands: EditorFormatCommands; disabled?: boolean }) {
  const { t } = useI18n();
  const [colorsOpen, setColorsOpen] = useState(false);
  const colorsRef = useRef<HTMLDivElement>(null);

  // A popover anchored to its own button: close on outside click or Escape,
  // never on a click inside it (that is how a colour gets picked).
  useEffect(() => {
    if (!colorsOpen) return;
    const outside = (event: MouseEvent) => { if (!colorsRef.current?.contains(event.target as Node)) setColorsOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setColorsOpen(false); };
    document.addEventListener("mousedown", outside);
    window.addEventListener("keydown", escape);
    return () => { document.removeEventListener("mousedown", outside); window.removeEventListener("keydown", escape); };
  }, [colorsOpen]);

  return <div className="format-toolbar" role="group" aria-label={t.format.toolbar}>
    <button type="button" className="format-button format-bold" title={t.format.bold} aria-label={t.format.bold} disabled={disabled} onClick={() => commands.toggleBold()}>B</button>
    <button type="button" className="format-button format-italic" title={t.format.italic} aria-label={t.format.italic} disabled={disabled} onClick={() => commands.toggleItalic()}>I</button>
    <button type="button" className="format-button format-highlight" title={t.format.highlight} aria-label={t.format.highlight} disabled={disabled} onClick={() => commands.toggleHighlight()}>H</button>
    <div className="format-color" ref={colorsRef}>
      <button type="button" className="format-button format-color-button" title={t.format.color} aria-label={t.format.color} aria-expanded={colorsOpen} disabled={disabled} onClick={() => setColorsOpen(open => !open)}>
        A<span className="format-color-underline"/>
      </button>
      {colorsOpen && <ColorPalette disabled={disabled} onPick={color => { setColorsOpen(false); commands.applyTextColor(color); }}/>}
    </div>
    <button type="button" className="format-button format-clear" title={t.format.clear} aria-label={t.format.clear} disabled={disabled} onClick={() => commands.clearFormatting()}>⌫</button>
  </div>;
}

/** The colour swatches. Labels are localized next to the palette they describe. */
export function ColorPalette({ onPick, disabled = false }: { onPick: (color: string) => void; disabled?: boolean }) {
  const { tr } = useI18n();
  const names: Record<string, [string, string]> = {
    red: ["红色", "Red"], orange: ["橙色", "Orange"], yellow: ["黄色", "Yellow"], green: ["绿色", "Green"],
    blue: ["蓝色", "Blue"], purple: ["紫色", "Purple"], gray: ["灰色", "Gray"],
  };
  return <div className="color-palette" role="group" aria-label={tr("文字颜色", "Text colour")}>
    {TEXT_COLORS.map(option => {
      const [zh, en] = names[option.id] ?? [option.id, option.id];
      const label = tr(`${zh}文字`, `${en} text`);
      return <button key={option.id} type="button" className="color-swatch" style={{ background: option.hex }} title={label} aria-label={label} disabled={disabled} onClick={() => onPick(option.hex)}/>;
    })}
  </div>;
}

/**
 * Word-style table grid: hovering a cell previews its size, clicking inserts a
 * table with that many rows (header included) and columns.
 */
export function TablePicker({ onPick, disabled = false }: { onPick: (rows: number, columns: number) => void; disabled?: boolean }) {
  const { tr } = useI18n();
  const [size, setSize] = useState<{ rows: number; columns: number }>({ rows: DEFAULT_TABLE.rows, columns: DEFAULT_TABLE.columns });
  const rows = Math.min(PICKER_ROWS, TABLE_MAX_ROWS);
  const columns = Math.min(PICKER_COLUMNS, TABLE_MAX_COLUMNS);
  return <div className="table-picker">
    <div className="table-picker-label" aria-live="polite">{tr("插入表格", "Insert table")} · {size.rows} × {size.columns}</div>
    <div className="table-picker-grid" role="group" aria-label={tr("选择表格行列数", "Choose table size")}
      onMouseLeave={() => setSize({ rows: DEFAULT_TABLE.rows, columns: DEFAULT_TABLE.columns })}>
      {Array.from({ length: rows }, (_, rowIndex) => <div key={rowIndex} className="table-picker-row">
        {Array.from({ length: columns }, (_, columnIndex) => {
          const row = rowIndex + 1;
          const column = columnIndex + 1;
          const active = row <= size.rows && column <= size.columns;
          return <button key={column} type="button" className={`table-picker-cell${active ? " active" : ""}`}
            aria-label={tr(`${row} 行 ${column} 列表格`, `${row} × ${column} table`)}
            disabled={disabled}
            onMouseEnter={() => setSize({ rows: row, columns: column })}
            onFocus={() => setSize({ rows: row, columns: column })}
            onClick={() => onPick(row, column)}/>;
        })}
      </div>)}
    </div>
  </div>;
}
