# tests/fixtures

M1–M8 test fixtures used by backend/API and controlled smoke tests.

## AI (M6–M8)

- `ai_models_connected.json` — a valid `/v1/models`-style response containing a
  Qwen3.5-4B model and an embedding model. The Qwen capability is inferred by
  the model-matching logic; the fixture's explicit capability metadata applies
  only where present.

## Vault (M1)

`vault/` is a **fabricated** Vault used only for tests and the controlled API
smoke script. It intentionally includes:

- Chinese / Emoji / space-containing and nested file names;
- a small binary attachment (`attachments/image.png`);
- duplicate Markdown titles kept as independent files;
- a controlled large file (`large.md`, ~216 KB) that is still far below the
  default `max_file_bytes`;
- a `bytes/` corpus with exact-byte variants: CRLF, UTF-8 BOM + LF, no
  trailing newline, non-UTF-8 bytes, and unknown Obsidian/HTML/code syntax.

Rules:

- Tests never write into `tests/fixtures/vault` itself: they copy the tree
  into `tmp_path` (see `tests/backend/conftest.py`) and mutate the copy.  The
  committed tree stays pristine so reads are byte-deterministic.
- Tests may also build throwaway Vaults inside pytest `tmp_path` and are
  allowed to create symlinks there (that is where the symlink-escape matrix
  lives).
- `server` code under test must never touch a real user Vault.
