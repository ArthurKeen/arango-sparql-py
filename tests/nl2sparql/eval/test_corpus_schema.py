"""Regression tests for the eval-harness contract seams (Plan 06.2-01).

Two capabilities the harder corpus depends on, proven here before a single
new case is authored:

1. A **load-time Pydantic gate** (``CorpusCase`` / ``BaselineConfig``): a
   malformed/unparseable positive gold must FAIL the corpus load loudly,
   never be silently skipped (AI-SPEC Critical Failure Mode 2). A negative
   case marked ``expect_refusal: true`` carries a human-readable rationale
   in ``expected`` — the gold-must-parse validator must NOT try to parse it.
2. An **inverted refusal judge** branch in ``_judge``: an ``expect_refusal``
   case PASSES iff the pipeline produced no transpilable AQL
   (``outcome.aql == ""``) and FAILS iff it emitted a confident query over
   invented terms (the silently-wrong-but-parseable trap, AI-SPEC §5).

Key-free / no-network: mirrors ``test_eval.py``'s ``pytest.mark.eval`` +
``RUN_EVAL`` skip idiom so this stays off the default fast path and only ever
touches the runner module (never a live provider). All fixtures are tiny
in-memory dicts / stand-in outcome objects — no ``corpus.yml`` read, no
pipeline invocation.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tests.nl2sparql.eval.runner import BaselineConfig, CorpusCase, _judge

# Same "off" semantics as test_eval.py: treat "", "0", "false", "no" as off
# (a non-empty string is truthy, so RUN_EVAL=0 must be explicitly excluded).
_RUN_EVAL = os.getenv("RUN_EVAL", "").strip().lower() not in ("", "0", "false", "no")

pytestmark = [
    pytest.mark.eval,
    pytest.mark.skipif(not _RUN_EVAL, reason="set RUN_EVAL=1 to run the NL eval gate"),
]


# Trivially-valid gold: parses cleanly under rdflib.plugins.sparql.
_VALID_GOLD = "SELECT ?s WHERE { ?s a <http://ex.org/Person> }"
# Obviously-unparseable: the gold-must-parse validator must reject this.
_MALFORMED_GOLD = "NOT SPARQL {{{"


# ---------------------------------------------------------------------------
# CorpusCase load-time gate
# ---------------------------------------------------------------------------


def test_corpus_case_positive_malformed_gold_raises() -> None:
    """A POSITIVE case (no expect_refusal) with an unparseable gold must
    raise a ValidationError — the load fails loudly rather than silently
    dropping the case (AI-SPEC Critical Failure Mode 2)."""
    with pytest.raises(ValidationError):
        CorpusCase(name="bad", nl="whatever", expected=_MALFORMED_GOLD)


def test_corpus_case_positive_valid_gold_constructs_and_round_trips() -> None:
    """A positive case with valid gold SPARQL constructs and round-trips."""
    case = CorpusCase(name="ok", nl="list people", expected=_VALID_GOLD)
    assert case.name == "ok"
    assert case.nl == "list people"
    assert case.expected == _VALID_GOLD
    assert case.expect_refusal is False


def test_corpus_case_refusal_rationale_not_parsed_as_gold() -> None:
    """An ``expect_refusal: true`` case carries a human rationale in
    ``expected`` — the validator MUST NOT parse it as SPARQL, so a
    non-SPARQL rationale string is ACCEPTED (AI-SPEC §5 scoring negatives)."""
    case = CorpusCase(
        name="negative",
        nl="What is the airspeed velocity of an unladen swallow?",
        expected="Out of schema: there is no swallow class; the model should refuse.",
        expect_refusal=True,
    )
    assert case.expect_refusal is True
    assert case.expected.startswith("Out of schema")


# ---------------------------------------------------------------------------
# Inverted expect_refusal judge branch
# ---------------------------------------------------------------------------


def test_judge_refusal_passes_when_no_aql() -> None:
    """expect_refusal case + empty AQL → PASS (honest refusal)."""
    case = {"name": "neg", "expected": "rationale", "expect_refusal": True}
    outcome = SimpleNamespace(aql="", sparql="")
    assert _judge("canonical", case, outcome)[0] is True


def test_judge_refusal_fails_when_aql_emitted() -> None:
    """expect_refusal case + non-empty AQL → FAIL (confident hallucination)."""
    case = {"name": "neg", "expected": "rationale", "expect_refusal": True}
    outcome = SimpleNamespace(
        aql="FOR d IN Swallow RETURN d",
        sparql="SELECT ?s WHERE { ?s a <http://ex.org/Swallow> }",
    )
    assert _judge("canonical", case, outcome)[0] is False


def test_judge_positive_empty_aql_still_fails_via_canonical() -> None:
    """A NON-refusal case with empty AQL still FAILS via the canonical judge —
    the inverted branch must only fire for expect_refusal cases."""
    case = {"name": "pos", "expected": _VALID_GOLD}
    outcome = SimpleNamespace(aql="", sparql="")
    assert _judge("canonical", case, outcome)[0] is False


# ---------------------------------------------------------------------------
# BaselineConfig — scripted shape vs live shape, bounds
# ---------------------------------------------------------------------------


def test_baseline_config_scripted_shape_valid() -> None:
    """The existing scripted shape (no live fields) is accepted unchanged."""
    cfg = BaselineConfig(
        pass_rate=0.8333333333333334,
        passed=5,
        total=6,
        cases={"a": True, "b": False},
    )
    assert cfg.model is None
    assert cfg.temperature is None
    assert cfg.corpus_sha is None


def test_baseline_config_live_shape_valid() -> None:
    """The live-baseline shape carries model/temperature/corpus_sha."""
    cfg = BaselineConfig(
        pass_rate=0.75,
        passed=3,
        total=4,
        cases={"a": True},
        model="gpt-4o-mini",
        temperature=0.0,
        corpus_sha="deadbeef",
    )
    assert cfg.model == "gpt-4o-mini"
    assert cfg.temperature == 0.0
    assert cfg.corpus_sha == "deadbeef"


def test_baseline_config_rejects_pass_rate_out_of_range() -> None:
    """pass_rate outside [0,1] must be rejected."""
    with pytest.raises(ValidationError):
        BaselineConfig(pass_rate=1.5, passed=6, total=6, cases={"a": True})
    with pytest.raises(ValidationError):
        BaselineConfig(pass_rate=-0.1, passed=0, total=6, cases={"a": False})
