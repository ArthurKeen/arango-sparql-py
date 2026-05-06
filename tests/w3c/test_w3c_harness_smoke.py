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
