import {useI18n} from "../i18n";
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

function OutgoingList({ links, onOpen, onCreate }: { links: LinkRef[]; onOpen: (path: string) => void; onCreate?: (target: string) => void }) {
  const {tr} = useI18n();
  if (links.length === 0) return <p className="relations-empty">{tr("这篇笔记还没有引用链接。","No outgoing links.")}</p>;
  return (
    <ul className="link-list">
      {links.map((link, index) => (
        <LinkItem key={`${link.raw}-${index}`} link={link} onOpen={onOpen} onCreate={onCreate} />
      ))}
    </ul>
  );
}

/** M3: outgoing + backlinks for the active note (click-to-open targets). */
export function NotesLinksPanel({
  relations,
  onOpen,
  onRetry,
  onCreate,
}: {
  relations: NoteRelationsState;
  onOpen: (path: string) => void;
  onRetry: () => void;
  /** Create the missing note behind a broken `[[wikilink]]`. */
  onCreate?: (target: string) => void;
}) {
  const {tr,errorText} = useI18n();
  if (!relations.path) return null;
  const loading = relations.status === "loading" || relations.status === "idle";
  return (
    <section className="relations-panel" aria-label={`Links for ${relations.path}`}>
      <header className="relations-header">
        <h2>{tr("笔记关联","Links")}</h2>
        <span className="relations-path">{relations.path}</span>
      </header>
      {relations.status === "error" && (
        <div role="alert" className="relations-error">
          {errorText(relations.error)}{" "}
          <Button onClick={onRetry}>{tr("重试","Retry")}</Button>
        </div>
      )}
      <div className="relations-columns">
        <div className="relations-column">
          <h3>{tr("引用链接","Outgoing")} {relations.brokenCount > 0 && <span className="link-badge link-badge-broken">{tr(`${relations.brokenCount} 个失效`,`${relations.brokenCount} broken`)}</span>}</h3>
          {loading ? (
            <p className="relations-empty">{tr("正在加载引用…","Loading links\u2026")}</p>
          ) : (
            relations.outgoing && <OutgoingList links={relations.outgoing} onOpen={onOpen} onCreate={onCreate} />
          )}
        </div>
        <div className="relations-column">
          <h3>{tr("反向链接","Backlinks")} {relations.backlinks && relations.backlinks.length > 0 && <span className="relations-count">{relations.backlinks.length}</span>}</h3>
          {loading ? (
            <p className="relations-empty">{tr("正在加载反向链接…","Loading backlinks\u2026")}</p>
          ) : relations.backlinks && relations.backlinks.length > 0 ? (
            <ul className="backlink-list">
              {relations.backlinks.map((backlink, index) => (
                <BacklinkRow key={`${backlink.source_path}-${index}`} backlink={backlink} onOpen={onOpen} />
              ))}
            </ul>
          ) : (
            <p className="relations-empty">{tr("还没有其他笔记链接到这里。","No backlinks.")}</p>
          )}
        </div>
      </div>
    </section>
  );
}
