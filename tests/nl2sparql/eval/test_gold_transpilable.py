"""Authoring guard: every non-refusal corpus gold must be JUDGEABLE.

A positive corpus gold that does not both (a) parse via rdflib AND (b)
transpile to non-empty AQL can never pass the canonical-algebra judge — an
un-transpilable gold is permanently un-judgeable and silently always fails
(AI-SPEC Pitfall 4 / Guardrail "Gold transpilability gate"). This guard makes
that gate PERMANENT: a future gold that stops transpiling (a typo, an
unsupported operator such as the inverse-arm negated path ``!(^:p)``, or a
schema term the resolver can't map) fails CI at authoring time rather than
masquerading as a model-quality regression.

Negative cases (``expect_refusal: true``) carry a human-readable rationale in
``expected`` — NOT gold SPARQL — so the honest-refusal convention (AI-SPEC §5)
scores them by the inverted empty-AQL signal. They are excluded here.

Key-free / no-network: mirrors ``test_eval.py``'s ``pytest.mark.eval`` +
``RUN_EVAL`` skip idiom so this stays off the default fast path. It only ever
touches ``corpus.yml`` (and any ``vendored/*/corpus.yml``), the resolver,
``_canonical``, and the deterministic ``translate`` API — never a live
provider.

Auto-discovers every vendored corpus (07.1-01 Task 3): parametrized over
``[CORPUS_PATH] + sorted(EVAL_DIR.glob("vendored/*/corpus.yml"))`` so a
future set (QALD-9-plus, CK25, ...) is covered by this guard the instant it
lands a ``corpus.yml`` under ``vendored/`` — no edits to this file required.
"""

from __future__ import annotations

import os

import pytest

from arango_sparql.api import translate
from arango_sparql.translate.resolver import SchemaResolver
from tests.nl2sparql.eval.runner import CORPUS_PATH, EVAL_DIR, _canonical, _load_corpus

# Same "off" semantics as test_eval.py: treat "", "0", "false", "no" as off
# (a non-empty string is truthy, so RUN_EVAL=0 must be explicitly excluded).
_RUN_EVAL = os.getenv("RUN_EVAL", "").strip().lower() not in ("", "0", "false", "no")

pytestmark = [
    pytest.mark.eval,
    pytest.mark.skipif(not _RUN_EVAL, reason="set RUN_EVAL=1 to run the NL eval gate"),
]


def _corpus_paths() -> list:
    """The root ``corpus.yml`` plus every vendored set's ``corpus.yml``.

    Auto-discovers `vendored/*/corpus.yml` (07.1-01 Task 3) so Waves 2/3 of
    this phase (QALD-9-plus, CK25) are covered by this guard the moment they
    land a vendored corpus file — with ZERO further edits to this module.
    Preserves today's behavior when no `vendored/` sets exist yet (only
    `corpus.yml` is discovered).
    """
    return [CORPUS_PATH] + sorted(EVAL_DIR.glob("vendored/*/corpus.yml"))


def _positive_cases() -> list[dict]:
    """Every non-``expect_refusal`` case across every discovered corpus file —
    the golds the canonical judge scores directly and therefore must be
    transpilable.

    Each returned dict carries two synthetic keys alongside the normal
    ``CorpusCase`` fields: ``_ontology_ttl`` (the case's own ``ontology:``
    override, else its corpus file's shared ``ontology:``) and
    ``_qualified_name`` (the case ``name`` prefixed with its set folder, e.g.
    ``qald9plus/qald9plus-99``, ``ck25/ck25-1``; bare for the root corpus) so
    `-k qald9plus` / `-k ck25` select a set once vendored.
    """
    positive: list[dict] = []
    for path in _corpus_paths():
        corpus = _load_corpus(path)
        shared_ontology = corpus.get("ontology", "")
        prefix = "" if path == CORPUS_PATH else f"{path.parent.name}/"
        for case in corpus.get("cases", []):
            if case.get("expect_refusal"):
                continue
            enriched = dict(case)
            enriched["_ontology_ttl"] = case.get("ontology", shared_ontology)
            enriched["_qualified_name"] = f"{prefix}{case['name']}"
            positive.append(enriched)
    return positive


def _case_ids() -> list[str]:
    return [c["_qualified_name"] for c in _positive_cases()]


@pytest.mark.parametrize("case", _positive_cases(), ids=_case_ids())
def test_positive_gold_parses_and_transpiles(case: dict) -> None:
    """Each positive gold parses via rdflib AND transpiles to non-empty AQL."""
    resolver = SchemaResolver.from_turtle(case["_ontology_ttl"])

    gold = case["expected"]

    # (a) Parses via rdflib — _canonical returns None on a parse failure.
    assert _canonical(gold) is not None, (
        f"gold for case {case['_qualified_name']!r} does not parse via rdflib"
    )

    # (b) Transpiles to non-empty AQL — an empty .aql is the authoritative
    # "un-judgeable" signal the canonical judge keys on (runner._judge_canonical).
    result = translate(gold, resolver=resolver, params=case.get("params"))
    assert result.aql, (
        f"gold for case {case['_qualified_name']!r} transpiled to EMPTY AQL — it can never "
        f"pass the canonical judge (AI-SPEC Pitfall 4)"
    )
