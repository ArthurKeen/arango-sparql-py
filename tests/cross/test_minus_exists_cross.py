"""Cross-validation for SPARQL ``MINUS`` / ``EXISTS`` / ``NOT EXISTS``.

These three constructs lower to a correlated ``LET p = LENGTH((<probe>))``
+ ``FILTER p {== 0 | > 0}`` (``arango_sparql.translate.minus_exists``).
Until the interpreter learned to execute that probe shape they were
verified by golden AQL strings only — never by running the AQL and
comparing bindings to the W3C ground truth. This module closes that gap
for the *existing* (already-shipped) translations; the MINUS+OPTIONAL
conditional-add cases land in their own module alongside the visitor
change that enables them.

Uses the permissive ``Document`` resolver (empty ontology, single
default collection) so the setup mirrors the W3C harness exactly: no
type patterns needed, every subject lives in one collection.
"""

from __future__ import annotations

from typing import Any

import pytest

from arango_sparql.api import translate
from arango_sparql.translate.resolver import SchemaResolver
from tests.helpers.aql_interp import run_aql_subset
from tests.helpers.oxi import (
    assert_bindings_equal,
    drop_null_bindings,
    normalize_oxi_row,
    oxi_bindings,
)

oxi = pytest.importorskip("pyoxigraph", reason="pyoxigraph required for cross tests")

EX = "http://ex.org/"

# Source of truth: people with a ``name``; ``bob`` is hidden, ``carol``
# is an admin. The two flags give MINUS / EXISTS something to test
# against on both the subject-shared and value-shared axes.
DATA_TTL = """
@prefix : <http://ex.org/> .
:alice :name "Alice" .
:bob   :name "Bob" ; :hidden true .
:carol :name "Carol" ; :admin true .
:dave  :name "Dave" ; :hidden true ; :admin true .
"""


def _docs() -> dict[str, list[dict[str, Any]]]:
    return {
        "Document": [
            {"_uri": EX + "alice", "name": "Alice"},
            {"_uri": EX + "bob", "name": "Bob", "hidden": True},
            {"_uri": EX + "carol", "name": "Carol", "admin": True},
            {"_uri": EX + "dave", "name": "Dave", "hidden": True, "admin": True},
        ]
    }


CASES = [
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?s WHERE { ?s :name ?n MINUS { ?s :hidden true } }",
        id="minus_shared_subject",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?s WHERE { ?s :name ?n FILTER NOT EXISTS { ?s :hidden true } }",
        id="not_exists_shared_subject",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?s WHERE { ?s :name ?n FILTER EXISTS { ?s :hidden true } }",
        id="exists_shared_subject",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?s WHERE { ?s :name ?n "
        "FILTER ( NOT EXISTS { ?s :hidden true } && EXISTS { ?s :admin true } ) }",
        id="not_exists_and_exists_combined",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?s ?n WHERE { ?s :name ?n MINUS { ?s :hidden true } }",
        id="minus_projects_extra_var",
    ),
]


@pytest.fixture(scope="module")
def oxi_store() -> Any:
    store = oxi.Store()
    store.load(DATA_TTL.encode("utf-8"), oxi.RdfFormat.TURTLE)
    return store


def _run(sparql: str) -> list[dict[str, Any]]:
    resolver = SchemaResolver.from_turtle("", default_collection="Document", permissive_class_resolution=True)
    result = translate(sparql, resolver=resolver)
    return [drop_null_bindings(r) for r in run_aql_subset(result.aql, result.bind_vars, _docs())]


@pytest.mark.cross
@pytest.mark.parametrize("sparql", CASES)
def test_minus_exists_matches_oxigraph(oxi_store: Any, sparql: str) -> None:
    actual = _run(sparql)
    expected = [normalize_oxi_row(r) for r in oxi_bindings(oxi_store, sparql)]
    assert_bindings_equal(expected, actual)


# ---- Typed shared subject (issue #51) ----------------------------------
# The same people, mapped as ``:Person``. With the subject typed *inside*
# the probe (or after VALUES / BIND), the type pattern opened a FOR with no
# join to the value already bound, so the probe ignored the outer row (any
# hidden Person removed every Person) and VALUES / BIND crossed every Person.
TYPED_ONTOLOGY_TTL = """
@prefix : <http://ex.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .
:Person a owl:Class ; phys:collectionName "Document" .
"""
TYPED_DATA_TTL = (
    DATA_TTL
    + """
:alice a :Person . :bob a :Person . :carol a :Person . :dave a :Person .
"""
)

TYPED_CASES = [
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?s WHERE { ?s a :Person . MINUS { ?s a :Person ; :hidden true } }",
        id="minus_typed_shared_subject",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?s WHERE { ?s a :Person . FILTER NOT EXISTS { ?s a :Person ; :hidden true } }",
        id="not_exists_typed_shared_subject",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?s WHERE { ?s a :Person . FILTER EXISTS { ?s a :Person ; :admin true } }",
        id="exists_typed_shared_subject",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?s WHERE { VALUES ?s { :alice :carol } ?s a :Person }",
        id="values_then_type_pattern",
    ),
    pytest.param(
        "PREFIX : <http://ex.org/> SELECT ?s WHERE { BIND(:bob AS ?s) ?s a :Person }",
        id="bind_then_type_pattern",
    ),
]


@pytest.fixture(scope="module")
def typed_oxi_store() -> Any:
    store = oxi.Store()
    store.load(TYPED_DATA_TTL.encode("utf-8"), oxi.RdfFormat.TURTLE)
    return store


@pytest.mark.cross
@pytest.mark.parametrize("sparql", TYPED_CASES)
def test_typed_subject_joins_its_prior_binding(typed_oxi_store: Any, sparql: str) -> None:
    result = translate(sparql, resolver=SchemaResolver.from_turtle(TYPED_ONTOLOGY_TTL))
    actual = [drop_null_bindings(r) for r in run_aql_subset(result.aql, result.bind_vars, _docs())]
    expected = [normalize_oxi_row(r) for r in oxi_bindings(typed_oxi_store, sparql)]
    assert_bindings_equal(expected, actual)
