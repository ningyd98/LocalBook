import { useMemo } from "react";
import type { ReactNode } from "react";
import { Icon, IconButton } from "@localnote/ui";
import { buildOutlineTree, findOutlineNode, parseHeadings } from "@localnote/protocol";
import type { HeadingEntry, OutlineNode } from "@localnote/protocol";
import { useI18n } from "../i18n";

const EMPTY: ReadonlySet<string> = new Set<string>();

/** Headings hidden by folding `node`: every descendant, not just its children. */
function hiddenCount(node: OutlineNode): number {
  return node.children.reduce((total, child) => total + 1 + hiddenCount(child), 0);
}

/**
 * Outline (目录) of the active note.
 *
 * The heading list is derived from the live editor source with the shared
 * `parseHeadings`, so it follows every keystroke and never disagrees with the
 * document; fenced code and frontmatter are excluded by the parser itself.
 *
 * Headings nest by level (see `buildOutlineTree`) and every heading with
 * children can be collapsed. Collapse state is owned by the caller and keyed by
 * note + node key, so it survives switching notes and views — and it is never
 * written into the document.
 */
export function OutlinePanel({ path, source, cursorLine = 0, collapsed, onToggle, onJump, onClose }: {
  path?: string | null;
  source: string;
  cursorLine?: number;
  /** Node keys (see `OutlineNode.key`) currently collapsed. */
  collapsed?: ReadonlySet<string>;
  onToggle?: (key: string) => void;
  onJump: (heading: HeadingEntry, index: number) => void;
  onClose?: () => void;
}) {
  const { tr } = useI18n();
  const headings = useMemo(() => parseHeadings(source), [source]);
  const tree = useMemo(() => buildOutlineTree(headings), [headings]);
  const folded = collapsed ?? EMPTY;
  /** The section the caret sits in: the last heading at or above it. */
  const activeIndex = useMemo(() => {
    let index = -1;
    for (let position = 0; position < headings.length; position += 1) {
      if (headings[position]!.line > cursorLine) break;
      index = position;
    }
    return index;
  }, [headings, cursorLine]);
  const activeKey = useMemo(() => (activeIndex < 0 ? null : findOutlineNode(tree, activeIndex)?.key ?? null), [tree, activeIndex]);

  const rows = (nodes: OutlineNode[], depth: number): ReactNode => nodes.map(node => {
    const hasChildren = node.children.length > 0;
    const isCollapsed = folded.has(node.key);
    const isActive = node.key === activeKey;
    const label = node.heading.text || tr("（无标题）", "(untitled)");
    return <div className="outline-node" key={node.key}>
      <div className={`outline-row${isActive ? " active" : ""}`} style={{ paddingLeft: 4 + depth * 13 }} data-outline-key={node.key}>
        {hasChildren && onToggle
          ? <button
            type="button"
            className={`outline-toggle${isCollapsed ? "" : " expanded"}`}
            aria-expanded={!isCollapsed}
            aria-label={isCollapsed ? tr("展开", "Expand") : tr("折叠", "Collapse")}
            title={isCollapsed ? tr("展开这一节", "Expand this section") : tr("折叠这一节", "Collapse this section")}
            onClick={event => { event.stopPropagation(); onToggle(node.key); }}
          ><Icon name="chevron" size={12}/></button>
          : <span className="outline-spacer"/>}
        <button
          type="button"
          className="outline-item"
          aria-current={isActive ? "location" : undefined}
          title={tr(`第 ${node.heading.line + 1} 行 · H${node.heading.level}`, `Line ${node.heading.line + 1} · H${node.heading.level}`)}
          onClick={() => onJump(node.heading, node.index)}
        >
          <span className="outline-level">H{node.heading.level}</span>
          <span className="outline-text">{label}</span>
          {hasChildren && isCollapsed && <span className="outline-folded" aria-hidden="true">{hiddenCount(node)}</span>}
        </button>
      </div>
      {hasChildren && !isCollapsed && rows(node.children, depth + 1)}
    </div>;
  });

  return <section className="file-browser" aria-label={tr("目录", "Outline")}>
    <header className="side-header">
      <span>{tr("目录", "Outline")}{headings.length > 0 && <em className="outline-count">{headings.length}</em>}</span>
      {onClose && <div className="side-header-actions"><IconButton onClick={onClose} title={tr("关闭目录", "Close outline")} aria-label={tr("关闭目录", "Close outline")}><Icon name="close" size={15}/></IconButton></div>}
    </header>
    {!path
      ? <div className="side-empty"><Icon name="outline" size={28}/><p role="status">{tr("打开一篇笔记，这里会列出它的标题。", "Open a note to list its headings here.")}</p></div>
      : headings.length === 0
        ? <div className="side-empty"><Icon name="outline" size={28}/><p role="status">{tr("这篇笔记还没有标题。用 # 写一个标题就会出现在这里。", "This note has no headings yet. Add one with # and it appears here.")}</p></div>
        : <nav className="outline-list" aria-label={tr("笔记标题", "Note headings")}>{rows(tree, 0)}</nav>}
  </section>;
}
