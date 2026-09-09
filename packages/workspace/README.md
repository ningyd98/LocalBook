# @localnote/workspace

In-memory Zustand workspace session store for the M2–M12 web workspace: tree,
tabs, split panes, theme, relations/search/graph/AI state, history, attachment
uploads, rename, wikilink creation, and save/conflict state. UI components
remain in `apps/web`.

- The store has no filesystem access; all I/O is provided by an injectable
  `WorkspaceApi`. Vault file operations are required; other domain actions are
  optional and can be configured shallowly.
- Per-path save queues/coalescing, dirty-only UTF-8 saves, expected SHA-256,
  and race guards are enforced. Failures are not automatically retried.
- Conflicts expose reload/keep-local flows without silently overwriting the
  server version. Selectors and save-controller helpers are also exported.

## Attachment / rename / wikilink actions (M9–M11)

- `uploadAndInsertAttachment(file, options)` — size-based channel split
  (≤10 MiB JSON base64, otherwise multipart), inserts the reference at the live
  caret through `registerCaretInsert` and falls back to appending when no editor
  is mounted.
- `renameEntry(path, newName)` — same-directory move through the existing
  `moveVaultFile` endpoint. The expected digest is always read from disk (a
  session `baseSha256` would 409 a file with unsaved edits); open tabs and
  sessions are re-pointed so drafts survive.
- `openOrCreateLinkedNote(fromPath, target)` / `ensureFolder(directory)` —
  resolves a `[[wikilink]]` by Vault-wide basename first, otherwise creates the
  note next to the source note and creates only the missing folder levels.
