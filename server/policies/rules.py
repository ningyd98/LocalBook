"""M7 policy constants and hard safety budgets (PLAN-M7 §3.2/§8.1).

The engine reads these constants; config may tighten them per deployment but
never widen them beyond the hard ceilings declared here.
"""

from __future__ import annotations

from server.actions.schemas import ActionType

#: Every wire action the policy can ever accept.  Delete/attachment
#: overwrite/recursive/shell actions are *not* representable at all.
ALLOWED_ACTIONS = frozenset(ActionType)

#: Actions permitted to run automatically at Level 2 (tag bookkeeping only).
#: The *configured* Level-2 set starts empty — see PolicyEngine settings —
#: so Level 2 is closed by default.
AUTO_ACTIONS = frozenset({ActionType.ADD_TAGS, ActionType.REMOVE_TAGS})

#: Minimum permission level required by each action type (PLAN-M7 §3.2).
MIN_LEVEL_BY_ACTION: dict[ActionType, int] = {action: 1 for action in ALLOWED_ACTIONS}

#: Hard ceilings (config may only lower these).
MAX_ACTIONS = 20
MAX_FILES = 10
MAX_LEVEL1_MODIFIED_CHARS = 20_000
MAX_LEVEL2_MODIFIED_CHARS = 2_000
MAX_DIFF_BYTES = 200_000
MAX_JOURNAL_BYTES = 10_000_000
MAX_UNDO_BYTES = 10_000_000

#: Vault-relative prefixes that policy denies unconditionally.
DEFAULT_PROTECTED_PREFIXES: tuple[str, ...] = (".localnote",)

#: Only Markdown files may be mutated through M7 write actions.
MARKDOWN_SUFFIXES: tuple[str, ...] = (".md", ".markdown")

#: A Level-2 job may only touch one existing file, without body changes.
LEVEL2_MAX_FILES = 1
