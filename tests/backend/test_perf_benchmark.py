"""M4 optional 10k-note performance benchmark (PLAN-M4 §5.8/M4-14).

Opt-in only: skipped unless ``LOCALNOTE_RUN_PERF=1`` (optionally rescale with
``LOCALNOTE_PERF_NOTES``).  Run e.g.:

    LOCALNOTE_RUN_PERF=1 python -m pytest -q -m perf tests/backend/test_perf_benchmark.py -s

No CI thresholds are enforced — figures are recorded as a regression
reference and optimisation input.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from perf.generate import generate_vault

pytestmark = [
    pytest.mark.perf,
    pytest.mark.skipif(
        os.environ.get("LOCALNOTE_RUN_PERF") != "1",
        reason="performance benchmark is opt-in: set LOCALNOTE_RUN_PERF=1",
    ),
]


def test_perf_benchmark_10k_notes(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    from perf.benchmark import report_json, report_markdown, run_benchmark

    count = int(os.environ.get("LOCALNOTE_PERF_NOTES", "10000"))
    root = tmp_path / "perf-vault"
    generate_vault(root, count=count, seed=20260906)

    report = run_benchmark(root, note_count=count, iterations=5)

    # Record the report as an artifact next to tmp for the dev report.
    artifact_dir = tmp_path / "report"
    artifact_dir.mkdir(exist_ok=True)
    (artifact_dir / "benchmark.json").write_text(report_json(report), encoding="utf-8")
    markdown = report_markdown(report)
    (artifact_dir / "benchmark.md").write_text(markdown, encoding="utf-8")

    # Sanity assertions only (no CI thresholds): the index must actually work.
    assert report["indexed"] > count * 0.9
    assert report["failed"] == 0
    assert all(q["p95_ms"] >= 0 for q in report["queries"].values())

    capsys.readouterr()
    print("\n" + markdown)
    print("\nJSON artifact:", artifact_dir / "benchmark.json")
