# server/ai/profiles — Reserved provider/model profile boundary

This directory remains an intentional placeholder. M6 discovery and workflow
runtime are implemented in `server/ai/service.py`, `server/ai/schemas.py`,
and `server/ai/adapters/` without profile plumbing.

- No profile registry or profile-specific runtime behavior is exposed.
- Embedding and reranking endpoints remain unavailable and are reported as
  `capability_unavailable`; this package is not required for that behavior.
- A future profile layer must be introduced by an explicit milestone and must
  not weaken the current allow-list, schema, or offline-safety boundaries.
