import { Button } from "@localnote/ui";
import type { BacklinkRef, LinkRef } from "@localnote/protocol";
import { isEditableMarkdown } from "@localnote/workspace";
import type { NoteRelationsState } from "@localnote/workspace";
import { LinkItem } from "./LinkItem";

function BacklinkRow({ backlink, onOpen }: { backlink: BacklinkRef; onOpen: (path: string) => void }) {
  const editable = isEditableMarkdown(backlink.source_path);
  return (
    <li className="backlink-item">
      {editable ? (
        <button type="button" className="link-open" onClick={() => onOpen(backlink.source_path)}>
          {backlink.title || backlink.source_path}
        </button>
      ) : (
        <span className="link-static">{backlink.title || backlink.source_path}</span>
      )}
      <code className="backlink-source">{backlink.source_path}</code>
      {backlink.text && <p className="backlink-context">{backlink.text}</p>}
    </li>
  );
}

function OutgoingList({ links, onOpen }: { links: LinkRef[]; onOpen: (path: string) => void }) {
  if (links.length === 0) return <p className="relations-empty">No outgoing links.</p>;
  return (
    <ul className="link-list">
      {links.map((link, index) => (
        <LinkItem key={`${link.raw}-${index}`} link={link} onOpen={onOpen} />
      ))}
    </ul>
  );
}

/** M3: outgoing + backlinks for the active note (click-to-open targets). */
export function NotesLinksPanel({
  relations,
  onOpen,
  onRetry,
}: {
  relations: NoteRelationsState;
  onOpen: (path: string) => void;
  onRetry: () => void;
}) {
  if (!relations.path) return null;
  const loading = relations.status === "loading" || relations.status === "idle";
  return (
    <section className="relations-panel" aria-label={`Links for ${relations.path}`}>
      <header className="relations-header">
        <h2>Links</h2>
        <span className="relations-path">{relations.path}</span>
      </header>
      {relations.status === "error" && (
        <div role="alert" className="relations-error">
          {relations.error?.message ?? "Unable to load links."}{" "}
          <Button onClick={onRetry}>Retry</Button>
        </div>
      )}
      <div className="relations-columns">
        <div className="relations-column">
          <h3>Outgoing {relations.brokenCount > 0 && <span className="link-badge link-badge-broken">{relations.brokenCount} broken</span>}</h3>
          {loading ? (
            <p className="relations-empty">Loading links…</p>
          ) : (
            relations.outgoing && <OutgoingList links={relations.outgoing} onOpen={onOpen} />
          )}
        </div>
        <div className="relations-column">
          <h3>Backlinks {relations.backlinks && relations.backlinks.length > 0 && <span className="relations-count">{relations.backlinks.length}</span>}</h3>
          {loading ? (
            <p className="relations-empty">Loading backlinks…</p>
          ) : relations.backlinks && relations.backlinks.length > 0 ? (
            <ul className="backlink-list">
              {relations.backlinks.map((backlink, index) => (
                <BacklinkRow key={`${backlink.source_path}-${index}`} backlink={backlink} onOpen={onOpen} />
              ))}
            </ul>
          ) : (
            <p className="relations-empty">No backlinks.</p>
          )}
        </div>
      </div>
    </section>
  );
}
