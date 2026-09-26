import { Icon } from "@localnote/ui";
import type { IconName } from "@localnote/ui";
import { ContextMenu } from "./ContextMenu";
import { ColorPalette, TablePicker } from "./FormatToolbar";
import type { EditorFormatCommands } from "./FormatToolbar";
import { useI18n } from "../i18n";

/** A document action shown in the lower half of the editor's right-click menu. */
export interface FormatMenuAction {
  id: string;
  label: string;
  icon: IconName;
  onSelect: () => void;
}

/**
 * Right-click menu of the editing surface.
 *
 * `ContextMenu` renders its `items` first and arbitrary `children` after them;
 * this menu needs interleaved sections (format buttons, colour swatches, table
 * grid, document actions), so it drives the shell with `children` only. Every
 * action is a real `menuitem`, which keeps the keyboard navigation, the focus
 * handling and the existing right-click tests working unchanged.
 */
export function FormatMenu({ x, y, label, commands, disabled = false, actions = [], onClose }: {
  x: number;
  y: number;
  /** Accessible name of the menu (the editor surface has its own). */
  label: string;
  commands?: EditorFormatCommands;
  /** True while the note is frozen/read-only: every entry is disabled. */
  disabled?: boolean;
  actions?: FormatMenuAction[];
  onClose: () => void;
}) {
  const { t } = useI18n();
  const apply = (command?: () => boolean) => { onClose(); command?.(); };
  const entries: { id: string; label: string; icon: IconName; run: () => void }[] = [
    { id: "bold", label: t.format.bold, icon: "bold", run: () => apply(commands?.toggleBold) },
    { id: "italic", label: t.format.italic, icon: "italic", run: () => apply(commands?.toggleItalic) },
    { id: "highlight", label: t.format.highlight, icon: "highlight", run: () => apply(commands?.toggleHighlight) },
    { id: "clear", label: t.format.clear, icon: "eraser", run: () => apply(commands?.clearFormatting) },
  ];
  return <ContextMenu x={x} y={y} label={label} items={[]} onClose={onClose}>
    {entries.map(entry => <button key={entry.id} type="button" role="menuitem" className="context-menu-entry" disabled={disabled} onClick={entry.run}>
      <Icon name={entry.icon} size={14}/><span>{entry.label}</span>
    </button>)}
    <div className="context-menu-divider"/>
    <div className="context-menu-section">{t.format.color}</div>
    <ColorPalette disabled={disabled} onPick={color => apply(() => commands?.applyTextColor(color) ?? false)}/>
    <div className="context-menu-divider"/>
    <TablePicker disabled={disabled} onPick={(rows, columns) => apply(() => commands?.insertTable(rows, columns) ?? false)}/>
    {actions.length > 0 && <>
      <div className="context-menu-divider"/>
      {actions.map(action => <button key={action.id} type="button" role="menuitem" className="context-menu-entry" onClick={() => { onClose(); action.onSelect(); }}>
        <Icon name={action.icon} size={14}/><span>{action.label}</span>
      </button>)}
    </>}
  </ContextMenu>;
}
