# server/search — M4 SQLite FTS search with safe substring fallback

`SearchService` exposes read-only `GET /api/v1/search?q=`. The primary path
uses the derived SQLite FTS5 index (MATCH/bm25); Chinese, Emoji, unsupported
queries, FTS misses, or an unavailable FTS index use the M3 keyword-substring
fallback. The response DTO remains stable and reports degraded/skipped counts.

- Multi-token queries use AND semantics; snippets are plain text and safe for
  React rendering.
- Results are deterministic and never write Vault, SQLite, or query text to
  logs.
- Empty, oversized, or control-character queries return 400 `invalid_request`;
  an unavailable derived index returns 503 `index_unavailable`.

**Boundary:** no semantic search, embeddings, reranking, or index-as-source;
`.localnote/index.db` is disposable derived data rebuilt from Vault bytes.
