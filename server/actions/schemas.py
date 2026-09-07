"""Strict, model-facing action DTOs for M7 (PLAN-M7 §5.1).

Rules that live here:

- Every model is ``extra="forbid"`` so natural-language fields, shell
  commands or arbitrary function-call arguments are rejected before they ever
  reach policy evaluation.
- ``file``/``target_file``/``link_target`` reuse the M1 lexical path rule and
  are re-checked against the real Vault before any write.
- All list/size limits are hard Field bounds (actions <= 20, tags <= 10,
  hunks <= 20, reason <= 500 chars, create content <= 1 MB).
- The model-owned ``permission_level``/``model``/``prompt_version`` fields are
  untrusted metadata: the service overrides them with server-side values.

A model that outputs anything but a strict JSON ``ActionSet`` fails with a
stable error; it is never retried as free text.
"""

from __future__ import annotations

import base64
import binascii
import re
from enum import IntEnum, StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from server.vault.path_safety import validate_relative_path

_SHA256_RE = re.compile(r"^(?:sha256:)?[0-9a-fA-F]{64}$")


class ActionType(StrEnum):
    ADD_TAGS = "add_tags"
    REMOVE_TAGS = "remove_tags"
    ADD_LINK = "add_link"
    CREATE_NOTE = "create_note"
    PATCH_NOTE = "patch_note"
    MOVE_NOTE = "move_note"


class PermissionLevel(IntEnum):
    """Level 0 read-only, Level 1 suggestions, Level 2 low-risk auto."""

    READ_ONLY = 0
    SUGGEST = 1
    LOW_RISK_AUTO = 2


def normalize_sha256(value: str | None) -> str | None:
    """Validate and normalize a SHA-256 digest (accepts the sha256: prefix)."""
    if value is None:
        return None
    if not _SHA256_RE.fullmatch(value):
        raise ValueError("expected_sha256 must be a SHA-256 digest")
    return value.lower()


def normalize_tag(value: object) -> str:
    """Normalize one tag: strip ``#``/whitespace, reject control chars."""
    if not isinstance(value, str):
        raise ValueError("tag must be a string")
    tag = value.strip().lstrip("#").strip()
    if not tag:
        raise ValueError("tag must not be empty")
    if len(tag) > 100:
        raise ValueError("tag exceeds 100 characters")
    if any(char in tag for char in "\r\n,"):
        raise ValueError("tag contains invalid characters")
    return tag


class PatchHunk(BaseModel):
    """One bounded, locatable byte-span replacement (never free-form prose)."""

    model_config = ConfigDict(extra="forbid")

    start: int = Field(ge=0)
    old_text: str = Field(default="", max_length=10_000)
    new_text: str = Field(default="", max_length=10_000)
    old_sha256: str | None = None

    @field_validator("old_sha256")
    @classmethod
    def _valid_hash(cls, value: str | None) -> str | None:
        return normalize_sha256(value)


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: UUID = Field(default_factory=uuid4)
    action: ActionType
    permission_level: Literal[0, 1, 2] = 1
    file: str
    target_file: str | None = None
    tags: list[str] = Field(default_factory=list, max_length=10)
    link_target: str | None = None
    patch: list[PatchHunk] = Field(default_factory=list, max_length=20)
    content_base64: str | None = None
    reason: str = Field(min_length=1, max_length=500)
    expected_sha256: str | None = None

    @field_validator("file", "target_file", "link_target")
    @classmethod
    def _valid_paths(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_relative_path(value)

    @field_validator("tags")
    @classmethod
    def _valid_tags(cls, values: list[str]) -> list[str]:
        normalized = [normalize_tag(value) for value in values]
        if len({tag.casefold() for tag in normalized}) != len(normalized):
            raise ValueError("duplicate tags")
        return normalized

    @field_validator("expected_sha256")
    @classmethod
    def _valid_expected(cls, value: str | None) -> str | None:
        return normalize_sha256(value)

    @field_validator("content_base64")
    @classmethod
    def _valid_content(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            payload = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("content_base64 is not valid base64") from exc
        if len(payload) > 1_000_000:
            raise ValueError("content exceeds the 1 MB create limit")
        return value


class ActionSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actions: list[Action] = Field(max_length=20)
    task_type: Literal["daily_organizer", "weekly_review", "manual"]
    model: str | None = Field(default=None, max_length=200)
    prompt_version: str | None = Field(default=None, max_length=100)


class ActionValidationError(ValueError):
    """A structured action set violates its semantic contract."""


def validate_action(action: Action) -> Action:
    """Enforce action-type <-> field relationships (pure, no filesystem)."""
    kind = action.action
    if kind in (ActionType.ADD_TAGS, ActionType.REMOVE_TAGS):
        if not action.tags:
            raise ActionValidationError(f"{kind.value} requires at least one tag")
    if kind == ActionType.ADD_LINK:
        if not action.link_target:
            raise ActionValidationError("add_link requires link_target")
    if kind == ActionType.CREATE_NOTE:
        if action.content_base64 is None:
            raise ActionValidationError("create_note requires content_base64")
        if action.expected_sha256 is not None:
            raise ActionValidationError("create_note must not carry expected_sha256")
    if kind == ActionType.PATCH_NOTE:
        if not action.patch:
            raise ActionValidationError("patch_note requires at least one hunk")
        if action.content_base64 is not None:
            raise ActionValidationError("patch_note must not carry content_base64")
    if kind == ActionType.MOVE_NOTE:
        if not action.target_file:
            raise ActionValidationError("move_note requires target_file")
    if kind != ActionType.PATCH_NOTE and action.patch:
        raise ActionValidationError("patch hunks are only valid for patch_note")
    if kind != ActionType.CREATE_NOTE and action.content_base64 is not None:
        raise ActionValidationError("content_base64 is only valid for create_note")
    return action


def validate_action_set(actions: ActionSet) -> ActionSet:
    """Validate a whole set: non-empty, unique ids, per-action semantics."""
    if not actions.actions:
        raise ActionValidationError("action set must not be empty")
    ids = [action.action_id for action in actions.actions]
    if len({*ids}) != len(ids):
        raise ActionValidationError("duplicate action_id")
    for action in actions.actions:
        validate_action(action)
    return actions


__all__ = [
    "Action",
    "ActionSet",
    "ActionType",
    "ActionValidationError",
    "PermissionLevel",
    "PatchHunk",
    "normalize_sha256",
    "normalize_tag",
    "validate_action",
    "validate_action_set",
]
