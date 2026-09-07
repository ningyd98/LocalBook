"""M4 performance benchmark helpers (PLAN-M4 §5.8/M4-14).

Programmatically generates a deterministic, medium-sized Vault (default
10,000 markdown notes mixing Chinese/English text, frontmatter tags and
properties, wikilinks incl. broken targets) inside a caller-owned directory,
then measures full rebuild, single-note incremental upsert and representative
query latencies (P50/P95), database file size and peak memory.

Not part of the default CI gate: the pytest entry point is skipped unless
``LOCALNOTE_RUN_PERF=1`` (optionally also ``LOCALNOTE_PERF_NOTES`` to rescale).
"""
