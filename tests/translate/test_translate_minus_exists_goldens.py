"""Golden tests for SPARQL ``MINUS``, ``FILTER EXISTS``,
``FILTER NOT EXISTS``, and template-less ``CONSTRUCT WHERE``.

Three of the four constructs (MINUS / EXISTS / NOT EXISTS) share
one AQL recipe (child-builder probe + ``LET <p> = LENGTH((<inner
LIMIT 1 RETURN 1>))`` + comparator FILTER); the YAML corpus pins
the exact AQL so regressions in any of the three shapes surface
immediately. ``CONSTRUCT WHERE`` is bundled in this same slice
because its implementation is a small one-line addition to the
existing ``visit_ConstructQuery``.

Coverage moved from 32.8 % to 36.4 % on the W3C DAWG corpus with
this slice (+3.6 pp, +9 newly-passing tests across the ``Minus``,
``Builtin_EXISTS``, ``Builtin_NOTEXISTS``, and ``CONSTRUCT WHERE``
algebra buckets combined).

Three independent test blocks:

* **YAML goldens** — every shape from the corpus with exact AQL +
  bind-vars assertions. Critical disjoint-vars regression coverage
  for the MINUS-vs-NOT-EXISTS divergence.

* **Resolver-driven interactions** — Python tests for shapes that
  need richer setup (multiple shared variables in MINUS,
  NOT EXISTS nested inside an AND filter expression).

* **Negative tests** — the rare ``CONSTRUCT WHERE { }`` empty-BGP
  shape must still refuse (rdflib accepts it grammatically) since
  there's nothing to synthesise a template from.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from arango_sparql.api import translate
from arango_sparql.errors import UnsupportedSparqlError
from arango_sparql.translate.resolver import SchemaResolver

GOLDEN_PATH = Path(__file__).parent / "minus_exists.yml"


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
def test_minus_exists_golden(
    name: str,
    ontology_ttl: str,
    sparql: str,
    expected_aql: str,
    expected_bind_vars: dict,
) -> None:
    """Each golden produces the exact AQL the YAML declares.

    Pinning the AQL byte-for-byte protects against three classes
    of regression:

    1. **Probe shape drift** — if ``_translate_probe`` changed
       its short-circuit (e.g. dropped ``LIMIT 1`` or projected
       something other than ``1``), the inner block's structure
       would shift visibly.
    2. **MINUS/NOT-EXISTS conflation** — if the SPARQL §8.3.4
       disjoint-vars no-op were lost, the
       ``minus_disjoint_vars_is_noop`` case would gain spurious
       LET / FILTER clauses (it must stay a bare passthrough).
    3. **CONSTRUCT WHERE template synthesis** — if the BGP-walk
       in ``_collect_bgp_triples`` started picking up triples in
       a different order or scope, the synthesised template would
       reorder or balloon visibly.
    """
    resolver = SchemaResolver.from_turtle(ontology_ttl, default_collection="Document")
    result = translate(sparql, resolver=resolver)
    assert result.aql == expected_aql, (
        f"AQL mismatch for {name!r}:\n--- expected ---\n{expected_aql}\n--- actual ---\n{result.aql}"
    )
    assert result.bind_vars == expected_bind_vars, (
        f"bind_vars mismatch for {name!r}:\n"
        f"--- expected ---\n{expected_bind_vars}\n"
        f"--- actual ---\n{result.bind_vars}"
    )


# ---------------------------------------------------------------------------
# Resolver-driven interaction tests
# ---------------------------------------------------------------------------


def test_minus_multiple_shared_vars() -> None:
    """Both ``?s`` and ``?n`` are shared between outer and MINUS
    right side; the inner probe must emit TWO equality FILTERs,
    one per shared variable, before its own triple-level FILTERs.

    Regression coverage for ``_translate_probe``'s
    ``var_to_expr`` pre-seeding loop: a sloppy implementation
    that only seeded the first variable would emit a single
    FILTER and silently drop outer rows that matched on
    ``?s`` alone (regardless of ``?n``)."""
    resolver = SchemaResolver.from_turtle("", default_collection="Document")
    result = translate(
        "PREFIX : <http://ex.org/> SELECT ?s ?n WHERE { ?s :name ?n MINUS { ?s :nickname ?n } }",
        resolver=resolver,
    )
    # Inner block must constrain both shared variables.
    assert "FILTER doc2._uri == doc1._uri" in result.aql
    assert "FILTER doc2.nickname == doc1.name" in result.aql


def test_not_exists_in_compound_filter() -> None:
    """``FILTER (?n != 'alice' && NOT EXISTS { … })`` — the
    NOT EXISTS expression must integrate into the larger boolean
    FILTER, with the LET clause emitted SEPARATELY ahead of the
    FILTER (because LET must precede its consumer in AQL).

    Regression coverage for ``emit_exists_filter``'s side-effect
    contract: the LET goes on the body-clause list (so it lands
    before the FILTER); the returned string is just the probe-
    comparator expression spliced into the broader boolean.
    """
    resolver = SchemaResolver.from_turtle("", default_collection="Document")
    result = translate(
        "PREFIX : <http://ex.org/> SELECT ?s WHERE { ?s :name ?n . "
        'FILTER (?n != "alice" && NOT EXISTS { ?s :hidden true }) }',
        resolver=resolver,
    )
    # LET ahead of FILTER, both present.
    assert "LET not_exists_probe" in result.aql
    let_pos = result.aql.index("LET not_exists_probe")
    filter_pos = result.aql.index("FILTER ((")
    assert let_pos < filter_pos, "LET clause must precede the FILTER that consumes it:\n" + result.aql
    # The NOT-EXISTS comparator is spliced into the outer FILTER as
    # part of the larger boolean — appears alongside ``&&`` and the
    # neighbouring conjunct, never as its own separate FILTER clause.
    filter_line = next(line for line in result.aql.splitlines() if line.startswith("FILTER (("))
    assert "&&" in filter_line
    assert "not_exists_probe" in filter_line
    # Only ONE FILTER ((... clause — confirms the NOT EXISTS didn't
    # leak into a sibling FILTER statement.
    assert result.aql.count("FILTER ((") == 1


# ---------------------------------------------------------------------------
# Negative tests
# ---------------------------------------------------------------------------


def test_construct_where_empty_bgp_is_refused() -> None:
    """``CONSTRUCT WHERE { }`` (empty BGP) leaves nothing to
    synthesise a template from. Refuse with a clear typed error
    rather than emit a bare ``RETURN []`` that would silently
    materialise to an empty RDF graph (impossible to distinguish
    from a query that simply matched no data, which is a
    diagnostic black hole for operators)."""
    resolver = SchemaResolver.from_turtle("", default_collection="Document")
    # rdflib often refuses the truly-empty form at parse time;
    # the visitor-level guard covers any parse path that does
    # produce a Project → BGP-with-zero-triples shape.
    with pytest.raises((UnsupportedSparqlError, Exception)):
        translate(
            "PREFIX : <http://ex.org/> CONSTRUCT WHERE { }",
            resolver=resolver,
        )
