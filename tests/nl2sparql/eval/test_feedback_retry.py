"""Offline tests for ``feedback_retry.py`` — the execution-feedback postcondition,
the replayed first candidate, the equal budget, selection, and the scoring
model. No network, no key."""

from __future__ import annotations

import itertools

import pytest
from arango_query_core.nl.postconditions import PostconditionContext

from tests.nl2sparql.eval.feedback_retry import (
    CANDIDATE_BUDGET,
    EMPTY_HINT,
    EMPTY_REASON,
    ExecutionFeedback,
    ReplayFirstClient,
    apply_budget,
    chain_budget,
    compare,
    disjoint_runs,
    per_question,
    resample_continuation,
    select,
)
from tests.nl2sparql.eval.sample_vote import SIG_EMPTY, SIG_ERROR

ONTOLOGY = """
@prefix : <http://ex.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

:Person a owl:Class ;
    phys:collectionName "Person" .

:name a owl:DatatypeProperty ;
    rdfs:domain :Person ;
    rdfs:range <http://www.w3.org/2001/XMLSchema#string> .
""".strip()

DATA = """
@prefix : <http://ex.org/> .
:ada a :Person ; :name "Ada" .
"""

ANSWERED = "PREFIX : <http://ex.org/>\nSELECT ?n WHERE { ?s a :Person ; :name ?n }"
EMPTY = 'PREFIX : <http://ex.org/>\nSELECT ?n WHERE { ?s a :Person ; :name ?n FILTER(?n = "Nobody") }'


@pytest.fixture(scope="module")
def store():
    pytest.importorskip("pyoxigraph")
    from tests.helpers.oxi import load_store_from_string

    return load_store_from_string(DATA)


@pytest.fixture(scope="module")
def labels(store):
    from tests.nl2sparql.eval.runner import _build_label_map

    return _build_label_map(store)


def _ctx(attempt: int = 0) -> PostconditionContext:
    return PostconditionContext(schema_summary="", question="q", attempt=attempt)


# --- the postcondition ------------------------------------------------------


def test_feedback_accepts_a_candidate_that_returns_rows(store, labels) -> None:
    check = ExecutionFeedback(store=store, label_map=labels, cap=5)
    assert check.check(ANSWERED, context=_ctx()) is None
    assert check.executed[0]["signature"] not in (SIG_EMPTY, SIG_ERROR)


def test_feedback_rejects_an_empty_result_with_the_domain_free_hint(store, labels) -> None:
    check = ExecutionFeedback(store=store, label_map=labels, cap=5)
    violation = check.check(EMPTY, context=_ctx())
    assert violation is not None
    assert violation.code == "execution_empty"
    assert violation.reason == EMPTY_REASON and violation.suggested_hint == EMPTY_HINT
    assert check.executed == [{"sparql": EMPTY, "signature": SIG_EMPTY, "error": None, "attempt": 0}]


def test_feedback_rejects_an_execution_error_and_quotes_the_engine(store, labels) -> None:
    check = ExecutionFeedback(store=store, label_map=labels, cap=5)
    violation = check.check("SELECT ?x WHERE { ?x ?p }", context=_ctx(2))
    assert violation is not None and violation.code == "execution_error"
    assert check.executed[0]["error"] and check.executed[0]["error"] in violation.reason
    assert check.executed[0]["attempt"] == 2


def test_feedback_stops_objecting_at_its_cap(store, labels) -> None:
    check = ExecutionFeedback(store=store, label_map=labels, cap=2)
    assert check.check(EMPTY, context=_ctx(0)) is not None
    assert check.check(EMPTY, context=_ctx(1)) is None
    assert len(check.executed) == 2


def test_feedback_adds_nothing_to_the_system_prompt(store, labels) -> None:
    assert ExecutionFeedback(store=store, label_map=labels, cap=5).prompt_section() == ""


# --- replay, budget, selection ----------------------------------------------


