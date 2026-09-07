"""Controlled-agent tool implementations for M7 (PLAN-M7 §5.6).

Read tools are thin, bounded adapters over the service boundaries only
(VaultService / DerivedIndexService / MetadataService / LinksService /
SearchService) — never raw ``open`` or SQL.  Write tools are *not callable*:
they only expose pure plan helpers that compute the journaled
:class:`TransactionOperation` from already-fetched bytes; the executor (not
the model) performs the actual write through VaultService.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any

from server.actions.patches import (
    UnsupportedPatch,
    add_link,
    add_tags,
    apply_hunks,
    remove_tags,
)
from server.actions.schemas import Action
from server.policies.errors import InvalidAction
from server.policies.rules import MARKDOWN_SUFFIXES
from server.recovery.schemas import TransactionOperation


@dataclass
class ToolContext:
    """Service boundaries + budget for read-tool invocation."""

    vault: Any
    index: Any = None
    metadata: Any = None
    links: Any = None
    search: Any = None
    budget: int = field(default=100)


def _is_markdown(path: str) -> bool:
    return path.casefold().endswith(MARKDOWN_SUFFIXES)


def _read_text(ctx: ToolContext, path: str, max_chars: int | None = None) -> tuple[str, str]:
    if not _is_markdown(path):
        raise InvalidAction("tool can only read Markdown notes")
    data, digest = ctx.vault.read_bytes(path)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidAction("note is not UTF-8 text") from exc
    if max_chars is not None and len(text) > max_chars:
        text = text[:max_chars]
    return text, digest


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------


def vault_list(ctx: ToolContext, path: str | None = None, recursive: bool = True) -> list[str]:
    entries = ctx.vault.list_files(path, recursive=recursive, include_hidden=False)
    return [entry.path for entry in entries if entry.kind == "file" and _is_markdown(entry.path)]


def vault_read(
    ctx: ToolContext,
    path: str,
    max_chars: int | None = None,
) -> dict[str, Any]:
    text, digest = _read_text(ctx, path, max_chars)
    return {"path": path, "content": text, "sha256": digest}


def vault_search(ctx: ToolContext, query: str, limit: int = 20) -> list[str]:
    safe_limit = max(1, min(int(limit), 50))
    if ctx.search is not None:
        response = ctx.search.search(query)
        return [hit.path for hit in response.hits[:safe_limit]]
    # Degraded local scan: read tools only, bounded to the first 200 notes.
    terms = [term.casefold() for term in query.split() if term]
    hits: list[str] = []
    for path in vault_list(ctx)[:200]:
        try:
            text, _digest = _read_text(ctx, path)
        except InvalidAction:
            continue
        folded = text.casefold()
        if all(term in folded for term in terms):
            hits.append(path)
        if len(hits) >= safe_limit:
            break
    return hits


def metadata_get(ctx: ToolContext, path: str) -> dict[str, Any]:
    if ctx.metadata is not None:
        return ctx.metadata.get(path).model_dump(mode="json")
    # Degraded fallback: derive tags from the frontmatter parse only.
    text, _digest = _read_text(ctx, path)
    return {"path": path, "tags": _frontmatter_tags(text), "title": path.rsplit("/", 1)[-1]}


def _frontmatter_tags(text: str) -> list[str]:
    try:
        from server.markdown.frontmatter import parse_frontmatter  # type: ignore[attr-defined]

        result = parse_frontmatter(text)
        return list(result.tags or [])
    except Exception:  # pragma: no cover - parser degrades to no tags
        return []


def _shared_related(ctx: ToolContext, path: str, limit: int) -> list[str]:
    """Related notes ranked by shared tags then backlinks (index-backed)."""
    if ctx.index is None:
        return []
    try:
        own_tags = {tag.casefold() for tag in (ctx.index.note_tags(path) or [])}
        own_tags = {tag for tag in own_tags if isinstance(tag, str)}
    except Exception:
        own_tags = set()
    backlink_paths: list[str] = []
    try:
        backlink_paths = [source for source, _ref in ctx.index.backlink_sources(path)]
    except Exception:
        pass
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for candidate in [*backlink_paths, *vault_list(ctx)]:
        if candidate == path or candidate in seen:
            continue
        score = 0
        if candidate in backlink_paths:
            score += 2
        if ctx.index is not None:
            try:
                candidate_tags = {
                    tag for tag in (ctx.index.note_tags(candidate) or []) if isinstance(tag, str)
                }
                score += len({tag.casefold() for tag in candidate_tags} & own_tags)
            except Exception:
                pass
        if score:
            seen.add(candidate)
            scored.append((score, candidate))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [candidate for _score, candidate in scored[: max(1, min(int(limit), 20))]]


def knowledge_related(ctx: ToolContext, path: str, limit: int = 5) -> list[str]:
    return _shared_related(ctx, path, limit)


def backlinks(ctx: ToolContext, path: str) -> dict[str, Any]:
    if ctx.links is not None:
        response = ctx.links.backlinks(path)
        return {
            "path": path,
            "backlinks": [
                {"source_path": item.source_path, "title": item.title}
                for item in response.backlinks
            ],
        }
    sources = []
    if ctx.index is not None:
        try:
            sources = [source for source, _ref in ctx.index.backlink_sources(path)]
        except Exception:
            sources = []
    return {"path": path, "backlinks": [{"source_path": item} for item in sources]}


def outgoing(ctx: ToolContext, path: str) -> dict[str, Any]:
    if ctx.links is not None:
        response = ctx.links.outgoing(path)
        return {
            "path": path,
            "outgoing": [
                {"target": item.target, "raw": item.raw} for item in response.outgoing
            ],
        }
    refs: list[dict[str, str]] = []
    if ctx.index is not None:
        try:
            refs = [
                {"target": item.target, "raw": item.raw}
                for item in ctx.index.outgoing_for(path)
            ]
        except Exception:
            refs = []
    return {"path": path, "outgoing": refs}


def keyword(ctx: ToolContext, query: str, limit: int = 20) -> list[str]:
    return vault_search(ctx, query, limit)


# ---------------------------------------------------------------------------
# Write-plan helpers (pure; used by the job service / executor only)
# ---------------------------------------------------------------------------


def plan_create_operation(action: Action) -> TransactionOperation:
    if action.content_base64 is None:
        raise InvalidAction("create_note requires content")
    after = base64.b64decode(action.content_base64)
    return TransactionOperation(
        operation="create",
        path=action.file,
        before_exists=False,
        before_bytes=None,
        before_hash=None,
        after_bytes=after,
        after_hash=None,  # filled by make_entry from after_bytes
    )


def _after_bytes_for(action: Action, before: bytes, path: str) -> bytes:
    try:
        text = before.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnsupportedActionError("note is not UTF-8 text") from exc
    kind = action.action.value
    try:
        if kind == "add_tags":
            return add_tags(text, action.tags).encode("utf-8")
        if kind == "remove_tags":
            return remove_tags(text, action.tags).encode("utf-8")
        if kind == "add_link":
            if not action.link_target:
                raise UnsupportedActionError("add_link requires a target")
            return add_link(text, action.link_target).encode("utf-8")
        if kind == "patch_note":
            return apply_hunks(before, action.patch)
        if kind == "create_note":
            return base64.b64decode(action.content_base64 or "")
    except UnsupportedPatch as exc:
        raise UnsupportedActionError(str(exc)) from exc
    raise UnsupportedActionError(f"no write plan for {kind} on {path}")


class UnsupportedActionError(InvalidAction):
    def __init__(self, message: str) -> None:
        super().__init__(message)


def plan_existing_file_operation(
    action: Action,
    path: str,
    before: bytes,
    before_hash: str,
) -> TransactionOperation:
    """Plan a bounded in-place write for tag/link/patch actions."""
    after = _after_bytes_for(action, before, path)
    if action.action.value == "move_note":
        raise UnsupportedActionError("move_note is planned by plan_move_operation")
    return TransactionOperation(
        operation="update",
        path=path,
        before_exists=True,
        before_bytes=before,
        before_hash=before_hash,
        after_bytes=after,
        after_hash=None,
    )


def plan_move_operation(
    action: Action,
    source: str,
    target: str,
    before: bytes,
    before_hash: str,
) -> TransactionOperation:
    """Plan a no-overwrite single-file move (content bytes preserved)."""
    return TransactionOperation(
        operation="move",
        path=target,
        before_exists=True,
        before_bytes=before,
        before_hash=before_hash,
        after_bytes=before,
        after_hash=before_hash,
        metadata={"source": source},
    )


__all__ = [
    "ToolContext",
    "UnsupportedActionError",
    "backlinks",
    "keyword",
    "knowledge_related",
    "metadata_get",
    "outgoing",
    "plan_create_operation",
    "plan_existing_file_operation",
    "plan_move_operation",
    "vault_list",
    "vault_read",
    "vault_search",
]
