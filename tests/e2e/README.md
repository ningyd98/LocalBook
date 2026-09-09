# tests/e2e — Browser E2E placeholder

The repository now has M1–M8 backend integration tests through FastAPI
TestClient and frontend component tests through Vitest with mocked APIs, but no
real-browser/Playwright suite is implemented in this directory.

- Any future browser E2E test must use a disposable temporary Vault and fake or
  local-only AI adapters; it must never open a real user Vault.
- Playwright or another browser dependency must not block the default unit and
  integration test gates unless explicitly adopted by a milestone.
