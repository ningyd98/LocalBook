"""Benchmark harness shared by the perf test (PLAN-M4 §5.8).

Measures, against one generated Vault:

- full ``rebuild()`` wall time (and per-note amortisation);
- single-note incremental upsert latency (a simulated watcher modify);
- representative query families: English FTS, Chinese 2-char (substring
  degradation), tag, basename/autocomplete-ish, outgoing/backlinks read;
  each reports P50/P95 over repeated runs;
- ``index.db`` size on disk;
- peak Python memory during rebuild (``tracemalloc``).

Guidance targets (not CI-enforced, environment-dependent): single-note
open/upsert < 100ms, keyword search < 300ms, autocomplete-like < 100ms.
"""

from __future__ import annotations

import json
import time
import tracemalloc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from server.index.service import DerivedIndexService
from server.links.service import LinksService
from server.search.service import SearchService
from server.vault.events import VaultEvent
from server.vault.service import VaultService

_QUERY_FAMILIES = {
    "english_fts": "hello world",
    "english_basename_like": "note",
    "chinese_two_char": "你好",
    "tag": "工作",
    "chinese_phrase": "机器学习 全文检索",
    "emoji": "😀",
}

_REPORT_KEYS = (
    "rebuild_seconds",
    "rebuild_per_note_ms",
    "upsert_p50_ms",
    "db_size_bytes",
    "peak_memory_mb",
    "queries",
)


@dataclass
class QueryTiming:
    path: str
    total_hits: int
    samples_ms: list[float] = field(default_factory=list)

    def p(self, percentile: float) -> float:
        ordered = sorted(self.samples_ms)
        if not ordered:
            return 0.0
        index = max(0, min(len(ordered) - 1, int(round(percentile / 100 * (len(ordered) - 1)))))
        return ordered[index]


def _p50_p95(samples: list[float]) -> tuple[float, float]:
    if not samples:
        return 0.0, 0.0
    ordered = sorted(samples)
    def pct(p: float) -> float:
        idx = max(0, min(len(ordered) - 1, int(round(p / 100 * (len(ordered) - 1)))))
        return ordered[idx]
    return pct(50.0), pct(95.0)


def run_benchmark(vault_root: Path, *, note_count: int, iterations: int = 5) -> dict[str, Any]:
    """Run the M4 benchmark against ``vault_root`` and return a report dict."""
    service = VaultService(vault_root)
    service.initialize(start_watcher=False)
    index = DerivedIndexService(service)
    links = LinksService(index)
    search = SearchService(index)

    # ---- full rebuild -----------------------------------------------------
    # Time WITHOUT tracemalloc: tracing every Python allocation distorts the
    # numbers by ~10x.  Peak memory is measured on a separate rebuild below.
    started = time.perf_counter()
    rebuild_result = index.rebuild()
    rebuild_seconds = time.perf_counter() - started
    assert rebuild_result.ready is True

    # Peak memory during a *tracing* rebuild (allocations are counted, not
    # timed; the DB is simply rebuilt again over the same Vault).
    tracemalloc.start()
    try:
        index.rebuild()
    finally:
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

    db_path = index.db_path
    db_size = db_path.stat().st_size if db_path is not None and db_path.exists() else 0

    # ---- single-note incremental upsert (simulated watcher modify) --------
    files = sorted(e.path for e in service.list_tree("") if e.kind == "file")
    if not files:
        files = ["shard-000/note-00000.md"]
    upsert_samples: list[float] = []
    probe = files[0]
    for _ in range(max(2, iterations)):
        _data, digest = service.read_bytes(probe)
        service.write_bytes(probe, _data + b"\n# touch\n", digest)
        t0 = time.perf_counter()
        index.handle_event(VaultEvent.modified(probe))
        upsert_samples.append((time.perf_counter() - t0) * 1000.0)
    upsert_p50, _upsert_p95 = _p50_p95(upsert_samples)

    # ---- query families ----------------------------------------------------
    query_results: dict[str, QueryTiming] = {}
    for label, query in _QUERY_FAMILIES.items():
        timing = QueryTiming(path=query, total_hits=0)
        for _ in range(iterations):
            t0 = time.perf_counter()
            response = search.search(query)
            timing.samples_ms.append((time.perf_counter() - t0) * 1000.0)
            timing.total_hits = response.total
        query_results[label] = timing

    # outgoing + backlinks reads (UI hot paths)
    for _label, note in (("outgoing_read", "notes/Alpha.md"),):
        if index.entry(note) is None and files:
            note = files[0]
        timing = QueryTiming(path=note, total_hits=0)
        for _ in range(iterations):
            t0 = time.perf_counter()
            response = links.outgoing(note)
            timing.samples_ms.append((time.perf_counter() - t0) * 1000.0)
            timing.total_hits = len(response.outgoing)
        query_results[_label] = timing

    index.close()

    queries = {}
    for label, timing in sorted(query_results.items()):
        p50, p95 = _p50_p95(timing.samples_ms)
        queries[label] = {
            "q": timing.path,
            "hits": timing.total_hits,
            "p50_ms": round(p50, 3),
            "p95_ms": round(p95, 3),
        }

    return {
        "note_count": note_count,
        "rebuild_seconds": round(rebuild_seconds, 3),
        "rebuild_per_note_ms": round(rebuild_seconds * 1000.0 / max(1, note_count), 4),
        "upsert_p50_ms": round(upsert_p50, 3),
        "upsert_samples": [round(v, 3) for v in upsert_samples],
        "db_size_bytes": db_size,
        "peak_memory_mb": round(peak / (1024 * 1024), 2),
        "queries": queries,
        "indexed": rebuild_result.indexed,
        "failed": rebuild_result.failed,
        "skipped": rebuild_result.skipped,
        "duration_ms": rebuild_result.duration_ms,
    }


def report_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# LocalNote M4 performance benchmark",
        "",
        f"- notes generated: {report['note_count']}",
        f"- full rebuild: **{report['rebuild_seconds']} s** "
        f"({report['rebuild_per_note_ms']} ms/note amortised)",
        f"- single-note incremental upsert P50: **{report['upsert_p50_ms']} ms**",
        f"- index.db size: {report['db_size_bytes']} bytes",
        f"- peak memory during rebuild: {report['peak_memory_mb']} MB",
        f"- rebuild response: indexed={report['indexed']} failed={report['failed']} "
        f"skipped={report['skipped']}",
        "",
        "| query family | latency P50 (ms) | P95 (ms) | hits |",
        "|---|---|---|---|",
    ]
    for label, value in sorted(report["queries"].items()):
        lines.append(
            f"| {label} ({value['q']}) | {value['p50_ms']} | {value['p95_ms']} "
            f"| {value['hits']} |"
        )
    lines += [
        "",
        "Guidance targets (informational, environment-sensitive): single-note",
        "open/upsert < 100ms; keyword search < 300ms; autocomplete-like < 100ms.",
    ]
    return "\n".join(lines)


def report_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True)


__all__ = ["QueryTiming", "report_json", "report_markdown", "run_benchmark"]
