# server/ai/prompts — M6 versioned read-only prompts

Six schema-bound Markdown prompts are registered for M6 workflows. They contain
instructions only; note context remains untrusted data and no prompt is persisted.

- M6 ships six schema-bound prompts used by the read-only workflow service.
- Note context remains untrusted reference material and no prompt is persisted.
- Allowed schema dependencies: `server.ai.schemas`; no tools, writes, or agent execution.
- M7+ capabilities (agents, policy, tools, history, recovery, scheduling) are not implemented.
