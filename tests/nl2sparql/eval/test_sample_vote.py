"""Offline tests for ``sample_vote.py`` — the vote, the disjoint-run scoring, and
the answer signature. No network, no key."""

from __future__ import annotations

import pytest

from tests.nl2sparql.eval.sample_vote import (
    SIG_EMPTY,
    SIG_ERROR,
    Candidate,
    answer_signature,
    candidates_drawn,
    score,
    vote,
)


def _c(*sigs: str) -> list[Candidate]:
    return [Candidate(s) for s in sigs]


def test_vote_first_always_picks_index_zero() -> None:
    assert vote(_c("a", "b", "b"), "first") == 0


def test_vote_majority_picks_the_commonest_signature() -> None:
    assert vote(_c("a", "b", "b"), "majority") == 1


def test_vote_majority_breaks_ties_by_first_seen() -> None:
    assert vote(_c("b", "a", "a", "b"), "majority") == 0


def test_vote_majority_ignores_errors_even_when_they_are_commonest() -> None:
    assert vote(_c(SIG_ERROR, SIG_ERROR, "a"), "majority") == 2


def test_vote_all_errors_falls_back_to_index_zero() -> None:
    assert vote(_c(SIG_ERROR, SIG_ERROR), "majority-nonempty") == 0


def test_vote_majority_counts_empty_results_as_an_answer() -> None:
    assert vote(_c(SIG_EMPTY, SIG_EMPTY, "a"), "majority") == 0


def test_vote_majority_nonempty_discards_empty_results() -> None:
    assert vote(_c(SIG_EMPTY, SIG_EMPTY, "a"), "majority-nonempty") == 2


def test_vote_majority_nonempty_keeps_empty_when_nothing_else_ran() -> None:
    assert vote(_c(SIG_ERROR, SIG_EMPTY, SIG_EMPTY), "majority-nonempty") == 1


def test_vote_first_nonempty_takes_the_first_candidate_with_an_answer() -> None:
    assert vote(_c(SIG_ERROR, SIG_EMPTY, "b", "a", "a"), "first-nonempty") == 2


def test_vote_first_nonempty_falls_back_to_the_first_that_ran() -> None:
    assert vote(_c(SIG_ERROR, SIG_EMPTY, SIG_EMPTY), "first-nonempty") == 1


def test_candidates_drawn_counts_what_a_sequential_system_generates() -> None:
    cands = _c(SIG_EMPTY, SIG_ERROR, "a", "b", "a")
    assert candidates_drawn(cands, "first") == 1
    assert candidates_drawn(cands, "first-nonempty") == 3
    assert candidates_drawn(cands, "majority-nonempty") == 5
    assert candidates_drawn(_c(SIG_EMPTY, SIG_EMPTY), "first-nonempty") == 2


def test_vote_rejects_unknown_rule_and_empty_input() -> None:
    with pytest.raises(ValueError, match="unknown vote rule"):
        vote(_c("a"), "best")
    with pytest.raises(ValueError, match="at least one"):
        vote([], "first")


def _s(case: str, sample: int, sig: str, passed: bool) -> dict:
    return {"case": case, "sample": sample, "signature": sig, "passed": passed}


def test_score_k1_scores_each_sample_index_as_its_own_run() -> None:
    samples = [
        _s("q1", 0, "a", True),
        _s("q1", 1, "b", False),
        _s("q2", 0, "c", True),
        _s("q2", 1, "c", True),
    ]
    assert score(samples, 1, "first") == [2, 1]


def test_score_vote_runs_use_disjoint_samples() -> None:
    # q1: run 0 = samples 0-2 (majority "a", passes); run 1 = samples 3-5 (majority "b", fails).
    sigs = ["a", "b", "a", "b", "b", "a"]
    samples = [_s("q1", j, sig, sig == "a") for j, sig in enumerate(sigs)]
    assert score(samples, 3, "majority") == [1, 0]


def test_score_drops_the_trailing_partial_group_and_honours_exclude() -> None:
    samples = [_s("q1", j, "a", True) for j in range(5)] + [_s("q2", j, "a", False) for j in range(5)]
    assert score(samples, 2, "majority") == [1, 1]
    assert score(samples, 2, "majority", exclude=frozenset({"q1"})) == [0, 0]


def test_score_uses_the_shallowest_case_and_ignores_samples_past_a_gap() -> None:
    samples = [_s("q1", j, "a", True) for j in range(4)] + [_s("q2", 0, "a", True), _s("q2", 3, "a", True)]
    assert score(samples, 1, "first") == [2]


def test_score_raises_when_no_case_has_k_samples() -> None:
    with pytest.raises(ValueError, match="needs 3 samples"):
        score([_s("q1", 0, "a", True)], 3, "majority")
    with pytest.raises(ValueError, match="no samples"):
        score([], 1, "first")


_TTL = """
@prefix ex: <http://example.org/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
ex:a rdfs:label "Alpha" ; ex:p ex:b .
ex:b rdfs:label "Beta" .
"""


@pytest.fixture(scope="module")
def store():
    pytest.importorskip("pyoxigraph")
    from tests.helpers.oxi import load_store_from_string

    return load_store_from_string(_TTL)


def test_signature_is_insensitive_to_variable_names_iris_versus_labels_and_duplicates(store) -> None:
    from tests.nl2sparql.eval.runner import _build_label_map

    labels = _build_label_map(store)
    by_iri = answer_signature(store, "SELECT ?x WHERE { ?x <http://example.org/p> ?y }", "AQL", labels)
    by_label = answer_signature(
        store,
        "SELECT ?n WHERE { ?s <http://example.org/p> ?o . ?s <http://www.w3.org/2000/01/rdf-schema#label> ?n }",
        "AQL",
        labels,
    )
    union = "SELECT ?x WHERE { { ?x <http://example.org/p> ?y } UNION { ?x <http://example.org/p> ?y } }"
    assert by_iri == by_label == answer_signature(store, union, "AQL", labels)
    assert "Alpha" in by_iri


def test_signature_marks_empty_ask_untranspiled_and_rejected(store) -> None:
    from tests.nl2sparql.eval.runner import _build_label_map

    labels = _build_label_map(store)
    assert (
        answer_signature(store, "SELECT ?x WHERE { ?x <http://example.org/q> ?y }", "AQL", labels)
        == SIG_EMPTY
    )
    assert answer_signature(store, "ASK { ?x <http://example.org/p> ?y }", "AQL", labels) == "ASK:True"
    assert answer_signature(store, "SELECT ?x WHERE { ?x ?p ?o }", "", labels) == SIG_ERROR
    assert answer_signature(store, "SELECT ?x WHERE { ?x ?p }", "AQL", labels) == SIG_ERROR
