#!/usr/bin/env python3
"""Fail Ruff diagnostics on added/changed lines of production Python files."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUFF = ROOT / ".venv" / "bin" / "ruff"


def is_test_path(path: str) -> bool:
    """True when any normalized path segment identifies test code."""
    return "tests" in path.replace("\\", "/").split("/")


def added_line_ranges(diff: str) -> dict[str, list[tuple[int, int]]]:
    """Return added-line intervals from a unified-zero git diff (new-file coordinates)."""
    result: dict[str, list[tuple[int, int]]] = {}
    current: str | None = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
            continue
        if not line.startswith("@@") or current is None or not current.endswith(".py"):
            continue
        match = re.search(r"\+(\d+)(?:,(\d+))?", line)
        if match is None:
            continue
        start = int(match.group(1))
        count = int(match.group(2) or "1")
        # A pure deletion has zero lines in the new file and needs no Ruff check.
        if count:
            result.setdefault(current, []).append((start, start + count - 1))
    return result


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True)


def _comparison_base() -> str:
    base = os.environ.get("RUFF_BASE_REF")
    if not base and os.environ.get("GITHUB_BASE_REF"):
        base = f"origin/{os.environ['GITHUB_BASE_REF']}"
    if not base:
        base = "HEAD^"
    try:
        return _git("merge-base", base, "HEAD").strip()
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"cannot resolve Ruff comparison base {base!r}") from exc


def changed_ranges(base: str) -> dict[str, list[tuple[int, int]]]:
    # One base-to-working-tree diff is intentional: its hunks all use the same
    # current-file coordinates, including committed, staged and unstaged edits.
    patch = _git("diff", "--unified=0", base, "--", "server")
    ranges = added_line_ranges(patch)

    # Untracked files have no base counterpart; check their complete contents.
    untracked = _git("ls-files", "--others", "--exclude-standard", "--", "server")
    for name in untracked.splitlines():
        if name.endswith(".py") and not is_test_path(name):
            path = ROOT / name
            if path.is_file():
                count = sum(1 for _ in path.open(encoding="utf-8"))
                ranges[name] = [(1, max(count, 1))]
    return {name: spans for name, spans in ranges.items() if name.endswith(".py") and not is_test_path(name)}


def main() -> int:
    if not RUFF.is_file():
        print(f"error: {RUFF} missing; run uv sync --dev", file=sys.stderr)
        return 2
    try:
        ranges = changed_ranges(_comparison_base())
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    files = sorted(name for name in ranges if (ROOT / name).is_file())
    if not files:
        print("Ruff strict changed-production-line gate: no changed production Python lines.")
        return 0
    print(f"Ruff strict changed-production-line gate: {len(files)} production files")
    result = subprocess.run(
        [str(RUFF), "check", "--output-format", "json", *files],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    try:
        diagnostics = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(result.stderr, file=sys.stderr)
        return result.returncode or 2

    violations = []
    for diagnostic in diagnostics:
        name = Path(diagnostic["filename"]).resolve().relative_to(ROOT).as_posix()
        row = diagnostic["location"]["row"]
        if any(start <= row <= end for start, end in ranges.get(name, [])):
            violations.append(diagnostic)
    for item in violations:
        name = Path(item["filename"]).resolve().relative_to(ROOT).as_posix()
        print(f"{name}:{item['location']['row']}:{item['location']['column']}: {item['code']} {item['message']}")
    if violations:
        print(f"Found {len(violations)} Ruff issue(s) on changed lines.", file=sys.stderr)
        return 1
    print("No Ruff diagnostics on changed production lines.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
