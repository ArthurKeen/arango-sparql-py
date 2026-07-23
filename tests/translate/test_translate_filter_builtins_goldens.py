"""Golden tests for SPARQL FILTER / projection builtins shipped in
the v0.7 slice: ``Builtin_IF``, ``Builtin_CONCAT``,
``Builtin_LANG``, ``Builtin_LANGMATCHES``, plus the empty-BGP path
that ``visit_BGP`` now handles.

Coverage moved from 37.9 % to 41.5 % on the W3C DAWG corpus with
this slice (+3.6 pp, +9 newly-passing tests across the
``Builtin_LANGMATCHES``, ``Builtin_CONCAT``, ``Builtin_IF``, and
"no FOR clause" buckets — the last of which was an emergent bucket
unblocked by ``LANGMATCHES`` translation reaching queries with no
BGP triples).

Two test blocks:

* **YAML goldens** — every shape from the corpus with exact AQL
  + bind-vars assertions. The empty-BGP cases pin the
  ``FOR empty1 IN [1]`` opener.
* **Resolver-driven interactions** — Python tests for shapes that
  combine multiple new builtins, exercising the AQL's operator
  precedence around the ternary and the LANGMATCHES expansion.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from arango_sparql.api import translate
from arango_sparql.translate.resolver import SchemaResolver

GOLDEN_PATH = Path(__file__).parent / "filter_builtins.yml"


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
def test_filter_builtins_golden(
    name: str,
    ontology_ttl: str,
    sparql: str,
    expected_aql: str,
    expected_bind_vars: dict,
) -> None:
    """Each golden produces the exact AQL the YAML declares.

    Pinning the AQL byte-for-byte protects against:

    1. **Operator-precedence drift** in the IF ternary — the
       outer parens around the whole ``(... ? ... : ...)`` form
       are load-bearing (AQL's ``?:`` binds looser than ``&&``
       / ``||``), and per-operand parens guard against future
       compound operands changing the parse.
    2. **LANGMATCHES expansion shape** — RFC 4647's special-case
       ``"*"`` branch and the case-insensitive prefix dance
       (``STARTS_WITH(LOWER(tag), CONCAT(LOWER(range), '-'))``)
       are spec-defined; a regression that drops the ``CONCAT``
       or swaps the case sensitivity would silently change
       which W3C tests pass.
    3. **Empty-BGP opener** — the ``FOR empty1 IN [1]`` row is
       the load-bearing AQL bridge; a regression dropping it
       would re-surface the AqlEmit "no FOR clause" XFAILs
       these tests unblocked.
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


def test_concat_inside_if() -> None:
    """Nest CONCAT inside an IF arm — exercises the ternary's
    per-operand parens (no precedence collision with CONCAT's
    own commas inside the ``then`` arm)."""
    resolver = SchemaResolver.from_turtle("", default_collection="Document")
    result = translate(
        'PREFIX : <http://ex.org/> SELECT (IF(?n > 0, CONCAT(?n, "+"), "neg") AS ?x) WHERE { ?s :n ?n }',
        resolver=resolver,
    )
    # The ternary's then-arm is wrapped in parens so CONCAT's
    # commas don't get mistaken for a ternary delimiter.
    assert "? (CONCAT(" in result.aql
    # AQL well-formed: matching parens for CONCAT(...) and the ternary.
    assert result.aql.count("(") == result.aql.count(")"), "unbalanced parens:\n" + result.aql


def test_langmatches_compose_with_other_filters() -> None:
    """``FILTER (LANGMATCHES(LANG(?n), "en") && ?n != "")`` —
    the LANGMATCHES expansion's outer parens must not collide
    with the surrounding ``&&`` precedence.

    Regression coverage: a sloppy expansion that dropped the
    outermost paren would let ``&&`` bind inside the
    ``LANGMATCHES`` body and silently change semantics.
    """
    resolver = SchemaResolver.from_turtle("", default_collection="Document")
    result = translate(
        "PREFIX : <http://ex.org/> SELECT ?s WHERE { "
        '?s :n ?n . FILTER (LANGMATCHES(LANG(?n), "en") && ?n != "")'
        " }",
        resolver=resolver,
    )
    # Exactly one FILTER ((..)).
    assert result.aql.count("FILTER ((") == 1
    # Both LANGMATCHES & inequality must appear inside that FILTER.
    assert "STARTS_WITH(LOWER(" in result.aql
    assert "!= @" in result.aql
    # Conjunction operator present.
    filter_line = next(line for line in result.aql.splitlines() if line.startswith("FILTER (("))
    assert " && " in filter_line


def test_empty_bgp_with_multiple_binds() -> None:
    """Two ``BIND``s in an otherwise-empty WHERE — the
    empty-BGP opener fires once and both Extends attach to it.

    Regression coverage: a buggy ``visit_BGP`` that opened a
    new ``FOR`` per Extend would balloon the alias count and
    produce duplicate FOR clauses.
    """
    resolver = SchemaResolver.from_turtle("", default_collection="Document")
    result = translate(
        "PREFIX : <http://ex.org/> SELECT ?x ?y WHERE { BIND(1 AS ?x) BIND(2 AS ?y) }",
        resolver=resolver,
    )
    # Exactly one FOR — the empty-BGP opener.
    assert result.aql.count("FOR ") == 1
    assert "FOR empty1 IN [1]" in result.aql
    # Both LETs landed under that FOR.
    assert result.aql.count("LET bv") == 2
