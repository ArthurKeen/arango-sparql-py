"""Issue #18 — subjects of documents WITHOUT ``_uri`` have a real identity.

The translator read a document's identity from ``_uri`` only (an RDF-import
convention). Ordinary ArangoDB data has no ``_uri``, so every subject bound
``null`` and ``COUNT(DISTINCT ?s)`` collapsed to 1 — on prod.demo IAM, 1
instead of 37,516 roles, and the Workbench showed ``null`` rows. Identity is
now ``_uri`` when present, else ``_id`` (``translate/builder.py::subject_ref``).

Live because the bug is about what ArangoDB returns, not the AQL text. Gated
like the rest of the integration suite (``RUN_INTEGRATION=1``).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from arango_sparql.api import translate
from arango_sparql.translate.resolver import SchemaResolver
from tests.integration.conftest import (
    DEFAULT_ARANGO_DB,
    DEFAULT_ARANGO_PASSWORD,
    DEFAULT_ARANGO_URL,
    DEFAULT_ARANGO_USER,
    arangodb_reachable,
    ensure_test_database,
    integration_enabled,
    try_boot_arangodb_via_compose,
)

pytestmark = pytest.mark.integration

_ASSETS, _OWNERS, _OWNS = "Issue18Assets", "Issue18Owners", "Issue18Owns"

_TTL = f"""
@prefix :     <http://example.org/> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix phys: <https://arango.solutions/phys#> .
:Asset a owl:Class ; phys:mappingStyle "COLLECTION" ; phys:collectionName "{_ASSETS}" .
:Owner a owl:Class ; phys:mappingStyle "COLLECTION" ; phys:collectionName "{_OWNERS}" .
:owns a owl:ObjectProperty ; rdfs:domain :Owner ; rdfs:range :Asset ;
  phys:edgeCollectionName "{_OWNS}" .
"""


@pytest.fixture(scope="module")
def db() -> Iterator[Any]:
    if not integration_enabled():
        pytest.skip("set RUN_INTEGRATION=1 to enable integration tests")
    if not arangodb_reachable() and not try_boot_arangodb_via_compose():
        pytest.skip(f"ArangoDB at {DEFAULT_ARANGO_URL} is unreachable and could not be booted")
    from arango import ArangoClient

    ensure_test_database()
    handle = ArangoClient(hosts=DEFAULT_ARANGO_URL).db(
        DEFAULT_ARANGO_DB, username=DEFAULT_ARANGO_USER, password=DEFAULT_ARANGO_PASSWORD
    )
    for name in (_ASSETS, _OWNERS, _OWNS):
        if handle.has_collection(name):
            handle.delete_collection(name)
    # Plain ArangoDB data — no _uri — except one RDF-style document.
    handle.create_collection(_ASSETS).insert_many(
        [
            {"_key": "a1", "name": "laptop"},
            {"_key": "a2", "name": "phone"},
            {"_key": "a3", "name": "badge", "_uri": "http://example.org/asset/badge"},
        ]
    )
    handle.create_collection(_OWNERS).insert_many(
        [{"_key": "o1", "name": "Ada"}, {"_key": "o2", "name": "Bo"}]
    )
    handle.create_collection(_OWNS, edge=True).insert_many(
        [
            {"_from": f"{_OWNERS}/o1", "_to": f"{_ASSETS}/a1"},
            {"_from": f"{_OWNERS}/o1", "_to": f"{_ASSETS}/a3"},
            {"_from": f"{_OWNERS}/o2", "_to": f"{_ASSETS}/a2"},
        ]
    )
    yield handle
    for name in (_OWNS, _ASSETS, _OWNERS):
        handle.delete_collection(name)


def _run(db: Any, body: str, *, default_collection: str = "Document") -> list[dict[str, Any]]:
    resolver = SchemaResolver.from_turtle(_TTL, default_collection=default_collection)
    res = translate("PREFIX : <http://example.org/>\n" + body, resolver=resolver)
    db.aql.validate(res.aql)
    return list(db.aql.execute(res.aql, bind_vars=res.bind_vars))


def test_subjects_are_distinct_and_never_null(db: Any) -> None:
    rows = _run(db, "SELECT ?s WHERE { ?s a :Asset }")
    assert sorted(r["s"] for r in rows) == sorted(
        [f"{_ASSETS}/a1", f"{_ASSETS}/a2", "http://example.org/asset/badge"]
    )


def test_count_distinct_counts_every_document(db: Any) -> None:
    # The issue's exact repro: this returned 1.
    rows = _run(db, "SELECT (COUNT(DISTINCT ?s) AS ?n) WHERE { ?s a :Asset }")
    assert rows == [{"n": 3}]


def test_a_join_across_an_edge_uses_the_same_identity(db: Any) -> None:
    rows = _run(db, "SELECT ?on ?an WHERE { ?o a :Owner ; :name ?on ; :owns ?a . ?a a :Asset ; :name ?an . }")
    assert sorted((r["on"], r["an"]) for r in rows) == [("Ada", "badge"), ("Ada", "laptop"), ("Bo", "phone")]


# A constant subject is looked up in the resolver's default collection (the
# existing routing; routing an ``_id``-shaped constant to its own collection
# is a separate follow-up), so these point the default at the assets.
def test_an_identity_from_a_result_works_as_a_constant(db: Any) -> None:
    rows = _run(db, f"SELECT ?n WHERE {{ <{_ASSETS}/a2> :name ?n . }}", default_collection=_ASSETS)
    assert rows == [{"n": "phone"}]


def test_a_document_with_uri_keeps_that_identity(db: Any) -> None:
    by_uri = _run(
        db, "SELECT ?n WHERE { <http://example.org/asset/badge> :name ?n . }", default_collection=_ASSETS
    )
    assert by_uri == [{"n": "badge"}]
    # ...and is NOT also matched by its _id: one document, one identity.
    by_id = _run(db, f"SELECT ?n WHERE {{ <{_ASSETS}/a3> :name ?n . }}", default_collection=_ASSETS)
    assert by_id == []
