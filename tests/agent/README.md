# tests/agent — Agent-specific test directory placeholder

M7 controlled-Agent behavior is implemented and tested under `tests/backend/`:
Action schemas, tool allow-lists, Policy decisions, diff/confirmation,
transaction rollback, History, Undo, API contracts, and M8 scheduler integration.

This directory currently contains no separate suite. If tests move here, they
must keep fake adapters, `tmp_path` Vaults, bounded workflows, and the rule that
no model can invoke write tools or enter an autonomous loop.
