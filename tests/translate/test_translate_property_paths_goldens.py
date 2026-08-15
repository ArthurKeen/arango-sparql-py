"""Golden tests for SPARQL 1.1 property-path translation.

Three independent corpora live in :mod:`property_paths.yml` next to
this file:

* ``cases`` — supported paths (SequencePath, InvPath, and their
  compositions). Each entry produces a deterministic AQL string and
  bind-vars dict.
* ``unsupported`` — paths that currently raise
  :class:`~arango_sparql.errors.UnsupportedSparqlError` with a
  stable message (AlternativePath, MulPath, NegatedPath). Pinned
  here so the W3C XFAIL bucket reasons stay grep-friendly and so a
  future slice that implements one of these naturally fails this
  test (forcing the author to remove the entry).

A third small block of inline tests covers the *interactions*
between the path module and other visitor features: composition
with a type pattern and refusal on RPT-mapped subjects. These live
in Python rather than YAML because they're resolver-driven (the
Turtle ontology and physical-mapping inputs are richer than the
``ontology`` field in the YAML schema).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from arango_sparql.api import translate
from arango_sparql.errors import UnsupportedSparqlError
from arango_sparql.translate.mapping import MappingBundle, MappingSource
from arango_sparql.translate.resolver import SchemaResolver

GOLDEN_PATH = Path(__file__).parent / "property_paths.yml"


def _load_supported() -> list[tuple[str, str, str, str, dict, int | None]]:
    """Return ``(name, ontology_ttl, sparql, expected_aql,
    expected_bind_vars, property_path_max_depth)`` per supported-path golden.

    ``property_path_max_depth`` is ``None`` for cases that don't
    override the resolver default; MulPath cases set it explicitly so
    the UNION-of-fixed-paths AQL stays short and reviewable."""
    data = yaml.safe_load(GOLDEN_PATH.read_text())
    ttl = data["ontology"]
    out: list[tuple[str, str, str, str, dict, int | None]] = []
    for case in data["cases"]:
        out.append(
            (
                case["name"],
                ttl,
                case["sparql"],
                case["expected_aql"].rstrip("\n"),
                case["expected_bind_vars"],
                case.get("property_path_max_depth"),
            )
        )
    return out


def _load_unsupported() -> list[tuple[str, str, str]]:
    """Return ``(name, sparql, expected_error_substring)`` per
    unsupported-path golden."""
    data = yaml.safe_load(GOLDEN_PATH.read_text())
    out: list[tuple[str, str, str]] = []
    for case in data.get("unsupported", []) or []:
        out.append(
            (
                case["name"],
                case["sparql"],
                case["expected_error_substring"],
            )
        )
    return out


@pytest.mark.parametrize(
    "name, ontology_ttl, sparql, expected_aql, expected_bind_vars, max_depth",
    _load_supported(),
    ids=[c[0] for c in _load_supported()],
)
def test_property_path_supported_golden(
    name: str,
    ontology_ttl: str,
    sparql: str,
    expected_aql: str,
    expected_bind_vars: dict,
    max_depth: int | None,
) -> None:
    """Supported paths produce the exact AQL the golden declares.

    The empty-resolver path (``ontology=""``) is used for most cases
    because it mirrors the W3C analyzer convention and exercises
    the default-collection fallback the largest XFAIL buckets sit
    on. A separate inline test below covers the typed-class
    composition.
    """
    resolver = SchemaResolver.from_turtle(ontology_ttl, default_collection="Document")
    if max_depth is not None:
        resolver.property_path_max_depth = max_depth
    result = translate(sparql, resolver=resolver)
    assert result.aql == expected_aql, (
        f"AQL mismatch for {name!r}:\n--- expected ---\n{expected_aql}\n--- actual ---\n{result.aql}"
    )
    assert result.bind_vars == expected_bind_vars, (
        f"bind_vars mismatch for {name!r}:\n"
        f"--- expected ---\n{expected_bind_vars}\n"
        f"--- actual ---\n{result.bind_vars}"
    )


@pytest.mark.parametrize(
    "name, sparql, expected_substring",
    _load_unsupported(),
    ids=[c[0] for c in _load_unsupported()],
)
def test_property_path_unsupported_golden(
    name: str,
    sparql: str,
    expected_substring: str,
) -> None:
    """Unsupported paths raise with the exact message substring.

    Pinning the message keeps the W3C ``analyze_coverage.py`` XFAIL
    counter's "Top XFAIL reasons" table uniquely identifying each
    path operator. If a future slice implements one of these (and
    the error message disappears), this test forces the author to
    remove the entry — preventing an accidental regression where
    the error message changes but the XFAIL bucket name does not.
    """
    resolver = SchemaResolver.from_turtle("", default_collection="Document")
    with pytest.raises(UnsupportedSparqlError) as exc_info:
        translate(sparql, resolver=resolver)
    assert expected_substring in str(exc_info.value), (
        f"error message for {name!r} did not contain {expected_substring!r}; got {str(exc_info.value)!r}"
    )


# ---------------------------------------------------------------------------
# Resolver-driven composition tests.
#
# These live in Python (not YAML) because the per-case ontology /
# physical-mapping inputs are richer than the YAML harness's single
# ``ontology`` field. They cover the two interactions the path
# module has with the rest of the visitor that aren't visible in
# the empty-resolver corpus.
# ---------------------------------------------------------------------------


_TYPED_PERSON_OWL = """
@prefix : <http://ex.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .
:Person a owl:Class ;
    phys:collectionName "Person" .
