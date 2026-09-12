"""Phase 11 — Golden-dataset evaluation gates (M14 §二十)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.rag.eval.golden import DOCUMENTS, GOLDEN, score
from tests.rag.eval.run import build_eval_vault, evaluate, evaluate_all, format_report

# Floors measured with the deterministic offline embedder (see the dev report).
# They exist to catch *regressions* — a chunking, fusion or prompt change that
# makes retrieval worse must fail here instead of shipping quietly. They are
# deliberately below the measured values so normal variation stays green.
MIN_HYBRID_RECALL_AT_5 = 0.9
MIN_HYBRID_RECALL_AT_10 = 0.95
MIN_KEYWORD_RECALL_AT_10 = 0.85  # lexical path alone, without embeddings
MIN_VECTOR_RECALL_AT_10 = 0.85
MIN_HYBRID_MRR = 0.8


@pytest.fixture(scope="module")
def eval_vault(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("rag-eval")
    vault = build_eval_vault(Path(root))
    try:
        yield vault
    finally:
        vault.close()


def test_golden_dataset_is_well_formed() -> None:
    assert len(DOCUMENTS) >= 10
    assert len(GOLDEN) >= 10
    for question in GOLDEN:
        assert question.expected_paths
        for path in question.expected_paths:
            assert path in DOCUMENTS, f"{path} is not part of the corpus"


def test_eval_vault_indexes_every_document(eval_vault) -> None:
    paths = set(eval_vault.store.document_paths())
    assert set(DOCUMENTS) <= paths
    assert eval_vault.store.chunk_count() >= len(DOCUMENTS)
    assert eval_vault.store.embedded_count() == eval_vault.store.chunk_count()


def test_report_metrics_are_computed(eval_vault) -> None:
    reports = evaluate_all(eval_vault, modes=("keyword", "vector", "hybrid"), top_k=10)
    report = reports["hybrid"]
    assert report.total == len(GOLDEN)
    assert 0.0 <= report.recall_at_5 <= 1.0
    assert 0.0 <= report.recall_at_10 <= 1.0
    assert 0.0 <= report.mrr <= 1.0
    assert report.recall_at_10 >= report.recall_at_5
    as_dict = report.as_dict()
    assert set(as_dict) == {"mode", "questions", "recall@5", "recall@10", "mrr", "misses"}
    assert "hybrid" in format_report(reports)


def test_single_path_recall_is_measurable_and_floors_hold(eval_vault) -> None:
    keyword = evaluate(eval_vault, mode="keyword")
    vector = evaluate(eval_vault, mode="vector")
    hybrid = evaluate(eval_vault, mode="hybrid")
    print("\n" + format_report({"keyword": keyword, "vector": vector, "hybrid": hybrid}))

    assert keyword.recall_at_10 >= MIN_KEYWORD_RECALL_AT_10, keyword.format()
    assert vector.recall_at_10 >= MIN_VECTOR_RECALL_AT_10, vector.format()
    assert hybrid.recall_at_5 >= MIN_HYBRID_RECALL_AT_5, hybrid.format()
    assert hybrid.recall_at_10 >= MIN_HYBRID_RECALL_AT_10, hybrid.format()
    assert hybrid.mrr >= MIN_HYBRID_MRR, hybrid.format()


def test_optional_rerank_is_measurable_and_does_not_hurt_recall(eval_vault) -> None:
    """The optional stage is scored, not assumed: it may only add ranking gain."""
    fused = evaluate(eval_vault, mode="hybrid")
    reranked = evaluate(eval_vault, mode="hybrid_rerank")
    fused_mrr, reranked_mrr = fused.mrr, reranked.mrr
    print(
        f"\n[rerank] fused MRR={fused_mrr:.3f} recall@5={fused.recall_at_5:.3f} "
        f"| reranked MRR={reranked_mrr:.3f} recall@5={reranked.recall_at_5:.3f}"
    )
    # Recall must never drop: a reranker that loses evidence is a regression.
    assert reranked.recall_at_5 >= fused.recall_at_5
    assert reranked.recall_at_10 >= fused.recall_at_10
    # Measured gain with the local heuristic reranker on this dataset.
    assert reranked_mrr >= fused_mrr


def test_hybrid_is_not_worse_than_each_single_path(eval_vault) -> None:
    """Fusion must dominate both paths on recall, the metric that decides
    whether the right note is *available* to the answer generator."""
    keyword = evaluate(eval_vault, mode="keyword")
    vector = evaluate(eval_vault, mode="vector")
    hybrid = evaluate(eval_vault, mode="hybrid")
    assert hybrid.recall_at_5 >= max(keyword.recall_at_5, vector.recall_at_5)
    assert hybrid.recall_at_10 >= max(keyword.recall_at_10, vector.recall_at_10)


# ----------------------------------------------------------------------
# Link/graph path (roadmap item ③)
# ----------------------------------------------------------------------


def test_link_mode_is_part_of_the_harness() -> None:
    from tests.rag.eval.run import EVAL_MODES, FLOORS

    assert "hybrid_link" in EVAL_MODES
    # Same *recall* floor as pure hybrid: adding the link signal must never cost
    # evidence. Deliberately no MRR floor — a measured MRR trade-off is reported
    # (see the test below), not frozen into a threshold tuned to one run.
    assert FLOORS["hybrid_link"]["recall@5"] == FLOORS["hybrid"]["recall@5"]
    assert FLOORS["hybrid_link"]["recall@10"] == FLOORS["hybrid"]["recall@10"]
    assert "mrr" not in FLOORS["hybrid_link"]


def test_eval_vault_builds_the_graph_the_link_path_reads(eval_vault) -> None:
    """The link path is measured against a real derived graph, not an empty one."""
    assert eval_vault.m4 is not None
    snapshot = eval_vault.m4.graph_snapshot()
    resolved = [row for row in snapshot.links if row.resolved_path and not row.broken]
    assert resolved, "the Golden Dataset must contain resolvable wikilinks"
    assert any(row.tag_folded for row in snapshot.tags)


def test_link_path_returns_candidates_on_the_golden_dataset(eval_vault) -> None:
    """The link retriever must actually fire — otherwise "neutral" would only
    mean the path was never exercised."""
    from server.rag.retrieval.link import LinkRetriever

    link = LinkRetriever(eval_vault.store, index=eval_vault.m4)
    hits = link.retrieve("哪篇笔记讨论了快慢双系统？", top_k=10)
    assert hits, link.last_degraded
    # The anchor note itself is not returned: the query already found it.
    assert all(hit.path != "Thesis/cloud-edge.md" for hit in hits)
    assert all(hit.source in {"link", "graph"} for hit in hits)


def test_link_path_is_measurable_and_never_costs_recall(eval_vault) -> None:
    """Roadmap item ③ is scored, not assumed: the link list may only *add*
    evidence on top of pure hybrid.

    The Golden Dataset already saturates recall@5/10 for pure hybrid (this corpus
    is fully reachable lexically and semantically), so the acceptance criterion
    that can bite is "recall must not drop"; the MRR delta is printed and, when it
    is negative, must be visible in the report instead of being hidden.
    """
    fused = evaluate(eval_vault, mode="hybrid")
    linked = evaluate(eval_vault, mode="hybrid_link")
    text = format_report({"hybrid": fused, "hybrid_link": linked})
    print(
        f"\n[link] fused recall@5={fused.recall_at_5:.3f} "
        f"recall@10={fused.recall_at_10:.3f} MRR={fused.mrr:.3f} | link "
        f"recall@5={linked.recall_at_5:.3f} "
        f"recall@10={linked.recall_at_10:.3f} MRR={linked.mrr:.3f}"
    )
    assert linked.recall_at_5 >= fused.recall_at_5, linked.format()
    assert linked.recall_at_10 >= fused.recall_at_10, linked.format()
    assert "link:" in text
    if linked.mrr < fused.mrr:
        assert "tradeoff" in text or "REGRESSION" in text
    else:
        assert any(word in text for word in ("gain", "neutral", "ok"))


def test_link_sweep_labels_match_the_configuration_they_claim() -> None:
    """A row label must describe the configuration that actually runs.

    Guards the "experiment mistaken for product" failure mode: the two product
    rows are the shipped ones, and every ``DIAGNOSTIC`` row really passes
    ``link_add_only=False`` (a configuration the settings API cannot produce).
    """
    from tests.rag.eval.run import (
        DIAGNOSTIC_PREFIX,
        LINK_SWEEP,
        LINK_SWEEP_BASELINE,
        LINK_SWEEP_DEFAULT,
    )

    assert LINK_SWEEP[0][0] == LINK_SWEEP_BASELINE
    assert LINK_SWEEP[1][0] == LINK_SWEEP_DEFAULT
    diagnostic_rows = 0
    for label, link_kwargs, hybrid_kwargs in LINK_SWEEP:
        if link_kwargs is None:
            assert "link off" in label
        elif label.startswith(DIAGNOSTIC_PREFIX):
            diagnostic_rows += 1
            assert "link_add_only=False" in label
            assert hybrid_kwargs.get("link_add_only") is False
        else:
            # Every other row describes product behaviour: additive.
            assert hybrid_kwargs.get("link_add_only", True) is True
            assert label.startswith("link additive") or label == LINK_SWEEP_DEFAULT
    assert diagnostic_rows >= 1, "the 0.917 -> 0.558 experiment must stay reproducible"


def test_link_tuning_record_never_costs_recall(eval_vault) -> None:
    """The tuning that was actually tried is measured and printed, not hidden.

    Every configuration in the record must keep recall at least at the pure-hybrid
    baseline (the acceptance criterion: the link signal may add evidence, never
    lose it), and the shipped default must not rank worse either. The record also
    pins the honest conclusion that ships with the feature: on this corpus **no
    link configuration beats pure hybrid** on MRR (the default is neutral, the
    non-additive DIAGNOSTIC rows are strictly worse). If a future change ever
    produces a gain, this test fails and forces the report to be updated instead
    of leaving a stale "no gain" claim in the docs.
    """
    from tests.rag.eval.run import (
        DIAGNOSTIC_PREFIX,
        LINK_SWEEP,
        LINK_SWEEP_BASELINE,
        LINK_SWEEP_DEFAULT,
        format_sweep,
        sensitivity_sweep,
    )

    rows = sensitivity_sweep(eval_vault)
    print("\n" + format_sweep(rows))
    baseline = rows[LINK_SWEEP_BASELINE]
    for label, metrics in rows.items():
        assert metrics["recall@5"] >= baseline["recall@5"], label
        assert metrics["recall@10"] >= baseline["recall@10"], label
    default = rows[LINK_SWEEP_DEFAULT]
    assert default["recall@5"] == baseline["recall@5"]
    assert default["recall@10"] == baseline["recall@10"]
    assert default["mrr"] >= baseline["mrr"]
    # The quoted experiment must reproduce: naive full fusion ranks strictly worse.
    diagnostic = [
        metrics for label, metrics in rows.items() if label.startswith(DIAGNOSTIC_PREFIX)
    ]
    assert diagnostic
    assert all(metrics["mrr"] < baseline["mrr"] for metrics in diagnostic)
    assert all(metrics["mrr"] <= baseline["mrr"] for metrics in rows.values())
    assert len(rows) == len(LINK_SWEEP)


def test_link_path_contribution_is_measured(eval_vault) -> None:
    """The "neutral" verdict is explained by numbers, not asserted away."""
    from tests.rag.eval.run import link_path_contribution

    raw, kept, questions = link_path_contribution(eval_vault)
    print(f"\n[link] raw candidates={raw} kept={kept} over {questions} questions")
    assert questions == len(GOLDEN)
    assert raw > 0, "the link path never fired on this corpus"
    assert 0 <= kept <= raw


def test_semantic_and_lexical_paths_rank_differently(eval_vault) -> None:
    """The two paths must behave *measurably differently*, or the fusion has no
    reason to exist.

    The dataset declares which queries are intended as semantic-only or
    lexical-only; the assertion is on observable behaviour (the ranked document
    lists differ, and the paths disagree on at least one question), not on an
    idealized claim that one path can never find a given note — the lexical
    fallback's bigram matching does reach questions a bare `MATCH` could not.
    """
    keyword = evaluate(eval_vault, mode="keyword")
    vector = evaluate(eval_vault, mode="vector")
    disagreements = [
        outcome.question
        for outcome in keyword.outcomes
        for other in vector.outcomes
        if other.question == outcome.question
        and other.retrieved_paths[:5] != outcome.retrieved_paths[:5]
    ]
    assert disagreements, "the two retrieval paths rank identically everywhere"
    assert any(item.semantic_only for item in GOLDEN)
    assert any(item.lexical_only for item in GOLDEN)


def test_mrr_degradation_is_reported_not_hidden(eval_vault) -> None:
    """Hybrid MRR may trail the strongest single path; the report must say so."""
    reports = evaluate_all(eval_vault, modes=("keyword", "vector", "hybrid"), top_k=10)
    text = format_report(reports)
    best_single_mrr = max(reports["keyword"].mrr, reports["vector"].mrr)
    best_single_recall = max(reports["keyword"].recall_at_5, reports["vector"].recall_at_5)
    if reports["hybrid"].mrr >= best_single_mrr:
        assert "ok" in text
    elif reports["hybrid"].recall_at_5 >= best_single_recall:
        assert "tradeoff" in text
    else:
        assert "REGRESSION" in text
    # Either way the per-question detail is available for humans.
    assert reports["hybrid"].outcomes[0].retrieved_paths is not None


def test_scoring_helper_flags_hits_and_misses() -> None:
    from tests.rag.eval.golden import GoldenQuestion

    question = GoldenQuestion(question="q", expected_paths=["b.md"])
    hit = score(question, ["a.md", "b.md"])
    assert hit.recall_at_5 == 1.0 and hit.reciprocal_rank == 0.5 and hit.hit
    miss = score(question, ["a.md", "c.md"])
    assert miss.recall_at_5 == 0.0
    assert miss.reciprocal_rank == 0.0 and not miss.hit


def test_scoring_uses_first_relevant_rank() -> None:
    from tests.rag.eval.golden import GoldenQuestion

    question = GoldenQuestion(question="q", expected_paths=["c.md"])
    outcome = score(question, ["a.md", "b.md", "c.md"])
    assert outcome.reciprocal_rank == pytest.approx(1 / 3)
    assert outcome.recall_at_5 == 1.0


# ---------------------------------------------------------------------------
# The regression gate itself (M14 ③): rules evaluated on every run, never
# switchable off by leaving a flag out, and never a value frozen from one run.
# ---------------------------------------------------------------------------


def _synthetic_report(mode: str, *, mrr: float, recall_at_5: float = 1.0,
                      recall_at_10: float = 1.0):
    """A report with controlled metrics, so a gate rule can be made to fail."""
    from tests.rag.eval.golden import EvaluationReport, QuestionOutcome

    outcomes = [
        QuestionOutcome(
            question=f"q{index}",
            recall_at_5=recall_at_5,
            recall_at_10=recall_at_10,
            reciprocal_rank=mrr,
            mode=mode,
        )
        for index in range(2)
    ]
    return EvaluationReport(mode=mode, outcomes=outcomes)


def test_check_floors_flags_a_link_path_mrr_regression() -> None:
    """The relative rule fires, and says which two measurements disagree."""
    from tests.rag.eval.run import check_floors

    violations = check_floors({
        "hybrid": _synthetic_report("hybrid", mrr=0.9),
        "hybrid_link": _synthetic_report("hybrid_link", mrr=0.5),
    })
    # Only the relative rule can be the culprit: every absolute floor is met.
    assert len(violations) == 1, violations
    text = violations[0]
    assert "hybrid_link" in text and "hybrid" in text
    assert "0.500" in text and "0.900" in text
    assert "must not degrade the fused ranking" in text


def test_check_floors_accepts_equal_or_better_link_mrr() -> None:
    """Equal is enough: the rule forbids degradation, not parity."""
    from tests.rag.eval.run import check_floors

    assert check_floors({
        "hybrid": _synthetic_report("hybrid", mrr=0.9),
        "hybrid_link": _synthetic_report("hybrid_link", mrr=0.9),
    }) == []
    assert check_floors({
        "hybrid": _synthetic_report("hybrid", mrr=0.9),
        "hybrid_link": _synthetic_report("hybrid_link", mrr=0.95),
    }) == []
    # Absolute floors still bite for the modes that are present.
    assert check_floors({"hybrid": _synthetic_report("hybrid", mrr=0.5)})


def test_check_floors_tolerates_absent_modes() -> None:
    """A partial run evaluates what it has instead of crashing or guessing."""
    from tests.rag.eval.run import check_floors

    # Only one side of the comparison present: nothing to compare, no violation.
    assert check_floors({"hybrid": _synthetic_report("hybrid", mrr=0.9)}) == []
    assert check_floors({"hybrid_link": _synthetic_report("hybrid_link", mrr=0.1)}) == []
    assert check_floors({}) == []


def test_gate_takes_no_baseline_argument_and_freezes_no_link_mrr() -> None:
    """Pins the two rejected designs, so neither can creep back in.

    A ``baseline_mrr=`` parameter would let a caller pass a baseline that does
    not match the run, and a literal MRR floor for the link path would be a
    threshold tuned to one measurement. The baseline is always
    ``reports["hybrid"]`` from the same run.
    """
    import inspect

    from tests.rag.eval import run as run_module

    assert list(inspect.signature(run_module.check_floors).parameters) == ["reports"]
    source = inspect.getsource(run_module.check_floors)
    assert "baseline_mrr" not in source.split('"""')[2]  # prose may name it, code may not
    assert "HYBRID_MRR_FLOOR" not in inspect.getsource(run_module)
    assert "mrr" not in run_module.FLOORS["hybrid_link"]


def test_main_prints_the_gate_even_without_the_check_flag(
    tmp_path: Path, capsys
) -> None:
    """``--check`` only decides the exit code; the rules run regardless.

    This is the regression the gate fix exists for: while the call site was
    ``check_floors(reports) if args.check else []``, a run without the flag
    silently evaluated nothing — and ``--no-tests`` is exactly such a run, which
    is how the numbers going into the docs were produced.
    """
    from tests.rag.eval.run import main

    code = main(["--vault", str(tmp_path / "vault"), "--mode", "hybrid"])
    output = capsys.readouterr().out
    assert code == 0
    assert "floors: OK" in output
    assert "hybrid_link-vs-hybrid MRR" in output
    assert "--check controls only the exit code" in output
