# RAG Visual Verification

Date: 2026-09-12

## Scope

Validated the RAG visual search and grounded-summary integration without changing
implementation files. The backend contract was exercised through the dedicated
visual API tests; the frontend was checked with the focused `RagVisual` suite,
TypeScript, and production build.

## Commands and evidence

| Command | Result | Evidence |
|---|---|---|
| `python -m pytest -q tests/backend/test_rag_visual.py` | Not runnable in shell | `python` is not installed (`command not found`). |
| `python3 -m pytest -q tests/backend/test_rag_visual.py` | Not runnable in system Python | System Python has no `pytest` module. |
| `.venv/bin/python -m pytest -q tests/backend/test_rag_visual.py` | PASS | `5 passed, 2 warnings in 0.08s`. |
| `pnpm --filter @localnote/web typecheck` | Not runnable in shell | `pnpm` is not on PATH. |
| `pnpm --filter @localnote/web test -- RagVisual` | Not runnable in shell | `pnpm` is not on PATH. |
| `pnpm --filter @localnote/web build` | Not runnable in shell | `pnpm` is not on PATH. |
| `apps/web/node_modules/.bin/tsc --noEmit -p apps/web/tsconfig.json` | PASS | Exit code 0, no diagnostics. |
| `apps/web/node_modules/.bin/vitest run -- RagVisual` (cwd `apps/web`) | PASS | `42 passed, 1 skipped` test files; `361 passed, 3 skipped` tests. Includes `RagVisual.test.tsx` (6 tests), `RagPanel.test.tsx` (8 tests), Graph, AI, and Search suites. |
| `apps/web/node_modules/.bin/vite build` (cwd `apps/web`) | PASS with warnings | Production bundle generated successfully in 4.49s. Existing warnings: large minified chunk and a dynamic/static import split warning. |

## Contract checks

- Backend visual API tests cover response fields, empty-result behavior, generation
  degradation, and citation/source validation. The dedicated suite passes.
- Frontend visual tests cover evidence mapping, server summary/citations, source
  activation, loading, empty, generation-degraded, API-error, and unscored states.
- `RagPanel` exposes `role="status"` while busy and `role="alert"` for errors.
  Sources are rendered from server `sources` and open through `onOpenNote`; no
  citation path is derived from model prose.
- The full Vitest run selected by the `RagVisual` filter passed existing Graph,
  AI, and Search tests, so no regression was observed in those workflows.

## Residual risk

The repository's requested pnpm wrapper commands could not run because pnpm is
not installed/on PATH in this environment. Equivalent local project binaries
were used and passed. Build warnings are non-fatal and unrelated to RAG behavior.