def test_replay_returns_the_recorded_query_once_at_no_cost_then_delegates() -> None:
    from arango_sparql.nl2sparql import LLMResponse, ScriptedLLMClient

    inner = ScriptedLLMClient(
        [LLMResponse(content="later", prompt_tokens=7, completion_tokens=3, total_tokens=10)]
    )
    client = ReplayFirstClient(inner, "SELECT * WHERE { ?s ?p ?o }")
    first = client.generate([{"role": "user", "content": "q"}])
    assert "SELECT * WHERE" in first.content and first.prompt_tokens == 0 and first.completion_tokens == 0
    assert inner.calls == []
    assert client.generate([{"role": "user", "content": "q"}]).content == "later"
    assert len(inner.calls) == 1
    assert (client.provider, client.model) == (inner.provider, inner.model)


def test_chain_budget_matches_four_more_candidates_of_three_calls() -> None:
    assert chain_budget(replayed=True) == (12, CANDIDATE_BUDGET)
    # Nothing to replay: attempt 0 is a real call, and the failed base still
    # used one of the five candidates.
    assert chain_budget(replayed=False) == (11, CANDIDATE_BUDGET - 1)


def test_select_prefers_an_answer_then_the_first_that_ran_then_none() -> None:
    def ex(*sigs):
        return [{"signature": s} for s in sigs]

    assert select(ex(SIG_EMPTY, SIG_ERROR, "a", "b")) == 2
    assert select(ex(SIG_ERROR, SIG_EMPTY, SIG_EMPTY)) == 1
    assert select(ex(SIG_ERROR, SIG_ERROR)) is None
    assert select([]) is None


# --- the full chain through the real pipeline (scripted model) ----------------


def test_a_chain_replays_the_empty_base_feeds_back_and_keeps_the_answer(store, labels) -> None:
    from arango_sparql.nl2sparql import LLMResponse, NlPipeline, ScriptedLLMClient
    from arango_sparql.translate.resolver import SchemaResolver

    inner = ScriptedLLMClient(
        [
            LLMResponse(
                content=f"```sparql\n{ANSWERED}\n```", prompt_tokens=10, completion_tokens=5, total_tokens=15
            )
        ],
        latency_ms=0,
    )
    max_repairs, cap = chain_budget(replayed=True)
    check = ExecutionFeedback(store=store, label_map=labels, cap=cap)
    outcome = NlPipeline(
        client=ReplayFirstClient(inner, EMPTY),
        resolver=SchemaResolver.from_turtle(ONTOLOGY),
        ontology_ttl=ONTOLOGY,
        max_repairs=max_repairs,
        postconditions=[check],
    ).run("Who are the people?")
    assert [c["signature"] == SIG_EMPTY for c in check.executed] == [True, False]
    assert select(check.executed) == 1
    assert outcome.aql and outcome.sparql == ANSWERED
    assert outcome.llm_calls == 2  # the replay plus one real call
    assert len(inner.calls) == 1
    retry_prompt = inner.calls[0][-1]["content"]
    assert EMPTY_REASON in retry_prompt and EMPTY in retry_prompt


def test_a_chain_that_stays_empty_stops_at_the_cap(store, labels) -> None:
    from arango_sparql.nl2sparql import LLMResponse, NlPipeline, ScriptedLLMClient
    from arango_sparql.translate.resolver import SchemaResolver

    inner = ScriptedLLMClient(
        [
            LLMResponse(
                content=f"```sparql\n{EMPTY}\n```", prompt_tokens=1, completion_tokens=1, total_tokens=2
            )
        ],
        latency_ms=0,
    )
    max_repairs, cap = chain_budget(replayed=True)
    check = ExecutionFeedback(store=store, label_map=labels, cap=cap)
    NlPipeline(
        client=ReplayFirstClient(inner, EMPTY),
        resolver=SchemaResolver.from_turtle(ONTOLOGY),
        ontology_ttl=ONTOLOGY,
        max_repairs=max_repairs,
        postconditions=[check],
    ).run("Who is called Nobody?")
    assert len(check.executed) == CANDIDATE_BUDGET
    assert len(inner.calls) == CANDIDATE_BUDGET - 1
    assert select(check.executed) == 0  # all empty: the first that ran


