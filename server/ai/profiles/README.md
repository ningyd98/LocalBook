# server/ai/profiles — Phase 0 placeholder, no runtime behavior

Future provider/model profiles (capability hints, policy-relevant metadata).

- Phase 0 ships **no** profiles or runtime behavior. Discovery is implemented
  directly in server/ai (service + adapter) without profile plumbing.
- Allowed dependencies (future): server.ai.schemas.
- M1 entry: M6 when embedding/rerank profiles become real.
