"""M7 action schema and diff exports."""

from server.actions.diff import DiffEntry, build_diff, diff_to_dict, sha256
from server.actions.patches import (
    UnsupportedPatch,
    add_link,
    add_tags,
    apply_hunks,
    has_tags_block,
    remove_tags,
    tags_only_change,
)
from server.actions.schemas import (
    Action,
    ActionSet,
    ActionType,
    ActionValidationError,
    PatchHunk,
    PermissionLevel,
    normalize_sha256,
    normalize_tag,
    validate_action,
    validate_action_set,
)
from server.actions.validators import parse_action_set

__all__ = [
    "Action",
    "ActionSet",
    "ActionType",
    "ActionValidationError",
    "DiffEntry",
    "PatchHunk",
    "PermissionLevel",
    "UnsupportedPatch",
    "add_link",
    "add_tags",
    "apply_hunks",
    "build_diff",
    "diff_to_dict",
    "has_tags_block",
    "normalize_sha256",
    "normalize_tag",
    "parse_action_set",
    "remove_tags",
    "sha256",
    "tags_only_change",
    "validate_action",
    "validate_action_set",
]
