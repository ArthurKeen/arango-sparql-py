"""Golden tests for SPARQL aggregate translation (rdflib ``AggregateJoin``).

The goldens live in ``aggregate.yml`` next to this file. See
``.cursor/rules/200-testing.mdc`` for the golden-test contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from arango_sparql.api import translate
from arango_sparql.translate.resolver import SchemaResolver

GOLDEN_PATH = Path(__file__).parent / "aggregate.yml"


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
def test_aggregate_golden(
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


def test_group_key_date_literal_is_json_safe() -> None:
    result = translate(
        """
        PREFIX : <http://example/>
        PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
        SELECT ?x (SAMPLE(?v) AS ?sample)
        WHERE { ?s :p ?v . OPTIONAL { ?s :q ?w } }
        GROUP BY (COALESCE(?w, "1605-11-05"^^xsd:date) AS ?x)
        """,
        resolver=SchemaResolver.from_turtle(""),
    )

    json.dumps(result.bind_vars)
    assert "1605-11-05" in result.bind_vars.values()


def test_group_concat_uses_space_as_the_default_separator() -> None:
    result = translate(
        """
        PREFIX : <http://example/>
        SELECT (GROUP_CONCAT(?value) AS ?joined)
        WHERE { ?subject :p ?value }
        """,
        resolver=SchemaResolver.from_turtle(""),
    )

    separator_binds = {name: value for name, value in result.bind_vars.items() if name.endswith("_sep")}
    assert separator_binds == {"_p1_sep": " "}


def test_grouped_aggregate_preserves_the_empty_solution() -> None:
    result = translate(
        """
        PREFIX : <http://example/>
        SELECT ?subject (MAX(?value) AS ?maximum)
        WHERE { ?subject :p ?value }
        GROUP BY ?subject
        """,
        resolver=SchemaResolver.from_turtle(""),
    )

    assert "LET results" in result.aql
    assert "IN (LENGTH(results" in result.aql
    assert "? [{}] : results" in result.aql


def test_having_does_not_turn_filtered_groups_into_an_empty_solution() -> None:
    result = translate(
        """
        PREFIX : <http://example/>
        SELECT ?subject (COUNT(?value) AS ?count)
        WHERE { ?subject :p ?value }
        GROUP BY ?subject
        HAVING (COUNT(?value) > 10)
        """,
        resolver=SchemaResolver.from_turtle(""),
    )

    assert "LET results" not in result.aql
    assert "FILTER" in result.aql
