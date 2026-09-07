# @localnote/workspace

M2 in-memory Zustand workspace sessions, tree, tabs, save/conflict state and injectable API actions.

Workspace session orchestration is implemented here. UI components remain in apps/web.

- The store has no filesystem access; all IO is provided through WorkspaceApi.
- Per-path save serialization and conflict state are part of the M2 boundary.
