"""Static controlled-agent tool registry for M7 (PLAN-M7 §5.6, M7-09).

Registry facts are static metadata: name, read/write kind, minimum
permission level, input/output schema, cost budget and the workflows allowed
to see each tool.  Models never call tools — a workflow runs at most one
model request and returns one ActionSet; write tools cannot be invoked by a
model at all (kind ``write`` has an empty workflow allow-list and
:meth:`ToolRegistry.invoke` refuses them unconditionally).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from server.policies.errors import InvalidAction

# ---------------------------------------------------------------------------
# Tool specifications
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolSpec:
    name: str
    kind: str  # "read" | "write"
    min_level: int
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    cost: int
    allowed_workflows: frozenset[str] = field(default_factory=frozenset)

    def allowed_for(self, workflow: str) -> bool:
        return not self.allowed_workflows or workflow in self.allowed_workflows


_PATH = {"type": "string", "description": "root-relative POSIX path"}
_QUERY = {"type": "string", "description": "keyword query"}
_LIMIT = {"type": "integer", "minimum": 1, "maximum": 50}

READ_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "vault.list",
        "read",
        0,
        {"type": "object", "properties": {"path": _PATH, "recursive": {"type": "boolean"}}},
        {"type": "array", "items": {"type": "string"}},
        cost=2,
    ),
    ToolSpec(
        "vault.read",
        "read",
        0,
        {"type": "object", "properties": {"path": _PATH, "max_chars": {"type": "integer"}}},
        {"type": "object", "properties": {
            "path": _PATH,
            "content": {"type": "string"},
            "sha256": {"type": "string"},
        }},
        cost=4,
    ),
    ToolSpec(
        "vault.search",
        "read",
        0,
        {"type": "object", "properties": {"query": _QUERY, "limit": _LIMIT}},
        {"type": "array", "items": {"type": "string"}},
        cost=3,
    ),
    ToolSpec(
        "metadata.get",
        "read",
        0,
        {"type": "object", "properties": {"path": _PATH}},
        {"type": "object", "properties": {"path": _PATH, "tags": {"type": "array"}}},
        cost=2,
    ),
    ToolSpec(
        "knowledge.related",
        "read",
        0,
        {"type": "object", "properties": {"path": _PATH, "limit": _LIMIT}},
        {"type": "array", "items": {"type": "string"}},
        cost=3,
    ),
    ToolSpec(
        "backlinks",
        "read",
        0,
        {"type": "object", "properties": {"path": _PATH}},
        {"type": "object", "properties": {"backlinks": {"type": "array"}}},
        cost=2,
    ),
    ToolSpec(
        "outgoing",
        "read",
        0,
        {"type": "object", "properties": {"path": _PATH}},
        {"type": "object", "properties": {"outgoing": {"type": "array"}}},
        cost=2,
    ),
    ToolSpec(
        "keyword",
        "read",
        0,
        {"type": "object", "properties": {"query": _QUERY, "limit": _LIMIT}},
        {"type": "array", "items": {"type": "string"}},
        cost=3,
    ),
)

#: Write tools exist as metadata + safe transforms only.  Their workflow
#: allow-list is empty so a workflow can never invoke them directly, and
#: ``ToolRegistry.invoke`` refuses every write tool.
WRITE_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "note.create",
        "write",
        1,
        {"type": "object", "properties": {"path": _PATH, "content_base64": {"type": "string"}}},
        {"type": "object"},
        cost=0,
    ),
    ToolSpec(
        "note.patch",
        "write",
        1,
        {"type": "object", "properties": {"path": _PATH, "patch": {"type": "array"}}},
        {"type": "object"},
        cost=0,
    ),
    ToolSpec(
        "note.move",
        "write",
        1,
        {"type": "object", "properties": {"source": _PATH, "target": _PATH}},
        {"type": "object"},
        cost=0,
    ),
    ToolSpec(
        "metadata.set",
        "write",
        1,
        {"type": "object", "properties": {"path": _PATH, "tags": {"type": "array"}}},
        {"type": "object"},
        cost=0,
    ),
    ToolSpec(
        "tag.add",
        "write",
        1,
        {"type": "object", "properties": {"path": _PATH, "tags": {"type": "array"}}},
        {"type": "object"},
        cost=0,
    ),
    ToolSpec(
        "tag.remove",
        "write",
        1,
        {"type": "object", "properties": {"path": _PATH, "tags": {"type": "array"}}},
        {"type": "object"},
        cost=0,
    ),
    ToolSpec(
        "link.add",
        "write",
        1,
        {"type": "object", "properties": {"path": _PATH, "target": _PATH}},
        {"type": "object"},
        cost=0,
    ),
)

#: Read-tool name -> invocation function (implementations live in tools.py).
READ_TOOL_IMPLS: dict[str, str] = {
    "vault.list": "vault_list",
    "vault.read": "vault_read",
    "vault.search": "vault_search",
    "metadata.get": "metadata_get",
    "knowledge.related": "knowledge_related",
    "backlinks": "backlinks",
    "outgoing": "outgoing",
    "keyword": "keyword",
}

#: Wire action type -> write tool name (metadata/execution mapping).
ACTION_TO_WRITE_TOOL: dict[str, str] = {
    "create_note": "note.create",
    "patch_note": "note.patch",
    "move_note": "note.move",
    "add_tags": "tag.add",
    "remove_tags": "tag.remove",
    "add_link": "link.add",
}


class ToolRegistry:
    """Static metadata registry with model-side invocation guards."""

    def __init__(self) -> None:
        self._specs = {tool.name: tool for tool in (*READ_TOOLS, *WRITE_TOOLS)}

    def spec(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def names(self) -> list[str]:
        return sorted(self._specs)

    def read_tools(self) -> list[ToolSpec]:
        return [tool for tool in READ_TOOLS]

    def write_tools(self) -> list[ToolSpec]:
        return [tool for tool in WRITE_TOOLS]

    def allowed_read_tools(self, workflow: str) -> list[str]:
        return [
            tool.name
            for tool in READ_TOOLS
            if not tool.allowed_workflows or workflow in tool.allowed_workflows
        ]

    def check_workflow_access(self, workflow: str, name: str) -> ToolSpec:
        tool = self._specs.get(name)
        if tool is None:
            raise InvalidAction(f"unknown tool: {name}", meta={"tool": name})
        if tool.kind != "read":
            raise InvalidAction(
                f"write tool {name} cannot be invoked by a model",
                meta={"tool": name, "workflow": workflow},
            )
        if not tool.allowed_for(workflow):
            raise InvalidAction(
                f"tool {name} is not allowed for workflow {workflow}",
                meta={"tool": name, "workflow": workflow},
            )
        return tool

    def invoke(self, workflow: str, name: str, context: Any, **kwargs: Any) -> Any:
        """Invoke one read tool for ``workflow`` (write tools always denied)."""
        tool = self.check_workflow_access(workflow, name)
        import server.agents.tools as tool_impls

        function = getattr(tool_impls, READ_TOOL_IMPLS[name])
        result = function(context, **kwargs)
        if getattr(tool, "cost", 0):
            context.budget -= tool.cost  # type: ignore[attr-defined]
            if context.budget < 0:  # pragma: no cover - guard for future loops
                raise InvalidAction("tool budget exhausted", meta={"tool": name})
        return result


__all__ = [
    "ACTION_TO_WRITE_TOOL",
    "READ_TOOLS",
    "READ_TOOL_IMPLS",
    "ToolRegistry",
    "ToolSpec",
    "WRITE_TOOLS",
]
