"""Citation validation: the anti-hallucination boundary (M14 §九).

The model is allowed to cite only the source ids that appear in the
:class:`~server.rag.schemas.EvidencePack` it was given (``S1``, ``S2`` …). Every
answer therefore passes through :func:`validate_citations` before it is returned:

- markers that name a real source are kept (and reported as *resolved*);
- markers that name anything else (``[S99]``, a file path, a made-up note) are
  removed from the answer text and reported as *invalid*;
- an answer that is entirely unsupported by the retrieved evidence is flagged so
  the API can return the explicit "not enough evidence" wording instead of
  letting the model's prose stand as if it were grounded.

Nothing here trusts the model: the validated source list returned to the client
is always built from the pack, never from the model's output.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from .schemas import CitationReport, EvidencePack, SourceEvidence

# ``[S1]`` / ``[s1]`` / ``[S1, S2]`` / ``[S1][S2]`` in prose.
_CITATION_RE = re.compile(r"\[\s*[Ss](\d{1,3})(?:\s*[,，]\s*[Ss]?(\d{1,3}))*\s*\]")
_UNSUPPORTED_MARKERS = re.compile(r"\[\s*[Ss]\s*\d{1,3}\s*\]")

NOT_ENOUGH_EVIDENCE = "根据当前知识库内容，没有找到足够证据回答这个问题。"
NOT_INDEXED = "知识库索引尚未建立或不可用，因此无法基于你的笔记回答。"


def extract_citations(answer: str) -> list[str]:
    """All distinct ``S<n>`` markers in ``answer``, in first-seen order."""
    found: list[str] = []
    for match in _UNSUPPORTED_MARKERS.finditer(answer or ""):
        token = match.group(0)
        digits = re.sub(r"\D", "", token)
        if not digits:
            continue
        source_id = f"S{int(digits)}"
        if source_id not in found:
            found.append(source_id)
    return found


def validate_citations(
    answer: str, pack: EvidencePack
) -> tuple[str, CitationReport, list[SourceEvidence]]:
    """Strip unsupported markers and report which sources were really used.

    Returns ``(sanitized_answer, report, used_sources)`` where ``used_sources``
    is built **from the pack**, so a hallucinated path can never reach the wire.
    """
    allowed = {source.source_id: source for source in pack.sources}
    report = CitationReport()
    for source_id in extract_citations(answer):
        if source_id in allowed:
            if source_id not in report.valid_ids:
                report.valid_ids.append(source_id)
        elif source_id not in report.invalid_ids:
            report.invalid_ids.append(source_id)

    def _replace(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        source_id = f"S{int(digits)}" if digits else ""
        if source_id in allowed:
            if source_id not in report.resolved_ids:
                report.resolved_ids.append(source_id)
            return f"[{source_id}]"
        return ""

    sanitized = _UNSUPPORTED_MARKERS.sub(_replace, answer or "")
    if report.invalid_ids:
        # Never leave a double space / trailing punctuation artefact behind.
        sanitized = re.sub(r"[ \t]{2,}", " ", sanitized)
        sanitized = re.sub(r"[ \t]+([。，、；：！？.,;:!?])", r"\1", sanitized)
    used = [
        allowed[source_id]
        for source_id in report.resolved_ids
        if source_id in allowed
    ]
    return sanitized.strip(), report, used


def is_supported(answer: str, pack: EvidencePack) -> bool:
    """True when the answer actually cites at least one retrieved source."""
    _, report, _ = validate_citations(answer, pack)
    return bool(report.resolved_ids)


def ensure_grounded_answer(
    answer: str,
    pack: EvidencePack,
    *,
    require_citation: bool = True,
) -> tuple[str, CitationReport, list[SourceEvidence], bool]:
    """Final gate used by the RAG service.

    ``(answer, report, sources, grounded)``. When the model produced no usable
    citation for a non-empty pack, the question is answered with the explicit
    "not enough evidence" wording instead of ungrounded prose.
    """
    sanitized, report, used = validate_citations(answer, pack)
    grounded = bool(used)
    if not pack.sources:
        return NOT_ENOUGH_EVIDENCE, report, [], False
    if require_citation and not grounded:
        return NOT_ENOUGH_EVIDENCE, report, [], False
    return sanitized, report, used, grounded


def dedupe_sources(sources: Iterable[SourceEvidence]) -> list[SourceEvidence]:
    """Keep one entry per source id (order preserved)."""
    seen: set[str] = set()
    result: list[SourceEvidence] = []
    for source in sources:
        if source.source_id in seen:
            continue
        seen.add(source.source_id)
        result.append(source)
    return result


__all__ = [
    "NOT_ENOUGH_EVIDENCE",
    "NOT_INDEXED",
    "dedupe_sources",
    "ensure_grounded_answer",
    "extract_citations",
    "is_supported",
    "validate_citations",
]
