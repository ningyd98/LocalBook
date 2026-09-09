import {useI18n} from "../i18n";
import { useState } from "react";
import { Button } from "@localnote/ui";
import { useWorkspaceStore } from "@localnote/workspace";

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Text render with case-insensitive keyword highlights — never HTML. */
function Highlighted({ text, terms }: { text: string; terms: string[] }) {
  if (!text) return null;
  const pattern = new RegExp(`(${terms.filter(Boolean).map(escapeRegExp).join("|")})`, "gi");
  const parts = text.split(pattern);
  return (
    <>
      {parts.map((part, index) =>
        terms.some((term) => part.toLowerCase() === term.toLowerCase()) ? (
          <mark key={index}>{part}</mark>
        ) : (
          <span key={index}>{part}</span>
        ),
      )}
    </>
  );
}

function prettyPath(path: string) {
  return path.split("/").at(-1) ?? path;
}

/** M3 keyword-search entry + results panel (Ribbon "Search"). */
export function SearchPanel({ onOpen, onClose }: { onOpen: (path: string) => void; onClose: () => void }) {
  const {tr,errorText} = useI18n();
  const search = useWorkspaceStore((s) => s.search);
  const runSearch = useWorkspaceStore((s) => s.runSearch);
  const [query, setQuery] = useState(search.query);

  const submit = (event: { preventDefault: () => void }) => {
    event.preventDefault();
    void runSearch(query);
  };

  return (
    <section className="search-panel" aria-label={tr("搜索笔记","Search notes")}>
      <header className="search-header">
        <h2>{tr("搜索","Search")}</h2>
        <Button onClick={onClose} aria-label={tr("关闭搜索","Close search")}>
          ×
        </Button>
      </header>
      <form className="search-form" onSubmit={submit} role="search">
        <input
          aria-label={tr("搜索关键词","Search query")}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={tr("搜索笔记标题与正文…","Search note titles and contents\u2026")}
          autoFocus
        />
        <Button type="submit" disabled={search.status === "loading" || query.trim().length === 0}>
          {search.status === "loading" ? tr("搜索中…","Searching…") : tr("搜索","Search")}
        </Button>
      </form>
      {search.status === "error" && (
        <p role="alert" className="search-error">
          {errorText(search.error)}
        </p>
      )}
      {search.status === "ready" && search.response && (
        <>
          {search.response.degraded && (
            <p className="search-degraded">{tr(`部分笔记无法读取，已跳过 ${search.response.skipped_notes} 篇。`, `Some notes were skipped (${search.response.skipped_notes} unreadable).`)}</p>
          )}
          {search.response.total === 0 ? (
            <p className="search-empty">{tr(`没有找到“${search.response.query}”的结果。`, `No results for “${search.response.query}”.`)}</p>
          ) : (
            <ul className="search-results" aria-label={tr("搜索结果","Search results")}>
              {search.response.hits.map((hit) => (
                <li key={hit.path} className="search-result">
                  <Button className="search-result-title" onClick={() => onOpen(hit.path)}>
                    {hit.title || prettyPath(hit.path)}
                  </Button>
                  <code className="search-result-path">{hit.path}</code>
                  {hit.snippet && (
                    <p className="search-result-snippet">
                      <Highlighted text={hit.snippet} terms={hit.matched_terms} />
                    </p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      {search.status === "idle" && <p className="search-hint">{tr("输入关键词，搜索标题、标签和笔记正文。","Type keywords to search titles, tags and note text.")}</p>}
    </section>
  );
}
