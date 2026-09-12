"""RAG evaluation harness + runnable report (M14 §二十).

Builds the Golden Dataset into a throwaway Vault, indexes it, then scores the
retrieval modes (keyword-only, vector-only, hybrid, hybrid+rerank, hybrid+link)
with Recall@5/10 and MRR.

Two ways to use it:

- the test suite (``tests/rag/test_eval.py``) runs it and enforces floors so a
  chunking/retrieval change cannot silently regress quality;
- ``python -m tests.rag.eval.run`` prints the report table for manual review
  (the same numbers the tests assert, so the report cannot be "fabricated").

The embedding provider is injected: with a real endpoint configured the same
harness measures the real model; the default deterministic provider keeps the
numbers reproducible offline.

``hybrid_link`` (roadmap item ③) adds the link/graph path to the fused pool. It
runs over the *same* candidate pool as every other mode and uses the production
default weights, so the printed delta is the measured effect of the link signal
on this dataset — including the case where that effect is zero or negative, which
the report states instead of hiding.

The run also prints the **tuning record** (``LINK_SWEEP``): the link
configurations that were tried while closing out item ③, with their measured
recall@5/recall@10/MRR on the same pool. It is part of the harness so the
"no gain observed" conclusion can be re-measured by anyone
(``--link-diagnostics`` prints it on demand), and it is asserted by
``tests/rag/test_eval.py`` rather than trusted. Rows whose label starts with
``DIAGNOSTIC`` switch ``link_add_only`` off — a configuration that is *not*
reachable from settings and exists only to reproduce the MRR 0.917 → 0.558
measurement quoted in ``HybridRetriever``; the two product rows
(``hybrid (link off)`` / ``hybrid_link (add-only, shipped default)``) stay
separate from them.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from server.index.db import IndexDatabase
from server.index.service import DerivedIndexService
from server.rag.chunking.markdown import MarkdownChunker
from server.rag.embeddings.base import EmbeddingProvider, HashEmbeddingProvider
from server.rag.embeddings.runner import EmbeddingRunner
from server.rag.index_service import RagIndexService
from server.rag.rerank.base import LexicalOverlapReranker
from server.rag.retrieval.hybrid import HybridRetriever
from server.rag.retrieval.keyword import KeywordRetriever
from server.rag.retrieval.link import LinkRetriever
from server.rag.retrieval.vector import VectorRetriever
from server.rag.vector.sqlite import SqliteVectorStore
from server.vault.service import VaultService

from .golden import DOCUMENTS, GOLDEN, EvaluationReport, score

DEFAULT_CHUNKER = MarkdownChunker(target_tokens=200, max_tokens=320, overlap_tokens=40)


@dataclass(slots=True)
class EvalVault:
    """A temporary Vault + fully built RAG index used by the harness."""

    root: Path
    vault: VaultService
    database: IndexDatabase
    store: SqliteVectorStore
    index: RagIndexService
    provider: EmbeddingProvider
    # M4 note/tag/link/backlink index: the link retriever reads its graph from
    # here (read-only). Built from the same Markdown files as the RAG index.
    m4: DerivedIndexService | None = None

    def close(self) -> None:
        self.index.close()
        if self.m4 is not None:
            self.m4.close()
        self.database.close()


def build_eval_vault(
    root: Path,
    *,
    provider: EmbeddingProvider | None = None,
    chunker: MarkdownChunker | None = None,
) -> EvalVault:
    """Write the Golden Dataset into ``root`` and index it."""
    vault_root = Path(root) / "vault"
    vault_root.mkdir(parents=True, exist_ok=True)
    vault = VaultService(vault_root, watcher_enabled=False)
    vault.initialize(start_watcher=False)
    for path, body in DOCUMENTS.items():
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        if parent:
            parts = parent.split("/")
            for index in range(1, len(parts) + 1):
                segment = "/".join(parts[:index])
                try:
                    vault.create_directory(segment)
                except Exception:
                    pass
        vault.create_bytes(path, body.encode("utf-8"))

    resolved = provider or HashEmbeddingProvider(dimension=256)
    database = IndexDatabase(vault_root / ".localnote" / "index.db")
    database.open()
    store = SqliteVectorStore(database)
    store.ensure_ready()
    # The M4 index (notes/tags/links/backlinks) is a separate derived layer over
    # the same SQLite file; the link retriever needs it and never writes to it.
    m4 = DerivedIndexService(vault)
    m4.rebuild()
    index = RagIndexService(
        vault,
        store,
        chunker=chunker or DEFAULT_CHUNKER,
        embedding_provider=resolved,
        embedding_runner=EmbeddingRunner(resolved),
        embed_batch_size=16,
    )
    index.rebuild()
    return EvalVault(
        root=vault_root,
        vault=vault,
        database=database,
        store=store,
        index=index,
        provider=resolved,
        m4=m4,
    )


def _paths_from_results(results) -> list[str]:
    return [item.path for item in results]


EVAL_MODES = ("keyword", "vector", "hybrid", "hybrid_rerank", "hybrid_link")


def evaluate(
    vault: EvalVault,
    *,
    mode: str = "hybrid",
    top_k: int = 10,
) -> EvaluationReport:
    """Score one retrieval mode over the whole Golden Dataset.

    Every mode retrieves a fixed candidate pool (``candidate_k``, the production
    FTS/vector default of 30) and is then scored on its first ``top_k``
    documents. Scoring all modes on the same pool is what makes "hybrid beats
    either single path" a meaningful comparison rather than an artifact of
    truncating each path differently.

    ``hybrid_link`` fuses the same keyword+vector pool with the production
    link/graph retriever (default weights, ``index=vault.m4``); the extra list can
    only re-rank/add candidates inside that pool, never widen it.
    """
    candidate_k = max(int(top_k) * 3, 30)
    keyword = KeywordRetriever(vault.store)
    vector = VectorRetriever(vault.store, provider=vault.provider)
    hybrid = HybridRetriever(
        keyword=keyword,
        vector=vector,
        link=(
            LinkRetriever(vault.store, index=vault.m4, top_k=candidate_k)
            if mode == "hybrid_link"
            else None
        ),
        fts_top_k=candidate_k,
        vector_top_k=candidate_k,
        link_top_k=candidate_k,
        fusion_top_k=candidate_k,
        # ``hybrid_rerank`` measures the optional stage, so the comparison is
        # fusion-with and fusion-without the local reranker.
        reranker=LexicalOverlapReranker() if mode == "hybrid_rerank" else None,
        rerank_top_k=candidate_k,
    )

    outcomes = []
    for question in GOLDEN:
        if mode == "keyword":
            results = keyword.retrieve(question.question, top_k=candidate_k)
        elif mode == "vector":
            results = vector.retrieve(question.question, top_k=candidate_k)
        elif mode in {"hybrid", "hybrid_rerank", "hybrid_link"}:
            results = hybrid.retrieve(question.question, top_k=candidate_k)
        else:  # pragma: no cover - guarded by the CLI choices
            raise ValueError(f"unknown mode: {mode}")
        paths: list[str] = []
        for item in results:
            if item.path not in paths:
                paths.append(item.path)
        outcomes.append(score(question, paths[: max(int(top_k), 10)], mode=mode))
    return EvaluationReport(mode=mode, outcomes=outcomes)


def evaluate_all(
    vault: EvalVault, *, modes: tuple[str, ...] = EVAL_MODES, top_k: int = 10
) -> dict[str, EvaluationReport]:
    return {mode: evaluate(vault, mode=mode, top_k=top_k) for mode in modes}


def link_path_contribution(vault: EvalVault, *, top_k: int = 10) -> tuple[int, int, int]:
    """How many link candidates the ``hybrid_link`` mode produces / keeps.

    Returns ``(raw_candidates, candidates_kept_by_the_additive_filter, questions)``.
    This is the evidence behind the "neutral"/"gain" verdict: on a corpus whose
    notes are all reachable by the direct paths, the link path can fire (``raw``
    > 0) and still contribute nothing to the fused ranking (``kept`` == 0).
    """
    candidate_k = max(int(top_k) * 3, 30)
    hybrid = HybridRetriever(
        keyword=KeywordRetriever(vault.store),
        vector=VectorRetriever(vault.store, provider=vault.provider),
        link=LinkRetriever(vault.store, index=vault.m4, top_k=candidate_k),
        fts_top_k=candidate_k,
        vector_top_k=candidate_k,
        link_top_k=candidate_k,
        fusion_top_k=candidate_k,
    )
    raw = kept = 0
    for question in GOLDEN:
        outcome = hybrid.search(
            question.question, top_k=candidate_k, include_debug=True
        )
        raw += int((outcome.stats.debug or {}).get("link_candidates_raw", 0))
        kept += int(outcome.stats.link_candidates)
    return raw, kept, len(GOLDEN)


# The tuning record for the link path (roadmap item ③). It lives in the harness
# on purpose: every configuration that was actually tried, and its measured
# effect, is reproducible in the test suite instead of living in a throwaway
# script. An entry is ``(label, LinkRetriever kwargs | None, HybridRetriever
# kwargs)``; ``None`` means "no link path at all" (today's behaviour), and ``{}``
# means "the shipped defaults".
#
# Two kinds of row, deliberately separated by the label:
# - *product* rows describe what ships: ``hybrid (link off)`` and
#   ``hybrid_link (add-only, shipped default)``;
# - ``DIAGNOSTIC`` rows switch ``link_add_only`` off, which is **not reachable
#   from settings** (there is no such setting) and exists only to reproduce the
#   measurement quoted in ``HybridRetriever``'s docstrings (MRR 0.917 → 0.558).
#   ``tests/rag/test_eval.py`` asserts that every label matches the configuration
#   it claims, so an experiment can never be misread as the product.
LINK_SWEEP_BASELINE = "hybrid (link off)"
LINK_SWEEP_DEFAULT = "hybrid_link (add-only, shipped default)"
DIAGNOSTIC_PREFIX = "DIAGNOSTIC link_add_only=False"
LINK_SWEEP: tuple[tuple[str, dict | None, dict], ...] = (
    (LINK_SWEEP_BASELINE, None, {}),
    (LINK_SWEEP_DEFAULT, {}, {}),
    (
        "link additive, wikilinks only (graph=0, tag=0)",
        {"graph_weight": 0.0, "tag_weight": 0.0},
        {"link_add_only": True},
    ),
    (
        "link additive, single best anchor (seed_top_k=1)",
        {"seed_top_k": 1},
        {"link_add_only": True},
    ),
    (
        f"{DIAGNOSTIC_PREFIX}, w=1.0",
        {},
        {"link_add_only": False, "link_rrf_weight": 1.0},
    ),
    (
        f"{DIAGNOSTIC_PREFIX}, w=0.3",
        {},
        {"link_add_only": False, "link_rrf_weight": 0.3},
    ),
    (
        f"{DIAGNOSTIC_PREFIX}, w=0.1",
        {},
        {"link_add_only": False, "link_rrf_weight": 0.1},
    ),
)


def _measure_link_config(
    vault: EvalVault,
    *,
    link_kwargs: dict | None,
    hybrid_kwargs: dict,
    top_k: int = 10,
) -> dict[str, float]:
    """Score one link configuration on the fixed candidate pool of all modes."""
    candidate_k = max(int(top_k) * 3, 30)
    link = (
        LinkRetriever(vault.store, index=vault.m4, top_k=candidate_k, **link_kwargs)
        if link_kwargs is not None
        else None
    )
    hybrid = HybridRetriever(
        keyword=KeywordRetriever(vault.store),
        vector=VectorRetriever(vault.store, provider=vault.provider),
        link=link,
        fts_top_k=candidate_k,
        vector_top_k=candidate_k,
        link_top_k=candidate_k,
        fusion_top_k=candidate_k,
        **hybrid_kwargs,
    )
    outcomes = []
    raw = kept = 0
    for question in GOLDEN:
        outcome = hybrid.search(
            question.question, top_k=candidate_k, include_debug=True
        )
        raw += int((outcome.stats.debug or {}).get("link_candidates_raw", 0))
        kept += int(outcome.stats.link_candidates)
        paths: list[str] = []
        for item in outcome.results:
            if item.path not in paths:
                paths.append(item.path)
        outcomes.append(score(question, paths[: max(int(top_k), 10)]))
    report = EvaluationReport(mode="sweep", outcomes=outcomes)
    return {
        "recall@5": report.recall_at_5,
        "recall@10": report.recall_at_10,
        "mrr": report.mrr,
        "raw": float(raw),
        "kept": float(kept),
    }


def sensitivity_sweep(
    vault: EvalVault,
    *,
    top_k: int = 10,
    sweep: tuple[tuple[str, dict | None, dict], ...] = LINK_SWEEP,
) -> dict[str, dict[str, float]]:
    """Measure every link configuration in the tuning record (label → metrics)."""
    return {
        label: _measure_link_config(
            vault,
            link_kwargs=link_kwargs,
            hybrid_kwargs=hybrid_kwargs,
            top_k=top_k,
        )
        for label, link_kwargs, hybrid_kwargs in sweep
    }


def format_sweep(rows: dict[str, dict[str, float]]) -> str:
    lines = [
        "Link-path tuning record (same candidate pool as every eval mode)",
        "  product rows: 'hybrid (link off)' and "
        "'hybrid_link (add-only, shipped default)'",
        "  DIAGNOSTIC rows set link_add_only=False (no such setting exists); they",
        "  only reproduce the MRR 0.917 -> 0.558 measurement quoted in hybrid.py,",
        "  where the naive full fusion lifts notes the direct paths already ranked.",
    ]
    for label, metrics in rows.items():
        lines.append(
            f"  {label:48s} recall@5={metrics['recall@5']:.3f} "
            f"recall@10={metrics['recall@10']:.3f} mrr={metrics['mrr']:.3f} "
            f"raw={int(metrics['raw'])} kept={int(metrics['kept'])}"
        )
    return "\n".join(lines)


def format_report(reports: dict[str, EvaluationReport]) -> str:
    lines = ["RAG evaluation (Golden Dataset)"]
    for report in reports.values():
        lines.append("  " + report.format())
    hybrid = reports.get("hybrid")
    keyword = reports.get("keyword")
    vector = reports.get("vector")
    reranked = reports.get("hybrid_rerank")
    linked = reports.get("hybrid_link")
    if reranked and hybrid:
        delta = reranked.mrr - hybrid.mrr
        lines.append(
            f"  rerank: MRR={reranked.mrr:.3f} vs fused {hybrid.mrr:.3f} "
            f"({'+' if delta >= 0 else ''}{delta:.3f}); "
            f"recall@5={reranked.recall_at_5:.3f} vs {hybrid.recall_at_5:.3f}"
        )
    if linked and hybrid:
        # Roadmap item ③ is judged on the *measured* delta, including a zero or
        # negative one: the report states the outcome instead of hiding it.
        delta = linked.mrr - hybrid.mrr
        lines.append(
            f"  link: recall@5={linked.recall_at_5:.3f} vs fused "
            f"{hybrid.recall_at_5:.3f}; recall@10={linked.recall_at_10:.3f} vs "
            f"{hybrid.recall_at_10:.3f}; MRR={linked.mrr:.3f} vs {hybrid.mrr:.3f} "
            f"({'+' if delta >= 0 else ''}{delta:.3f})"
        )
        if (
            linked.recall_at_5 < hybrid.recall_at_5
            or linked.recall_at_10 < hybrid.recall_at_10
        ):
            lines.append("  link: REGRESSION (recall below pure hybrid)")
        elif linked.mrr < hybrid.mrr:
            lines.append(
                "  link: tradeoff (recall equal-or-better, MRR lower) — "
                "accepted and reported"
            )
        elif linked.mrr > hybrid.mrr:
            lines.append("  link: gain (recall equal-or-better, MRR higher)")
        else:
            lines.append(
                "  link: neutral (no measurable change on this dataset; the "
                "additive third list never displaced a direct hit — see the "
                "link contribution line below)"
            )
    if hybrid and keyword and vector:
        best_single_mrr = max(keyword.mrr, vector.mrr)
        best_single_recall = max(keyword.recall_at_5, vector.recall_at_5)
        if hybrid.mrr >= best_single_mrr:
            verdict = "ok"
        elif hybrid.recall_at_5 >= best_single_recall:
            # Fusion found *more* of the right documents while ranking one of
            # them a position lower: a real, reportable trade-off rather than a
            # failure the report should hide.
            verdict = "tradeoff (recall equal-or-better, MRR lower)"
        else:
            verdict = "REGRESSION"
        lines.append(
            f"  hybrid recall@5={hybrid.recall_at_5:.3f} (best single "
            f"{best_single_recall:.3f}), MRR={hybrid.mrr:.3f} "
            f"(best single {best_single_mrr:.3f}) -> {verdict}"
        )
    return "\n".join(lines)


# Regression floors. Deliberately below the measured values (see CHANGELOG M14):
# they exist to catch a real quality drop, not to certify an exact score. Keep
# them in sync with tests/rag/test_eval.py.
#
# ``hybrid_link`` gets the same *recall* floors as pure hybrid — the link signal
# may add rank but must never cost evidence — and deliberately no MRR floor: the
# measured effect of the link path on this dataset is reported by the harness
# (and asserted against pure hybrid in tests/rag/test_eval.py) rather than being
# frozen into a threshold tuned to one run.
FLOORS: dict[str, dict[str, float]] = {
    "hybrid": {"recall@5": 0.90, "recall@10": 0.95, "mrr": 0.80},
    "hybrid_link": {"recall@5": 0.90, "recall@10": 0.95},
    "keyword": {"recall@10": 0.85},
    "vector": {"recall@10": 0.85},
}


def check_floors(reports: dict[str, EvaluationReport]) -> list[str]:
    """Return the list of violated floors (empty means the gate passed).

    Two kinds of rule, both evaluated on **every** call:

    - absolute floors (:data:`FLOORS`) — regression guard rails for each mode;
    - a *relative* rule that cannot be expressed as a frozen threshold: the link
      path may not degrade the fused ranking, i.e.
      ``reports["hybrid_link"].mrr >= reports["hybrid"].mrr``. The baseline is the
      pure hybrid of **the same run**; there is deliberately no
      ``baseline_mrr=`` parameter (a caller-supplied baseline could disagree with
      the configuration that actually ran) and no literal MRR constant (a value
      frozen from one run would be exactly the tuning this check must not do).

    Division of labour with ``tests/rag/test_eval.py``: this gate enforces the
    measurably-bad case (link below pure hybrid), while the test suite pins the
    wider tuning record (every ``LINK_SWEEP`` row must be ``<=`` the baseline).
    ``main`` computes this list unconditionally, so ``--check`` — like
    ``--no-tests`` — can never turn the rules off.
    """
    violations: list[str] = []
    for mode, limits in FLOORS.items():
        report = reports.get(mode)
        if report is None:
            continue
        measured = {
            "recall@5": report.recall_at_5,
            "recall@10": report.recall_at_10,
            "mrr": report.mrr,
        }
        for metric, floor in limits.items():
            if measured[metric] < floor:
                violations.append(
                    f"{mode} {metric}={measured[metric]:.3f} < floor {floor:.2f}"
                )
    pure = reports.get("hybrid")
    linked = reports.get("hybrid_link")
    if pure is not None and linked is not None and linked.mrr < pure.mrr:
        violations.append(
            f"hybrid_link mrr={linked.mrr:.3f} < hybrid mrr={pure.mrr:.3f} "
            "(the link path must not degrade the fused ranking)"
        )
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the RAG Golden Dataset evaluation")
    parser.add_argument("--vault", required=True, help="throwaway directory for the eval Vault")
    parser.add_argument("--mode", default="all", choices=["all", *EVAL_MODES])
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument(
        "--link-diagnostics",
        action="store_true",
        help=(
            "also print the link-path tuning record, including the non-additive "
            "'DIAGNOSTIC link_add_only=False' configurations that reproduce the "
            "MRR 0.917 -> 0.558 measurement (not part of the product)"
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "exit non-zero on a floor violation; the rules themselves are always "
            "evaluated and printed, so this flag only controls the exit code"
        ),
    )
    args = parser.parse_args(argv)

    vault = build_eval_vault(Path(args.vault))
    violations: list[str] = []
    try:
        modes = EVAL_MODES if args.mode == "all" else (args.mode,)
        reports = evaluate_all(vault, modes=modes, top_k=args.top_k)
        print(format_report(reports))
        if "hybrid_link" in reports:
            raw, kept, questions = link_path_contribution(vault, top_k=args.top_k)
            print(
                f"  link contribution: raw candidates={raw} kept by the additive "
                f"filter={kept} over {questions} questions"
            )
        # The record is printed with the product modes and on demand, but the
        # DIAGNOSTIC rows are always labelled as such.
        if args.link_diagnostics or "hybrid_link" in reports:
            print(format_sweep(sensitivity_sweep(vault, top_k=args.top_k)))
        for mode in ("keyword", "vector"):
            if mode in reports:
                for outcome in reports[mode].outcomes:
                    if not outcome.hit:
                        print(f"  [{mode}] MISS: {outcome.question}")
        # Evaluated on every run, whatever the flags are: a gate that a caller
        # can switch off by leaving an argument out is not a gate, and
        # ``--no-tests`` (the mode used to transcribe numbers into the docs) runs
        # exactly this code. ``--check`` only decides whether a violation changes
        # the exit code.
        violations = check_floors(reports)
        if violations:
            print("REGRESSION:")
            for violation in violations:
                print(f"  {violation}")
        print(
            "  floors: "
            + ("FAILED" if violations else "OK")
            + " (absolute floors + hybrid_link-vs-hybrid MRR, evaluated on every "
            "run; --check controls only the exit code)"
        )
    finally:
        vault.close()
    return 1 if (violations and args.check) else 0


__all__ = [
    "DEFAULT_CHUNKER",
    "DIAGNOSTIC_PREFIX",
    "EVAL_MODES",
    "FLOORS",
    "LINK_SWEEP",
    "LINK_SWEEP_BASELINE",
    "LINK_SWEEP_DEFAULT",
    "check_floors",
    "EvalVault",
    "build_eval_vault",
    "evaluate",
    "evaluate_all",
    "format_report",
    "format_sweep",
    "link_path_contribution",
    "main",
    "sensitivity_sweep",
]


if __name__ == "__main__":  # pragma: no cover - manual entry point
    raise SystemExit(main())
