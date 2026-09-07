"""Versioned markdown prompt registry with strict metadata validation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    input_schema: str
    output_schema: str
    body: str

    @property
    def prompt_version(self) -> str:
        return f"{self.name}@{self.version}"


def _schema_is_parseable(value: str) -> bool:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        # Prompt files use stable schema class names; confirm they resolve to
        # actual Pydantic models rather than accepting arbitrary text.
        from . import schemas

        model = getattr(schemas, value, None)
        return isinstance(value, str) and model is not None and hasattr(model, "model_json_schema")
    return isinstance(parsed, dict)


def load_prompt(path: Path) -> Prompt:
    text = path.read_text(encoding="utf-8")
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
    required = ("name", "version", "input_schema", "output_schema")
    if set(values) != set(required) or any(not values[key] for key in required):
        raise ValueError("Incomplete prompt metadata")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", values["name"]):
        raise ValueError("Invalid prompt name")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", values["version"]):
        raise ValueError("Invalid prompt version")
    if not _schema_is_parseable(values["input_schema"]) or not _schema_is_parseable(
        values["output_schema"]
    ):
        raise ValueError("Prompt schema is not parseable")
    if not body.strip():
        raise ValueError("Prompt body is empty")
    return Prompt(*(values[key] for key in required), body.strip())


class PromptRegistry:
    def __init__(self, directory: Path | None = None):
        self.directory = directory or Path(__file__).parent / "prompts"

    def all(self) -> list[Prompt]:
        prompts = [
            load_prompt(path)
            for path in sorted(self.directory.glob("*.md"))
            if path.name != "README.md"
        ]
        keys = [(prompt.name, prompt.version) for prompt in prompts]
        if len(keys) != len(set(keys)):
            raise ValueError("Prompt name/version must be unique")
        return prompts

    def get(self, name: str, version: str | None = None) -> Prompt:
        prompts = [prompt for prompt in self.all() if prompt.name == name]
        if version is not None:
            prompts = [prompt for prompt in prompts if prompt.version == version]
        if len(prompts) != 1:
            raise KeyError(f"Prompt not found or ambiguous: {name}")
        return prompts[0]
