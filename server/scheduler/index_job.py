"""Index consistency job for M8 (PLAN-M8 §5.7, M8-09).

Read-only comparison between the authoritative Vault (path + sha256 from
``VaultService.list_tree``) and the derived index (``DerivedIndexService``
public queries).  Mismatches are reported, never auto-fixed by default; when
``index.auto_rebuild`` is on and a mismatch is clear-cut the job calls the
existing ``rebuild()`` which rewrites derived rows only, inside the index
lock, and never touches Markdown bytes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter

from server.index.service import DerivedIndexService
from server.vault.service import VaultService


@dataclass
class ConsistencyResult:
    ready: bool
    checked: int
    mismatches: list[str] = field(default_factory=list)
    degraded: bool = False
    rebuilt: bool = False
    duration_ms: float = 0.0
    error: str | None = None


class IndexConsistencyChecker:
    """One bounded consistency pass over Vault vs derived index."""

    def __init__(
        self,
        vault: VaultService | None,
        index: DerivedIndexService | None,
        *,
        auto_rebuild: bool = False,
    ) -> None:
        self.vault = vault
        self.index = index
        self.auto_rebuild = bool(auto_rebuild)

    def check(self, *, allow_rebuild: bool | None = None) -> ConsistencyResult:
        """Compare path set + sha256 hashes; optionally rebuild on mismatch."""
        started = perf_counter()
        rebuild_allowed = self.auto_rebuild if allow_rebuild is None else bool(allow_rebuild)
        if self.vault is None or self.index is None:
            return ConsistencyResult(
                ready=False,
                checked=0,
                degraded=True,
                duration_ms=(perf_counter() - started) * 1000,
                error="index consistency needs a configured Vault and index",
            )
        if self.index.build_state != "ready":
            return ConsistencyResult(
                ready=False,
                checked=0,
                degraded=True,
                duration_ms=(perf_counter() - started) * 1000,
                error="derived index is not ready",
            )
        try:
            tree = self.vault.list_tree(".", include_hidden=False)
        except Exception:
            return ConsistencyResult(
                ready=False,
                checked=0,
                degraded=True,
                duration_ms=(perf_counter() - started) * 1000,
                error="vault listing failed",
            )
        from server.policies.rules import MARKDOWN_SUFFIXES

        vault_notes: dict[str, str] = {}
        for entry in tree:
            path = entry.path
            if not path.casefold().endswith(MARKDOWN_SUFFIXES) or entry.kind != "file":
                continue
            if entry.sha256 is None:
                vault_notes[path] = "<unhashed>"
            else:
                vault_notes[path] = entry.sha256
        try:
            indexed = {item.path: item.sha256 for item in self.index.entries()}
        except Exception:
            return ConsistencyResult(
                ready=False,
                checked=0,
                degraded=True,
                duration_ms=(perf_counter() - started) * 1000,
                error="derived index read failed",
            )
        mismatches: list[str] = []
        for path, digest in vault_notes.items():
            if path not in indexed:
                mismatches.append(f"missing:{path}")
            elif indexed[path] != digest:
                mismatches.append(f"hash:{path}")
        for path in indexed:
            if path not in vault_notes:
                mismatches.append(f"stale:{path}")
        duration_ms = (perf_counter() - started) * 1000
        if mismatches and rebuild_allowed:
            try:
                rebuild_result = self.index.rebuild()
            except Exception:
                return ConsistencyResult(
                    ready=False,
                    checked=len(vault_notes),
                    mismatches=mismatches,
                    degraded=True,
                    duration_ms=(perf_counter() - started) * 1000,
                    error="index rebuild failed; health/Vault unaffected",
                )
            return ConsistencyResult(
                ready=bool(getattr(rebuild_result, "ready", False)),
                checked=len(vault_notes),
                mismatches=mismatches,
                degraded=False,
                rebuilt=True,
                duration_ms=(perf_counter() - started) * 1000,
            )
        return ConsistencyResult(
            ready=not mismatches,
            checked=len(vault_notes),
            mismatches=mismatches,
            degraded=False,
            duration_ms=duration_ms,
        )


def to_message(result: ConsistencyResult) -> str:
    if result.error:
        return result.error
    if result.rebuilt:
        return f"index rebuilt after {len(result.mismatches)} mismatch(es)"
    if result.mismatches:
        return f"index inconsistent ({len(result.mismatches)} mismatch(es))"
    return f"index consistent ({result.checked} notes, {result.duration_ms:.0f} ms)"


__all__ = ["ConsistencyResult", "IndexConsistencyChecker", "to_message"]
