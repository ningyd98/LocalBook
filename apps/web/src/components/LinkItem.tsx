import type { LinkRef } from "@localnote/protocol";
import { isEditableMarkdown } from "@localnote/workspace";

/**
 * One outgoing-link row. Resolved markdown targets open the note; broken and
 * ambiguous links are visually marked and never pretend to be navigable.
 */
export function LinkItem({ link, onOpen }: { link: LinkRef; onOpen: (path: string) => void }) {
  const label = link.display || link.target || link.raw;
  const suffix = link.section ? `#${link.section}` : link.block ? `^${link.block}` : "";
  const className = [
    "link-item",
    link.broken ? "link-broken" : "",
    link.ambiguous ? "link-ambiguous" : "",
  ]
    .filter(Boolean)
    .join(" ");

  let content;
  if (link.kind === "web") {
    content = (
      <a className="link-web" href={link.target} target="_blank" rel="noreferrer" title={link.raw}>
        {label} ↗
      </a>
    );
  } else if (link.resolved_path && isEditableMarkdown(link.resolved_path)) {
    content = (
      <button type="button" className="link-open" onClick={() => onOpen(link.resolved_path!)} title={link.raw}>
        {label}
        {suffix}
      </button>
    );
  } else {
    content = (
      <span className="link-static" title={link.raw}>
        {label}
        {suffix}
      </span>
    );
  }

  return (
    <li className={className}>
      {content}
      {link.broken && <span className="link-badge link-badge-broken">broken</span>}
      {link.ambiguous && <span className="link-badge link-badge-ambiguous">ambiguous</span>}
    </li>
  );
}
