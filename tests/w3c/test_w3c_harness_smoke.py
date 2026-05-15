"""Smoke tests for the W3C harness.

Two invariants:

1. The runner module imports cleanly even when the corpus is absent —
   so a fresh checkout doesn't fail collection.
2. When the corpus *is* on disk, ``collect_cases()`` actually returns
   QueryEvaluationTest / PositiveSyntaxTest11 / NegativeSyntaxTest11
   entries, and ``load_skip_iris`` parses ``SKIP_REASONS.md`` into a
   set without raising.

Heavy assertions about coverage live in ``analyze_coverage.py``; this
file is just the "is the wiring intact" guard.
"""

from __future__ import annotations

import pytest


@pytest.mark.w3c
def test_w3c_runner_imports() -> None:
    from tests.w3c.runner import (
        collect_cases,
        iter_manifest_cases,
        load_skip_iris,
        w3c_corpus_root,
    )

    assert callable(iter_manifest_cases)
    assert callable(w3c_corpus_root)
    assert callable(collect_cases)
    assert callable(load_skip_iris)


@pytest.mark.w3c
def test_w3c_corpus_optional() -> None:
    """When the corpus is on disk we expect a non-empty enumeration
    across the three core test categories. When it isn't, the harness
    must skip cleanly so a fresh clone doesn't fail collection."""
    from tests.w3c.runner import (
        NEG_SYNTAX_11,
        POS_SYNTAX_11,
        QUERY_EVAL,
        collect_cases,
        w3c_corpus_root,
    )

    if w3c_corpus_root() is None:
        pytest.skip("W3C SPARQL tests not present; run scripts/fetch_w3c.sh")

    cases = collect_cases()
    assert cases, "corpus is present but no test cases enumerated"
    types = {c.test_type for c in cases}
    # The DAWG corpus must surface all three categories the harness
    # relies on. If any are missing the manifest walker is broken.
    assert QUERY_EVAL in types
    assert POS_SYNTAX_11 in types
    assert NEG_SYNTAX_11 in types


@pytest.mark.w3c
def test_skip_reasons_parses() -> None:
    """`SKIP_REASONS.md` must be parsable even when it has no entries.

    The placeholder row (`_(none yet)_`) is intentionally not a valid
    IRI so the loader must skip it without raising."""
    from tests.w3c.runner import load_skip_iris

    skips = load_skip_iris()
    assert isinstance(skips, set)
    for iri in skips:
        assert iri.startswith(("http://", "https://", "file://")), (
            f"non-IRI escaped the skip-list parser: {iri!r}"
        )


@pytest.mark.w3c
def test_bucket_classifier_categorises_every_known_prefix() -> None:
    """Every XFAIL-reason prefix the analyzer emits must map to a
    non-``other`` bucket.

    The bucket rollup feeds the PRD §13.5 tracker; an XFAIL that
    silently lands in ``other`` would distort that tracker into
    over-counting harness artefacts as roadmap items (or vice versa).
    This test pins the classifier against every reason prefix
    ``_classify_query_eval`` and ``_classify_negative_syntax`` can
    produce."""
    from tests.w3c.analyze_coverage import BUCKET_OTHER, _bucket

    cases = {
        # UnsupportedSparql-family — all algebra-bucket.
        "UnsupportedSparql: SPARQL Algebra node 'ToMultiSet' is not implemented yet": "algebra",
        "UnsupportedSparql: variable predicates (?p) require multi-collection UNION": "algebra",
        "UnsupportedSparql: unsupported triple shape: subject=URIRef, predicate=MulPath, object=Variable": "algebra",
        "UnsupportedSparql: CONSTRUCT without a template is not supported": "algebra",
        # AqlEmit-family — algebra (emit-stage gap).
        "AqlEmit: query has no FOR clause; every BGP/SELECT translation needs at least one": "algebra",
        # SparqlParse-family — algebra (we asked rdflib to parse and it
        # raised; that's a real gap on our side once we wrap the input).
        "SparqlParse: unexpected end of input": "algebra",
        # SchemaResolution-family — schema (empty-resolver harness artefact).
        "SchemaResolution: class IRI 'http://www.w3.org/2002/07/owl#Restriction' is not declared": "schema",
        "SchemaResolution: class IRI 'http://example.org/x/c' is not declared owl:Class": "schema",
        # rdflib-family — the negative-syntax XFAIL phrase plus the
        # positive-syntax parse-failure phrase.
        "rdflib accepted invalid query": "rdflib",
        "rdflib parse failure: unexpected character": "rdflib",
    }
    for reason, expected_bucket in cases.items():
        actual = _bucket(reason)
        assert actual == expected_bucket, (
            f"reason {reason!r} expected bucket {expected_bucket!r}, got {actual!r}"
        )
        assert actual != BUCKET_OTHER, (
            f"reason {reason!r} fell through to 'other'; add a rule"
        )


@pytest.mark.w3c
def test_bucket_classifier_falls_back_to_other_for_unknown() -> None:
    """Truly unknown reasons fall through to ``other`` so the analyzer
    surfaces them for manual triage rather than silently mis-binning
    them. Keeps the ``other`` row visible in the report when it has
    members."""
    from tests.w3c.analyze_coverage import BUCKET_OTHER, _bucket

    assert _bucket("some brand-new error class we haven't seen") == BUCKET_OTHER
    assert _bucket("") == BUCKET_OTHER
