# server/ai/prompts — M6 versioned read-only prompts

Six schema-bound Markdown prompts are registered for M6 workflows. They contain
instructions only; note context remains untrusted data and no prompt is persisted.

- M6 ships exactly six schema-bound prompt files used by the read-only workflow
  service: `chat.md`, `summarize_note.md`, `generate_tags.md`,
  `suggest_links.md`, `extract_todos.md`, and `classify_note.md`.
- `README.md` is documentation and is excluded from registry discovery.
- Note context remains untrusted reference material and no prompt is persisted.
- Prompts have no tools or write authority. M7 controlled agents/policy/history/
  recovery and M8 scheduling are implemented in their separate server domains;
  they do not expand this prompt registry's permissions.
