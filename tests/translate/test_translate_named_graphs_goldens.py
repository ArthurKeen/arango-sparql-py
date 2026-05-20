"""Golden + interaction tests for the v0.9 named-graphs slice.

Three test blocks:

1. **YAML goldens** (``named_graphs.yml``) — byte-for-byte AQL
   pinning for every shape ADR-0001 says the visitor should
   produce. Covers constant / variable graph IRIs, multi-triple
   per-subject reuse, implicit graph joins across subjects,
   inside/outside-mixing under lax mode, GRAPH inside a
   sub-SELECT, and wildcard-predicate skip-list cooperation.

2. **Strict default-graph mode** — Python tests for the
   ``default_graph_includes_named=False`` knob, which flips the
   "no GRAPH wrapper" case from "see all docs" to "see only
   docs with ``_graph IS NULL``". The flip stays opt-in until
   the live-execution harness lands and existing translation
   goldens can be co-updated.

3. **Custom graph_field** — confirms the resolver knob actually
   propagates into the FILTER attribute path and into the
   wildcard-predicate skip list. A deployment that already uses
   ``_graph`` for something else can override here without
   forking the visitor.

Why a separate Python file for cases 2 and 3 (rather than
YAML rows): each needs a non-default ``SchemaResolver``
construction argument, and the YAML schema in this repo binds
one resolver per file. Inlining the resolver construction in
Python keeps the YAML reusable for the all-default cases.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from arango_sparql.api import translate
from arango_sparql.translate.resolver import SchemaResolver

GOLDEN_PATH = Path(__file__).parent / "named_graphs.yml"


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
def test_named_graphs_golden(
    name: str,
    ontology_ttl: str,
    sparql: str,
    expected_aql: str,
    expected_bind_vars: dict,
) -> None:
    """Each YAML golden produces exactly the AQL the file declares.

    Byte-pinning is justified here because:

    1. **Storage convention.** ADR-0001 defines the per-document
       ``_graph`` storage convention; any drift in the emitted
       attribute path is a silent API break for every deployment
       that has shipped data under the convention.
    2. **Graph-variable join semantics.** The
       ``graph_variable_two_subjects_implicit_graph_join`` golden
       pins the cross-FOR equality FILTER — a regression that
       dropped that filter would silently break SPARQL's
       "same graph variable means same graph" semantics.
    3. **Wildcard-predicate skip list.** The ``_graph`` entry
       in ``_sys_attrs`` is load-bearing — without it,
       ``?s ?p ?o`` would surface the named-graph IRI as if it
       were a triple predicate. The
       ``graph_variable_with_wildcard_predicate`` golden pins
       both halves.
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
# Strict default-graph mode (opt-in knob)
# ---------------------------------------------------------------------------


def test_strict_default_graph_adds_null_filter() -> None:
    """``default_graph_includes_named=False`` (strict mode)
    emits ``FILTER alias._graph == null`` on every FOR opened
    outside a GRAPH wrapper.

    This is SPARQL 1.1 §8.3-conformant — the spec defers the
    "default graph membership" choice to the dataset
    declaration; strict mode is the choice that restricts
    default-graph reads to documents that explicitly belong
    to the default graph (``_graph`` missing or ``null``).

    The knob is opt-in for v0.9 so existing translation
    goldens (which were minted before the convention existed)
    don't churn; a future slice may flip it once the
    live-execution harness can co-update goldens
    mechanically.
    """
    resolver = SchemaResolver.from_turtle(
        "", default_collection="Document",
        default_graph_includes_named=False,
    )
    result = translate(
        "PREFIX : <http://ex.org/> SELECT ?n WHERE { ?s :name ?n }",
        resolver=resolver,
    )
    assert "FILTER doc1._graph == null" in result.aql, (
        "strict default-graph mode must emit the null filter on every "
        "FOR opened outside a GRAPH wrapper:\n" + result.aql
    )


def test_strict_default_graph_no_filter_inside_graph_wrapper() -> None:
    """Strict mode must NOT add ``_graph == null`` to FORs
    opened INSIDE a GRAPH wrapper — the active graph scope
    already constrains the document's graph membership.

    Regression coverage for a tempting-but-wrong
    implementation that always emits the null filter and
    then ADDS the graph filter on top, producing AQL that
    matches the empty set (a doc can't have ``_graph == null``
    AND ``_graph == @g`` simultaneously).
    """
    resolver = SchemaResolver.from_turtle(
        "", default_collection="Document",
        default_graph_includes_named=False,
    )
    result = translate(
        "PREFIX : <http://ex.org/> "
        "SELECT ?n WHERE { GRAPH <http://ex.org/g1> { ?s :name ?n } }",
        resolver=resolver,
    )
    assert "_graph == null" not in result.aql, (
        "strict mode must NOT emit `_graph == null` inside a GRAPH "
        "wrapper (the wrapper already constrains the graph):\n"
        + result.aql
    )
    assert "doc1._graph == @_p1_graph" in result.aql, result.aql


# ---------------------------------------------------------------------------
# Custom graph_field
# ---------------------------------------------------------------------------


def test_custom_graph_field_propagates_into_filter_and_skip_list() -> None:
    """``SchemaResolver.graph_field`` overrides the per-document
    attribute used by visit_Graph AND by the wildcard-predicate
    skip list. A deployment that already uses ``_graph`` for
    something unrelated can pick a different name without
    forking the visitor.

    The two halves are tested together because they MUST stay
    in sync — overriding one but not the other would either
    leak the graph IRI as a predicate or silently break
    GRAPH translation against deployments that renamed the
    field. The dual-skip-list-and-filter coverage here is the
    invariant that catches such a regression.
    """
    resolver = SchemaResolver.from_turtle(
        "", default_collection="Document",
        graph_field="_quad_graph",
    )
    # Half 1: visit_Graph emits the filter on the custom field.
    result_const = translate(
        "PREFIX : <http://ex.org/> "
        "SELECT ?n WHERE { GRAPH <http://ex.org/g1> { ?s :name ?n } }",
        resolver=resolver,
    )
    assert "doc1._quad_graph == @_p1_graph" in result_const.aql, (
        "GRAPH filter must use the configured graph_field name:\n"
        + result_const.aql
    )
    assert "doc1._graph" not in result_const.aql, (
        "default `_graph` name must not leak when graph_field is "
        "overridden:\n" + result_const.aql
    )

    # Half 2: the wildcard-predicate skip list includes the
    # custom field so ``?s ?p ?o`` doesn't surface ``_quad_graph``
    # as a predicate.
    result_wild = translate(
        "SELECT ?s ?p ?o WHERE { ?s ?p ?o }",
        resolver=resolver,
    )
    assert result_wild.bind_vars["_p1_sys_attrs"] == [
        "_quad_graph",
        "_uri",
    ], (
        "wildcard-predicate skip list must include the configured "
        "graph_field name:\n"
        + str(result_wild.bind_vars)
    )
