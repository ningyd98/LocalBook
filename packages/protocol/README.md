# @localnote/protocol

M2 shared type-only API DTOs for health, AI and Vault tree/read/mutation/error contracts.

Shared API DTO types between the LocalNote backend and frontend:

- The authoritative contract source is Python: `server/ai/schemas.py`.
- This package mirrors those DTOs as TypeScript types (`src/index.ts`).
- No business logic, no HTTP, no UI. Frontend imports consume `import type`,
  so nothing from this package is ever bundled.

Allowed dependencies: none.
M1 entry: extend here only when a new public REST/WS contract is defined.
