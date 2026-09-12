# server/ai — AI status probe + M6 read-only workflows

## Current state

M6 is implemented as a read-only AI layer (PLAN-M6). The surface is:

- `GET /api/v1/ai/status` — Phase 0 oMLX discovery probe (`not_configured` /
  `offline` / `connected`, all HTTP 200), unchanged.
- Six POST read-only workflows behind one `AIWorkflowService`:
  `POST /api/v1/ai/chat` · `/ai/summarize` · `/ai/tags` · `/ai/related` ·
  `/ai/extract_todos` · `/ai/classify`.
- Strict input/output DTOs (`extra="forbid"`, bounded lengths/lists), a
  versioned Markdown Prompt Registry (`server/ai/prompts/*.md`, real prompt
  bodies + `prompt_version` reach the model and every response), a bounded
  ContextBuilder, and programmatic related-candidate narrowing driven by
  FTS/substring search merged with link/graph neighbors (allow-listed before
  any model call).
- One HTTP boundary: `server/ai/adapters/` (OpenAI-compatible client for
  chat/models plus capability probes). embedding/rerank stay optional and are
  reported `capability_unavailable` — never faked. `response_format`
  rejection falls back to plain JSON once; local strict validation remains
  final.
- Errors: stable codes with `meta: {prompt_version, model}` on structured
  failures; 400/404/502/503 mappings, never a leaked traceback or upstream
  body; AI failures never take health/Vault/editor down.

## Boundaries

- This package's M6 workflow endpoints are read-only: they do not write
  Markdown, frontmatter, SQLite, graph, history, or settings. Controlled M7
  writes and M8 scheduling are separate domains under `server/agents`,
  `policies`, `recovery`, `history`, and `scheduler`; they are not AI workflow
  implementation paths.
- Embedding/rerank real endpoints are **not** implemented; their absence is
  reported as `capability_unavailable`, never misrepresented as availability.
- No WebSocket or streaming API is exposed. Tests use `httpx.MockTransport`,
  fake adapters, and `tmp_path` fixtures only; they never use a real user Vault,
  real oMLX, or cloud AI.

See [`docs/ai-architecture.md`](../../docs/ai-architecture.md).
