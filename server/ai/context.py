"""Strict, filesystem-independent context assembly."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict


class ContextSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    title: str
    heading: str | None = None
    text: str
    relevance: float | None = None
    truncated: bool = False


class ContextBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notes: list[ContextSource]
    total_chars: int
    omitted_notes: int
    context_version: str = "m6-v1"


@dataclass
class ContextBuilder:
    max_notes: int = 8
    max_chars_per_note: int = 12000
    max_chars_total: int = 60000

    def build(self, sources: list[ContextSource]) -> ContextBundle:
        unique = []
        seen = set()
        total = 0

        def safe(path: str) -> bool:
            p = PurePosixPath(path)
            return (
                bool(path)
                and not path.startswith(("/", "\\"))
                and not path.startswith(".localnote")
                and ".." not in p.parts
                and "\\" not in path
            )

        ordered = sorted(
            enumerate(sources),
            key=lambda it: (
                it[1].path != sources[0].path if sources else False,
                -(it[1].relevance or 0),
                it[1].path.casefold(),
                it[0],
            ),
        )
        for _, source in ordered:
            if source.path in seen or not safe(source.path):
                continue
            seen.add(source.path)
            text = source.text
            if source.heading:
                lines = text.splitlines()
                marker = next(
                    (
                        i
                        for i, line in enumerate(lines)
                        if line.lstrip().startswith("#")
                        and line.lstrip().lstrip("#").strip() == source.heading
                    ),
                    None,
                )
                if marker is not None:
                    level = len(lines[marker]) - len(lines[marker].lstrip())
                    end = len(lines)
                    for i in range(marker + 1, len(lines)):
                        if (
                            lines[i].lstrip().startswith("#")
                            and len(lines[i]) - len(lines[i].lstrip()) <= level
                        ):
                            end = i
                            break
                    text = "\n".join(lines[marker:end])
            truncated = len(text) > self.max_chars_per_note
            text = text[: self.max_chars_per_note]
            if total + len(text) > self.max_chars_total:
                remaining = self.max_chars_total - total
                if remaining <= 0:
                    continue
                text = text[:remaining]
                truncated = True
            item = source.model_copy(update={"text": text, "truncated": truncated})
            unique.append(item)
            total += len(text)
            if len(unique) >= self.max_notes:
                break
        return ContextBundle(
            notes=unique, total_chars=total, omitted_notes=max(0, len(sources) - len(unique))
        )