def test_a_chain_gets_its_whole_budget_past_the_product_ceiling(store, labels) -> None:
    """Regression (W1b smoke run): RepairLoop clamps max_repairs at 5, so chains
    stopped after five real calls instead of the pre-registered twelve."""
    from arango_sparql.nl2sparql import LLMResponse, NlPipeline, ScriptedLLMClient
    from arango_sparql.translate.resolver import SchemaResolver

    bad = LLMResponse(
        content="```sparql\nSELECT WHERE { broken\n```", prompt_tokens=1, completion_tokens=1, total_tokens=2
    )
    good = LLMResponse(
        content=f"```sparql\n{ANSWERED}\n```", prompt_tokens=1, completion_tokens=1, total_tokens=2
    )
    inner = ScriptedLLMClient([bad] * 8 + [good], latency_ms=0)
    max_repairs, cap = chain_budget(replayed=True)
    check = ExecutionFeedback(store=store, label_map=labels, cap=cap)
    pipeline = NlPipeline(
        client=ReplayFirstClient(inner, EMPTY),
        resolver=SchemaResolver.from_turtle(ONTOLOGY),
        ontology_ttl=ONTOLOGY,
        max_repairs=max_repairs,
        postconditions=[check],
    )
    assert pipeline.repair_loop.max_repairs == 5  # the product ceiling
    outcome = apply_budget(pipeline, max_repairs).run("Who are the people?")
    assert pipeline.repair_loop.max_repairs == 12
    assert outcome.aql and outcome.sparql == ANSWERED
    assert len(inner.calls) == 9


# --- scoring ----------------------------------------------------------------


def test_resample_continuation_matches_enumeration() -> None:
    # Outcomes per draw: P pass-with-rows, F fail-with-rows, E empty-or-failed.
    p = {"P": 0.3, "F": 0.2, "E": 0.5}
    exact = 0.0
    for seq in itertools.product("PFE", repeat=4):
        prob = 1.0
        for x in seq:
            prob *= p[x]
        first = next((x for x in seq if x != "E"), None)
        exact += prob * (first == "P")
    assert resample_continuation(0.3, 0.5, 4) == pytest.approx(exact)
    assert resample_continuation(0.0, 1.0, 4) == 0.0
    with pytest.raises(ValueError):
        resample_continuation(0.8, 0.5, 4)


def _s(case, j, sig, passed, calls=1, cost=0.01):
    return {
        "case": case,
        "sample": j,
        "signature": sig,
        "passed": passed,
        "llm_calls": calls,
        "cost_usd": cost,
    }


def _c(case, j, passed, calls=2, cost=0.02):
    return {
        "case": case,
        "sample": j,
        "passed": passed,
        "real_calls": calls,
        "cost_usd": cost,
        "engine_ok": passed,
    }


def test_per_question_needs_a_chain_for_every_failed_sample() -> None:
    samples = [_s("q", 0, SIG_EMPTY, False), _s("q", 1, "a", True)]
    with pytest.raises(ValueError, match="no chain"):
        per_question(samples, [])
    row = per_question(samples, [_c("q", 0, True)])["q"]
    assert row["p_e"] == 0.5 and row["p_np"] == 0.5
    assert row["feedback"] == pytest.approx(1.0)
    assert row["resample"] == pytest.approx(0.5 + 0.5 * resample_continuation(0.5, 0.5, 4))


def test_per_question_refuses_a_passing_failed_sample() -> None:
    with pytest.raises(ValueError, match="passed"):
        per_question([_s("q", 0, SIG_EMPTY, True)], [_c("q", 0, True)])


def test_compare_pairs_by_question() -> None:
    rows = {
        "q1": {"resample": 0.5, "feedback": 0.9},
        "q2": {"resample": 1.0, "feedback": 1.0},
        "q3": {"resample": 0.2, "feedback": 0.1},
    }
    view = compare(rows)
    assert view["difference"] == pytest.approx(0.3)
    assert (view["questions_up"], view["questions_down"], view["questions_level"]) == (1, 1, 1)
    assert view["paired_t"] is not None


def test_disjoint_runs_share_the_base_and_differ_only_after_it() -> None:
    # q: run 0 base empty -> resample takes sample 1 (passes), chain for 0 fails.
    #    run 1 base answers and passes -> both arms pass without a chain.
    sigs = [SIG_EMPTY, "a", "b", "b", "b", "a", SIG_EMPTY, "b", "b", "b"]
    samples = [_s("q", j, sig, sig == "a") for j, sig in enumerate(sigs)]
    runs = disjoint_runs(samples, [_c("q", 0, False)], k=5)
    assert runs == [{"resample": 1, "feedback": 0}, {"resample": 1, "feedback": 1}]