"""


def test_sequence_path_composes_with_type_pattern() -> None:
    """A type pattern binds ``?s`` to the Person collection's FOR
    alias; the subsequent path expansion sees the existing
    ``var_to_doc_alias`` entry and threads through it without
    opening a redundant FOR. The intermediate variable still opens
    a fresh default-collection FOR because the resolver does not
    know which class the inner step's object lives in."""
    resolver = SchemaResolver.from_turtle(_TYPED_PERSON_OWL)
    result = translate(
        "PREFIX : <http://ex.org/> SELECT ?s ?o WHERE { ?s a :Person . ?s :p/:q ?o }",
        resolver=resolver,
    )
    assert result.aql == (
        "FOR doc1 IN @@c1_Person\n"
        'FILTER HAS(doc1, "p")\n'
        "FOR doc2 IN @@c2_Document\n"
        "FILTER doc2._uri == doc1.p\n"
        'FILTER HAS(doc2, "q")\n'
        "RETURN { s: doc1._uri, o: doc2.q }"
    ), result.aql
    assert result.bind_vars == {
        "@c1_Person": "Person",
        "@c2_Document": "Document",
    }


def test_property_path_on_rpt_subject_refuses() -> None:
    """An RPT-mapped subject + property path is explicitly rejected.

    The intermediate variables produced by path expansion would
    need to inherit the RPT class binding so each step reads from
    the triples table; that wiring is deferred to a follow-up slice.
    Until then the visitor refuses with a typed error rather than
    silently emitting wrong AQL — ``.cursor/rules/
    comprehensiveness-over-simplification.mdc`` mandates "no
    swallowed errors"."""
    rpt_ttl = """
    @prefix : <http://ex.org/> .
    @prefix owl: <http://www.w3.org/2002/07/owl#> .
    @prefix phys: <https://arango.solutions/phys#> .
    :Triples a owl:Class ;
        phys:mappingStyle "RPT" ;
        phys:triplesCollection "_triples" .
    """
    bundle = MappingBundle(
        physical_mapping={"entities": {}, "relationships": {}},
        owl_turtle=rpt_ttl,
        source=MappingSource(kind="manual"),
    )
    resolver = SchemaResolver.from_mapping_bundle(bundle)
    with pytest.raises(UnsupportedSparqlError) as exc_info:
        translate(
            "PREFIX : <http://ex.org/> SELECT ?s ?o WHERE { ?s a :Triples . ?s :p/:q ?o }",
            resolver=resolver,
        )
    assert "property paths on RPT-mapped subjects" in str(exc_info.value)


