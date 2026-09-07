"""Strict DTOs for controlled agent jobs (M7, PLAN-M7 §5.6/§6)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from server.actions.schemas import (
    Action,
    ActionSet,
    ActionType,
    ActionValidationError,
    PatchHunk,
    PermissionLevel,
    validate_action,
    validate_action_set,
)
from server.policies.rules import MARKDOWN_SUFFIXES
from server.recovery.schemas import JobStatus
from server.vault.path_safety import validate_relative_path

WORKFLOW_TASK_TYPES: tuple[str, ...] = ("daily_organizer", "weekly_review")
TaskType = Literal["daily_organizer", "weekly_review", "manual"]


def create_target_problem(
    target: str,
    *,
    scope_paths: list[str] | tuple[str, ...] = (),
    existing_markdown: set[str] | frozenset[str] | None = None,
) -> str | None:
    """Why a CREATE_NOTE target is unsafe (``None`` when acceptable).

    Pure check shared by the workflow scope enforcer and the job service:
    the target must be a safe new Markdown path, must not overwrite an
    existing note, and — when the request declares explicit scope paths —
    must sit in the same directory as one of those scope files.
    """
    if not target.casefold().endswith(MARKDOWN_SUFFIXES):
        return "create_note target must be a Markdown file"
    if existing_markdown is not None:
        folded = {path.casefold() for path in existing_markdown}
        if target.casefold() in folded:
            return "create_note target already exists; overwriting is denied"
    if scope_paths:
        target_dir = target.rsplit("/", 1)[0] if "/" in target else ""
        scope_dirs = {path.rsplit("/", 1)[0] if "/" in path else "" for path in scope_paths}
        if target_dir not in scope_dirs:
            return "create_note target is outside the request scope directory"
    return None


class JobScope(BaseModel):
    """Declared read/write scope of one job request (never free text)."""

    model_config = ConfigDict(extra="forbid")

    paths: list[str] = Field(default_factory=list, max_length=100)
    max_files: int = Field(default=10, ge=1, le=50)
    max_chars: int = Field(default=60_000, ge=1_000, le=300_000)

    @field_validator("paths")
    @classmethod
    def _valid_paths(cls, values: list[str]) -> list[str]:
        seen: set[str] = set()
        paths: list[str] = []
        for value in values:
            path = validate_relative_path(value)
            if not path.casefold().endswith(MARKDOWN_SUFFIXES):
                raise ValueError("scope paths must be Markdown files")
            if path.casefold() not in seen:
                seen.add(path.casefold())
                paths.append(path)
        return paths


class JobRequest(BaseModel):
    """One controlled planning/execution request (no shell, no URLs)."""

    model_config = ConfigDict(extra="forbid")

    task_type: TaskType = "manual"
    permission_level: int = Field(default=1, ge=0, le=2)
    scope: JobScope = Field(default_factory=JobScope)
    actions: list[Action] = Field(default_factory=list, max_length=20)
    execute: bool = False

    def validated(self) -> JobRequest:
        if self.actions:
            validate_action_set(
                ActionSet(actions=self.actions, task_type=self.task_type)
            )
        return self


class AcceptJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_ids: list[str] = Field(default_factory=list, max_length=20)
    confirm: bool = False


class UndoJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool = False


__all__ = [
    "AcceptJobRequest",
    "Action",
    "ActionSet",
    "ActionType",
    "ActionValidationError",
    "JobRequest",
    "JobScope",
    "JobStatus",
    "PatchHunk",
    "PermissionLevel",
    "TaskType",
    "UndoJobRequest",
    "WORKFLOW_TASK_TYPES",
    "create_target_problem",
    "validate_action",
    "validate_action_set",
]
