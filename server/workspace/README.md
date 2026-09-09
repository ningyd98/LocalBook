# server/workspace — Server-side workspace boundary (not implemented)

The current workspace/session orchestration lives in `packages/workspace` and the
React app. This server package remains an intentional placeholder: it does not
own sessions, tabs, persistence, or filesystem access.

- No server workspace/session REST API is exposed.
- Client-side state uses injectable `WorkspaceApi`; the server remains stateless.
- Future entry point: introduce a server session API only with an explicit
  milestone and protocol contract.
