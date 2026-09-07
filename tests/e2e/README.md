# tests/e2e — Phase 0 placeholder

End-to-end tests are intentionally deferred. Phase 0 verifies the API via
FastAPI TestClient, the frontend via Vitest + mocked fetch, and wiring via
`./scripts/dev.sh` + `curl`.

- No real user Vault is ever opened, and no Playwright dependency may block
  the default test run.
- M1 entry: when a real Vault round-trip exists, add e2e coverage that uses a
  disposable temporary vault only.
