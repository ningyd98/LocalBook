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
  const search = useWorkspaceStore((s) => s.search);
  const runSearch = useWorkspaceStore((s) => s.runSearch);
  const [query, setQuery] = useState(search.query);

  const submit = (event: { preventDefault: () => void }) => {
    event.preventDefault();
    void runSearch(query);
  };

  return (
    <section className="search-panel" aria-label="Search notes">
      <header className="search-header">
        <h2>Search</h2>
        <Button onClick={onClose} aria-label="Close search">
          ×
        </Button>
      </header>
      <form className="search-form" onSubmit={submit} role="search">
        <input
          aria-label="Search query"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search note titles and contents…"
          autoFocus
        />
        <Button type="submit" disabled={search.status === "loading" || query.trim().length === 0}>
          {search.status === "loading" ? "Searching…" : "Search"}
        </Button>
      </form>
      {search.status === "error" && (
        <p role="alert" className="search-error">
          {search.error?.message ?? "Search failed."}
        </p>
      )}
      {search.status === "ready" && search.response && (
        <>
          {search.response.degraded && (
            <p className="search-degraded">Some notes were skipped ({search.response.skipped_notes} unreadable).</p>
          )}
          {search.response.total === 0 ? (
            <p className="search-empty">No results for “{search.response.query}”.</p>
          ) : (
            <ul className="search-results" aria-label="Search results">
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
      {search.status === "idle" && <p className="search-hint">Type keywords to search titles, tags and note text.</p>}
    </section>
  );
}
