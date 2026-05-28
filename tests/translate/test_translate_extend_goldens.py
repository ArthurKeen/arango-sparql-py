"""Golden tests for BIND (rdflib ``Extend``) translation.

The goldens live in ``extend.yml`` next to this file. See
``.cursor/rules/200-testing.mdc`` for the golden-test contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from arango_sparql.api import translate
from arango_sparql.translate.resolver import SchemaResolver

GOLDEN_PATH = Path(__file__).parent / "extend.yml"


def _load_cases() -> list[tuple[str, str, str, str, dict]]:
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
    _load_cases(),
    ids=[c[0] for c in _load_cases()],
)
def test_extend_golden(
    name: str,
    ontology_ttl: str,
    sparql: str,
    expected_aql: str,
    expected_bind_vars: dict,
) -> None:
    resolver = SchemaResolver.from_turtle(ontology_ttl)
    result = translate(sparql, resolver=resolver)
    assert result.aql == expected_aql, (
        f"AQL mismatch for {name!r}:\n--- expected ---\n{expected_aql}\n--- actual ---\n{result.aql}"
    )
    assert result.bind_vars == expected_bind_vars, (
        f"bind_vars mismatch for {name!r}:\n"
        f"--- expected ---\n{expected_bind_vars}\n"
        f"--- actual ---\n{result.bind_vars}"
    )


def test_bind_unbound_rhs_emits_warning() -> None:
    """``BIND(?nova AS ?z)`` where ``?nova`` is never bound by the
    surrounding pattern must emit a ``W_UNBOUND_VARIABLE_IN_EXPR``
    warning naming the offending variable.

    Pinning the warning code AND payload separately from the YAML
    golden because the AQL byte-shape (``LET bv2 = null``) is
    indistinguishable from a legitimate ``BIND(IRI() AS ?z)``
    that happens to evaluate to null at runtime; only the warning
    distinguishes "you have a typo / scope bug" from "we faithfully
    translated your null-yielding expression". A SPARQL typo
    (``?nove`` vs ``?nova``) presents identically without this
    warning, which is exactly the disambiguation operators need.
    """
    ttl = """@prefix : <http://ex.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .
:Person a owl:Class ; phys:collectionName "Person" .
"""
    resolver = SchemaResolver.from_turtle(ttl)
    result = translate(
        "PREFIX : <http://ex.org/> "
        "SELECT ?z WHERE { ?s a :Person ; :name ?n . BIND(?nova AS ?z) }",
        resolver=resolver,
    )
    matching = [
        w for w in result.warnings
        if w.get("code") == "W_UNBOUND_VARIABLE_IN_EXPR"
    ]
    assert len(matching) == 1, (
        f"expected exactly one W_UNBOUND_VARIABLE_IN_EXPR warning, "
        f"got {result.warnings!r}"
    )
    assert matching[0]["variable"] == "nova"
    assert "?nova" in matching[0]["message"]
    assert "SPARQL §17.2.1" in matching[0]["message"]