@pytest.mark.parametrize(
    "nested, equivalent",
    [
        # (P*)*  ==  P*   (W3C property-path/pp37)
        ("((:P)*)*", "(:P)*"),
        # (P+)+  ==  P+
        ("(:P+)+", "(:P)+"),
        # (P*)+  ==  P*   — outer + repeats a zero-admitting inner
        ("(:P*)+", "(:P)*"),
        # (P+)*  ==  P*   — outer * admits zero hops
        ("(:P+)*", "(:P)*"),
        # (P?)+  ==  P*   — one-or-more of optional collapses to *
        ("(:P?)+", "(:P)*"),
        # (P?)*  ==  P*
        ("(:P?)*", "(:P)*"),
        # (P+)?  ==  P*   — zero-or-(one-or-more) admits zero
        ("(:P+)?", "(:P)*"),
        # (P*)?  ==  P*
        ("(:P*)?", "(:P)*"),
        # (P?)?  ==  P?   — the only nesting that stays bounded
        ("(:P?)?", "(:P)?"),
        # Triple nesting folds the same way: ((P+)*)?  ==  P*
        ("((:P+)*)?", "(:P)*"),
    ],
)
def test_nested_mul_path_collapses_to_equivalent_modifier(nested: str, equivalent: str) -> None:
    """Nested transitive modifiers fold to a single equivalent
    modifier (SPARQL 1.1 §18.4).

    Rather than byte-pin the ~200-line UNION expansion of each form,
    we assert the stronger *semantic equivalence* property: the
    nested path must translate to byte-identical AQL as its
    single-modifier equivalent. This is exactly the invariant
    :func:`arango_sparql.translate.paths._combine_mul_modifiers`
    must preserve, and it catches any drift in the fold table that a
    fixed golden could mask. Covers W3C ``property-path/pp37``
    (``((:P)*)*``) plus the full nine-pair modifier matrix and a
    triple-nesting case to prove the fold loops correctly.
    """
    resolver = SchemaResolver.from_turtle("", default_collection="Document")
    prefix = "prefix : <http://example.org/> "
    nested_aql = translate(f"{prefix}SELECT ?X WHERE {{ :A0 {nested} ?X }}", resolver=resolver).aql
    equiv_aql = translate(f"{prefix}SELECT ?X WHERE {{ :A0 {equivalent} ?X }}", resolver=resolver).aql
    assert nested_aql == equiv_aql, (
        f"{nested} should translate identically to {equivalent}:\n"
        f"--- nested ---\n{nested_aql}\n--- equivalent ---\n{equiv_aql}"
    )


def test_select_star_omits_sequence_path_intermediates() -> None:
    """``SELECT *`` must not project SequencePath ``_path_<n>`` join vars.

    SPARQL 1.1 §18.2.4 projects in-scope variables only; the intermediates
    minted by ``:p1/:p2/:p3`` desugaring are internal. Emitting them made
    W3C ``property-path/pp01`` (and siblings) diverge despite a correct
    ``?x`` binding — see live-coverage slice after document-edge fidelity.
    """
    from arango_sparql.api import translate

    ontology = """
    @prefix ex: <http://www.example.org/schema#> .
    @prefix owl: <http://www.w3.org/2002/07/owl#> .
    @prefix phys: <https://arango.solutions/phys#> .
    ex:p1 a owl:ObjectProperty ; phys:edgeCollectionName "edge_p1" .
    ex:p2 a owl:ObjectProperty ; phys:edgeCollectionName "edge_p2" .
    ex:p3 a owl:ObjectProperty ; phys:edgeCollectionName "edge_p3" .
    """
    resolver = SchemaResolver.from_turtle(ontology, default_collection="Document")
    result = translate(
        """
        PREFIX ex: <http://www.example.org/schema#>
        PREFIX in: <http://www.example.org/instance#>
        SELECT * WHERE { in:a ex:p1/ex:p2/ex:p3 ?x }
        """,
        resolver=resolver,
    )
    assert "_path_" not in result.aql.split("RETURN", 1)[-1]
    assert "x:" in result.aql.split("RETURN", 1)[-1]


