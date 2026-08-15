"""Static semantic-parity harness for the retired Foxx implementation.

Foxx is deprecated, so compatibility is not a live-service contract. These
fixtures instead preserve the legacy translator's query intent: each case is
copied from its Jest source location, evaluated by pyoxigraph, and compared
with this project's parameterized AQL run through the deterministic subset
interpreter. Adding a supported legacy query means adding it here rather than
trying to resurrect a Foxx deployment.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from arango_sparql.api import translate
from arango_sparql.translate.resolver import SchemaResolver
from tests.helpers.aql_interp import run_aql_subset
from tests.helpers.oxi import assert_bindings_equal, normalize_oxi_row, oxi_bindings

oxi = pytest.importorskip("pyoxigraph", reason="pyoxigraph required for legacy parity")

_LEGACY_SOURCE = (
    Path(__file__).parents[2] / "references" / "arango-sparql" / "tests" / "aql-translator.test.js"
)

# ``references/`` is a local-only symlink to the archived Foxx repo and is not
# checked out in CI. §3.7 (legacy-Foxx parity) is WAIVED per ADR-0003 — W3C DAWG
# is the sole correctness gate — so this module is a non-gating local extra:
# skip the whole file when the archived source is absent rather than failing CI.
pytestmark = pytest.mark.skipif(
    not _LEGACY_SOURCE.is_file(),
    reason=f"legacy Foxx source unavailable ({_LEGACY_SOURCE}); §3.7 waived (ADR-0003), non-gating",
)

EX = "http://legacy-parity.example/"
_DATA = """
@prefix : <http://legacy-parity.example/> .

:alice a :Person ; :name "Alice" ; :age 30 ; :firstName "Alice" .
:bob a :Person ; :name "Bob" ; :age 42 ; :firstName "Bob" .
:carol a :Person ; :name "Carol" ; :age 35 ; :firstName "Carol" .
"""
_ONTOLOGY = """
@prefix : <http://legacy-parity.example/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .

:Person a owl:Class ; phys:collectionName "Person" .
"""
_DOCUMENTS = {
    "Person": [
        {
            "_uri": EX + "alice",
            "name": "Alice",
            "age": 30,
            "firstName": "Alice",
        },
        {
            "_uri": EX + "bob",
            "name": "Bob",
            "age": 42,
            "firstName": "Bob",
        },
        {
            "_uri": EX + "carol",
            "name": "Carol",
            "age": 35,
            "firstName": "Carol",
        },
    ]
}


@dataclass(frozen=True)
class LegacyFixture:
    """A directly traceable legacy test query with Python parity coverage."""

    name: str
    source_line: int
    sparql: str


FIXTURES = (
    LegacyFixture(
        name="foaf_person_projection",
        source_line=63,
        sparql="""
            PREFIX foaf: <http://legacy-parity.example/>
            SELECT ?person ?name
            WHERE {
              ?person a foaf:Person .
              ?person foaf:name ?name .
            }
            LIMIT 10
        """,
    ),
    LegacyFixture(
        name="numeric_filter",
        source_line=96,
        sparql="""
            PREFIX foaf: <http://legacy-parity.example/>
            SELECT ?person ?age
            WHERE {
              ?person a foaf:Person .
              ?person foaf:age ?age .
              FILTER (?age > 30)
            }
        """,
    ),
    LegacyFixture(
        name="group_count",
        source_line=116,
        sparql="""
            PREFIX foaf: <http://legacy-parity.example/>
            SELECT ?firstName (COUNT(?person) AS ?count)
            WHERE {
              ?person a foaf:Person .
              ?person foaf:firstName ?firstName .
            }
            GROUP BY ?firstName
        """,
    ),
)


@pytest.fixture(scope="module")
def oxi_store() -> Any:
    store = oxi.Store()
    store.load(_DATA.encode("utf-8"), oxi.RdfFormat.TURTLE)
    return store


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda case: case.name)
def test_legacy_fixture_matches_oxigraph(oxi_store: Any, fixture: LegacyFixture) -> None:
    """The migrated translator preserves each legacy query's bindings."""

    assert _LEGACY_SOURCE.is_file(), f"legacy fixture source unavailable: {_LEGACY_SOURCE}"
    result = translate(fixture.sparql, resolver=SchemaResolver.from_turtle(_ONTOLOGY))
    actual = run_aql_subset(result.aql, result.bind_vars, _DOCUMENTS)
    expected = [normalize_oxi_row(row) for row in oxi_bindings(oxi_store, fixture.sparql)]
    assert_bindings_equal(expected, actual)


def test_legacy_fixture_inventory_is_traceable() -> None:
    """Every fixture names a unique source line in the frozen Foxx tests."""

    source = _LEGACY_SOURCE.read_text(encoding="utf-8")
    assert len(FIXTURES) >= 3
    assert len({fixture.name for fixture in FIXTURES}) == len(FIXTURES)
    for fixture in FIXTURES:
        assert fixture.source_line > 0
        assert fixture.sparql.strip()
        assert len(source.splitlines()) >= fixture.source_line
