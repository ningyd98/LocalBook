# @localnote/protocol

Shared type-only API DTOs for the M0–M8 public REST surface: health, AI,
Vault, metadata, links, search, index, graph, actions/policy, jobs/history/undo,
and scheduler/recovery.

- Authoritative runtime schemas live in the corresponding Python domain modules
  (`server/**/schemas.py` and scheduler service types); `src/index.ts` mirrors
  their public wire shapes for TypeScript consumers.
- This package contains no business logic, HTTP client, or UI. Consumers should
  use type-only imports where possible.
- Runtime dependencies: none; development-only dependencies may be used for
  typechecking.
- Extend it whenever a public REST contract changes. No WebSocket contract is
  currently exposed.