def test_mul_path_uses_union_distinct_set_semantics() -> None:
    """``:p+`` / ``:p*`` must collapse duplicate endpoints (SPARQL §9).

    Fixed-length UNION arms can reach the same node via different walks;
    bag ``UNION`` would multiply solutions (W3C pp12/pp21/pp37). Path
    evaluation is a set of nodes — AQL ``UNION_DISTINCT``.
    """
    resolver = SchemaResolver.from_turtle("", default_collection="Document")
    resolver.property_path_max_depth = 3
    plus = translate(
        "PREFIX : <http://ex.org/> SELECT ?o WHERE { :a :p+ ?o }",
        resolver=resolver,
    )
    star = translate(
        "PREFIX : <http://ex.org/> SELECT ?o WHERE { :a :p* ?o }",
        resolver=resolver,
    )
    assert "UNION_DISTINCT(" in plus.aql
    assert "UNION_DISTINCT(" in star.aql
    # Pattern UNION must stay bag-preserving.
    bag = translate(
        "PREFIX : <http://ex.org/> SELECT ?s ?o WHERE { { ?s :p ?o } UNION { ?s :q ?o } }",
        resolver=resolver,
    )
    assert "UNION_DISTINCT(" not in bag.aql
    assert " IN UNION(" in bag.aql


def test_mul_path_constant_endpoints_emit_single_existence_row() -> None:
    """``SELECT * WHERE { :a (:p)* :b }`` yields one empty binding if matched.

    Both endpoints are IRIs so no user variables are bound; the emitter
    projects a ``_path_hit`` sentinel under ``UNION_DISTINCT`` and omits
    it from the outer projection — never one empty row per walk length.
    """
    resolver = SchemaResolver.from_turtle("", default_collection="Document")
    resolver.property_path_max_depth = 2
    result = translate(
        "PREFIX : <http://ex.org/> SELECT * WHERE { :a (:p)* :b }",
        resolver=resolver,
    )
    assert "UNION_DISTINCT(" in result.aql
    assert "_path_hit: 1" in result.aql
    final_return = result.aql.rsplit("RETURN", 1)[-1]
    assert "_path_hit" not in final_return
    assert "{  }" in final_return or "{}" in final_return.replace(" ", "")


def test_fresh_path_var_increments_monotonically() -> None:
    """The intermediate-variable counter is per-query: two paths in
    the same query produce ``?_path_1``, ``?_path_2``, … in order,
    and a brand-new query resets to ``?_path_1``.

    This is an internal invariant of the binding state, but it's
    user-observable through the deterministic bind-var ordering
    downstream consumers (e.g. result-row hydration) rely on. A
    regression where the counter became class-level rather than
    instance-level would bleed numbering across queries — silent
    until the bind dict changed names between calls.
    """
    from arango_sparql.translate.builder import AqlQueryBuilder
    from arango_sparql.translate.visitor import AlgebraVisitor

    resolver = SchemaResolver.from_turtle("", default_collection="Document")

    # First query — produces a couple of fresh path vars.
    v1 = AlgebraVisitor(builder=AqlQueryBuilder(), resolver=resolver)
    a = v1._fresh_path_var()
    b = v1._fresh_path_var()
    assert str(a) == "_path_1"
    assert str(b) == "_path_2"

    # Second visitor — counter must reset.
    v2 = AlgebraVisitor(builder=AqlQueryBuilder(), resolver=resolver)
    c = v2._fresh_path_var()
    assert str(c) == "_path_1"
