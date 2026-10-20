"""Cross-validation: OPTIONAL over a list-valued attribute under fan-out (issue #53).

``fan_out_list_values`` reads a list attribute as one triple per element. The
same people go into pyoxigraph as RDF (``alice`` has two emails) and into the
interpreter as documents (``alice`` has an ``email`` list), and the bindings
must agree.
"""

from __future__ import annotations

from typing import Any

import pytest

from arango_sparql.api import translate
from arango_sparql.translate.resolver import SchemaResolver
from tests.helpers.aql_interp import run_aql_subset
from tests.helpers.oxi import assert_bindings_equal, drop_null_bindings, normalize_oxi_row, oxi_bindings

oxi = pytest.importorskip("pyoxigraph", reason="pyoxigraph required for cross tests")

EX = "http://ex.org/"

ONTOLOGY_TTL = """
@prefix : <http://ex.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .
:Person a owl:Class ; phys:collectionName "Person" .
"""

DATA_TTL = """
@prefix : <http://ex.org/> .
:alice a :Person ; :dept "eng" ; :email "alice@work.org", "yes@alice.org" .
:bob   a :Person ; :dept "eng" .
:carol a :Person ; :dept "ops" ; :email "carol@work.org" .
"""

DOCS: dict[str, list[dict[str, Any]]] = {
    "Person": [
        {"_uri": EX + "alice", "dept": "eng", "email": ["alice@work.org", "yes@alice.org"]},
        {"_uri": EX + "bob", "dept": "eng"},
        {"_uri": EX + "carol", "dept": "ops", "email": "carol@work.org"},
    ]
}

CASES = [
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?s ?e WHERE { ?s a :Person OPTIONAL { ?s :email ?e } }",
        id="optional_list_attribute",
    ),
    pytest.param(
        'PREFIX : <http://ex.org/> SELECT ?s ?e WHERE { ?s a :Person OPTIONAL { ?s :email ?e FILTER(STRSTARTS(?e, "yes")) } }',
        id="optional_list_attribute_inner_filter",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT (COUNT(?e) AS ?c) WHERE { ?s a :Person OPTIONAL { ?s :email ?e } }",
        id="count_over_optional_list_attribute",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?d (COUNT(?e) AS ?c) WHERE { ?s a :Person ; :dept ?d "
        "OPTIONAL { ?s :email ?e } } GROUP BY ?d",
        id="grouped_count_over_optional_list_attribute",
    ),
]


@pytest.fixture(scope="module")
def oxi_store() -> Any:
    store = oxi.Store()
    store.load(DATA_TTL.encode("utf-8"), oxi.RdfFormat.TURTLE)
    return store


@pytest.mark.cross
@pytest.mark.parametrize("sparql", CASES)
def test_optional_list_attribute_matches_oxigraph(oxi_store: Any, sparql: str) -> None:
    result = translate(sparql, resolver=SchemaResolver.from_turtle(ONTOLOGY_TTL, fan_out_list_values=True))
    actual = [drop_null_bindings(r) for r in run_aql_subset(result.aql, result.bind_vars, DOCS)]
    expected = [normalize_oxi_row(r) for r in oxi_bindings(oxi_store, sparql)]
    assert_bindings_equal(expected, actual)
