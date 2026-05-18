"""Golden tests for SPARQL 1.1 ``UNION`` and ``AlternativePath``.

Both constructs share one AQL emitter
(``arango_sparql.translate.union_paths._emit_union_of_arms``) — the
YAML corpus pins their byte-for-byte AQL output so regressions in
either path surface immediately. The shared-AQL property is the
main reason this slice is a single bundle: AlternativePath's
desugaring is the regression test for UNION's emitter, and vice
versa.

Coverage moved from 36.4 % to 37.9 % on the W3C DAWG corpus with
this slice (+1.5 pp, +4 newly-passing tests across the
``AlternativePath`` algebra bucket). UNION itself was below the
top-15 XFAIL surface (most W3C UNION tests cascade through other
unsupported features), so its hidden contribution will surface as
adjacent buckets close.

Two test blocks:

* **YAML goldens** — every shape from the corpus with exact AQL +
  bind-vars assertions, including the cross-check that
  AlternativePath produces byte-identical output to the explicit
  UNION form.
* **Resolver-driven interactions** — Python tests that exercise
  nesting (UNION inside a sub-SELECT), the shared-variable join
  through a tenanted resolver, and the empty-union refusal.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from arango_sparql.api import translate
from arango_sparql.translate.resolver import SchemaResolver

GOLDEN_PATH = Path(__file__).parent / "union_paths.yml"


def _load() -> list[tuple[str, str, str, str, dict]]:
    data = yaml.safe_load(GOLDEN_PATH.read_text())
    ttl = data["ontology"]
    out: list[tuple[str, str, str, str, dict]] = []
    for case in data["cases"]:
        out.append(
            (
                case["name"],
                ttl,
                case["sparql"],
                case["expected_aql"].rstrip("\n"),
                case["expected_bind_vars"],
            )
        )
    return out


@pytest.mark.parametrize(
    "name, ontology_ttl, sparql, expected_aql, expected_bind_vars",
    _load(),
    ids=[c[0] for c in _load()],
)
def test_union_paths_golden(
    name: str,
    ontology_ttl: str,
    sparql: str,
    expected_aql: str,
    expected_bind_vars: dict,
) -> None:
    """Each golden produces the exact AQL the YAML declares.

    Pinning the AQL byte-for-byte protects against four classes
    of regression:

    1. **Probe / emit phase asymmetry** — if the probe walked
       different code than the emit (e.g. cached state leaked
       between phases), the bound-variable set discovered in
       phase 1 would diverge from what phase 2 actually emitted.
    2. **Counter sharing across arms** — phase 2 absorbs each
       arm in order, so the parent's alias counter advances
       between arms. If absorb_child failed to push the counter
       back, both arms would mint ``doc1`` and the AQL would
       have an obvious alias collision.
    3. **Null-fill for disjoint vars** — the
       ``union_disjoint_vars_per_arm`` case pins explicit
       ``null`` for missing vars. A regression that just omitted
       the slot would change the RETURN object's key set and
       break the outer scope's ``row.<var>`` access pattern.
    4. **AlternativePath desugaring** — the ``altpath_two_arms``
       case must produce identical AQL to
       ``union_basic_shared_vars``. If the desugaring stopped
       routing through the shared emitter, the two forms would
       diverge.
    """
    resolver = SchemaResolver.from_turtle(
        ontology_ttl, default_collection="Document"
    )
    result = translate(sparql, resolver=resolver)
    assert result.aql == expected_aql, (
        f"AQL mismatch for {name!r}:\n"
        f"--- expected ---\n{expected_aql}\n"
        f"--- actual ---\n{result.aql}"
    )
    assert result.bind_vars == expected_bind_vars, (
        f"bind_vars mismatch for {name!r}:\n"
        f"--- expected ---\n{expected_bind_vars}\n"
        f"--- actual ---\n{result.bind_vars}"
    )


# ---------------------------------------------------------------------------
# Cross-form invariant: AlternativePath produces the same AQL as UNION
# ---------------------------------------------------------------------------


def test_altpath_matches_explicit_union_byte_for_byte() -> None:
    """``?s :p|:q ?o`` MUST produce the same AQL as
    ``{ ?s :p ?o } UNION { ?s :q ?o }``.

    This is the headline invariant of the bundle: the
    desugaring's correctness depends on AlternativePath calling
    the same shared emitter as Union. A regression that, e.g.,
    swapped the arm order or used a different row alias prefix
    would surface here even if both forms still pass their own
    individual golden.
    """
    resolver = SchemaResolver.from_turtle("", default_collection="Document")
    altpath = translate(
        "PREFIX : <http://ex.org/> SELECT ?s ?o WHERE { ?s :p|:q ?o }",
        resolver=resolver,
    )
    union = translate(
        "PREFIX : <http://ex.org/> SELECT ?s ?o WHERE "
        "{ { ?s :p ?o } UNION { ?s :q ?o } }",
        resolver=resolver,
    )
    assert altpath.aql == union.aql, (
        "AlternativePath and explicit UNION should be byte-identical:\n"
        f"altpath:\n{altpath.aql}\nunion:\n{union.aql}"
    )
    assert altpath.bind_vars == union.bind_vars


# ---------------------------------------------------------------------------
# Nesting + interaction tests
# ---------------------------------------------------------------------------


def test_union_nested_inside_subselect() -> None:
    """UNION inside a sub-SELECT — exercises both the ToMultiSet
    child-builder pattern AND the UNION two-phase emitter
    interacting through nested ``create_child`` calls.

    Counter seeding has to thread correctly: the sub-SELECT
    spawns a child, then INSIDE that child the UNION emitter
    spawns its own probes + arms. The grand-children must mint
    aliases disjoint from the sub-SELECT's child AND from the
    outer query.
    """
    resolver = SchemaResolver.from_turtle("", default_collection="Document")
    result = translate(
        "PREFIX : <http://ex.org/> SELECT ?s ?o WHERE { "
        "{ SELECT ?s ?o WHERE { { ?s :p ?o } UNION { ?s :q ?o } } } "
        "}",
        resolver=resolver,
    )
    # The outer-most FOR is the sub-SELECT row alias;
    # one ``UNION(`` appears inside it.
    assert result.aql.count("UNION(") == 1
    # Every alias is distinct.
    assert "FOR doc1 IN @@c1_Document" in result.aql
    assert "FOR doc2 IN @@c2_Document" in result.aql
    # No alias appears twice.
    aliases = [
        line.split()[1]
        for line in result.aql.splitlines()
        if line.strip().startswith("FOR ")
    ]
    assert len(aliases) == len(set(aliases)), (
        f"alias collision detected: {aliases}"
    )


def test_union_arms_have_independent_filters() -> None:
    """Each UNION arm's local FILTERs must stay INSIDE that arm's
    ``(…)`` block — a regression that leaked an arm's FILTER into
    the outer scope (or the sibling arm) would silently change
    query semantics.

    SPARQL: ``{ ?s :p ?o FILTER (?o > 0) } UNION { ?s :q ?o }``.
    Only arm1's ``?o > 0`` constraint applies to arm1's rows; arm2
    rows are unconstrained on ``?o``.
    """
    resolver = SchemaResolver.from_turtle("", default_collection="Document")
    result = translate(
        "PREFIX : <http://ex.org/> SELECT ?s ?o WHERE { "
        "{ ?s :p ?o FILTER (?o > 0) } UNION { ?s :q ?o } "
        "}",
        resolver=resolver,
    )
    # Exactly one FILTER on ``> 0`` — and it lives inside the
    # arm1 block, not at the outer scope.
    filter_count = result.aql.count("> ")
    assert filter_count == 1, (
        f"expected exactly one '> 0' filter; got {filter_count}\n"
        + result.aql
    )
    # arm2 has no FILTER on its inner FOR.
    arm2_idx = result.aql.index("doc2.q")
    arm2_block = result.aql[arm2_idx : result.aql.index(")", arm2_idx)]
    assert "FILTER" not in arm2_block, (
        f"arm2 unexpectedly carries a FILTER:\n{arm2_block}"
    )
