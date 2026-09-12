"""RAG prompt loading (M14 §九).

RAG prompts use the same Markdown-with-frontmatter format as the M6
``server/ai/prompts`` registry (``---`` metadata, ``name``/``version``/
``input_schema``/``output_schema``), but their schemas live in
``server.rag.api_schemas``. Rather than teaching the M6 registry about a second
schema module — which would couple the read-only AI workflows to RAG — this
module validates the same metadata against the RAG schemas.

The file format, strictness and failure behaviour are identical; only the
resolver differs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .. import api_schemas

PROMPTS_DIR = Path(__file__).parent
_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]*")
_VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_REQUIRED = ("name", "version", "input_schema", "output_schema")


@dataclass(frozen=True, slots=True)
class RagPrompt:
    name: str
    version: str
    input_schema: str
    output_schema: str
    body: str

    @property
    def prompt_version(self) -> str:
        return f"{self.name}@{self.version}"


def _schema_resolves(name: str) -> bool:
    return isinstance(getattr(api_schemas, name, None), type)


def load_rag_prompt(path: str | Path) -> RagPrompt:
    """Load and validate one RAG prompt file (raises ``ValueError`` when invalid)."""
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError("Invalid prompt metadata")
    try:
        head, body = text[4:].split("\n---\n", 1)
    except ValueError as exc:
        raise ValueError("Invalid prompt metadata") from exc
    values: dict[str, str] = {}
    for line in head.splitlines():
        if ":" not in line:
            raise ValueError("Invalid prompt metadata line")
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip()
    if set(values) != set(_REQUIRED) or any(not values[key] for key in _REQUIRED):
        raise ValueError("Incomplete prompt metadata")
    if not _NAME_RE.fullmatch(values["name"]):
        raise ValueError("Invalid prompt name")
    if not _VERSION_RE.fullmatch(values["version"]):
        raise ValueError("Invalid prompt version")
    if not _schema_resolves(values["input_schema"]) or not _schema_resolves(
        values["output_schema"]
    ):
        raise ValueError("Prompt schema is not parseable")
    if not body.strip():
        raise ValueError("Prompt body is empty")
    return RagPrompt(*(values[key] for key in _REQUIRED), body.strip())


def load_prompt_by_name(name: str) -> RagPrompt:
    """Load ``server/rag/prompts/<name>.md`` through the RAG validator."""
    return load_rag_prompt(PROMPTS_DIR / f"{name}.md")


__all__ = ["PROMPTS_DIR", "RagPrompt", "load_prompt_by_name", "load_rag_prompt"]
