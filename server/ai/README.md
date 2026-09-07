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

- Read-only: no endpoint writes Markdown, frontmatter, SQLite, graph, history
  or settings; no Agent/Policy/Diff/Undo/Recovery/Scheduler/WebSocket or
  streaming; no real user Vault / real oMLX / cloud AI in tests (only
  `httpx.MockTransport`, fake adapters and `tmp_path` fixtures).
- Embedding/rerank real endpoints are **not** implemented; their absence is
  never misrepresented as availability.
- M7 entry: write paths (`Policy → Diff → History → Recovery → user approval
  → Vault write`) are explicitly out of scope until the M6 audit gate passes.

See [`PLAN-M6.md`](../../PLAN-M6.md) and
[`docs/ai-architecture.md`](../../docs/ai-architecture.md).
