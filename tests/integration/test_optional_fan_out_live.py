"""Issue #53 — under ``fan_out_list_values`` an OPTIONAL fans a list out too.

With the setting on, a list-valued attribute is one triple per element, and a
required triple binds once per element. An OPTIONAL over the same attribute
bound the whole list as one value, so it returned too few rows and counts over
it were short. It now yields one solution per element (or one unbound when
the attribute is missing).

Live because the bug is about what ArangoDB returns. Every query also runs
through pyoxigraph on the same data, the W3C ground truth, and the answers
must agree. Gated like the rest of the integration suite (``RUN_INTEGRATION=1``).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest

from arango_sparql.api import translate
from arango_sparql.translate.resolver import SchemaResolver
from tests.helpers.oxi import drop_null_bindings, normalize_oxi_row, oxi_bindings
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

oxi = pytest.importorskip("pyoxigraph", reason="pyoxigraph is the ground truth for these tests")

EX = "http://example.org/"
_PEOPLE = "Issue53People"

_TTL = f"""
@prefix :     <{EX}> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .
:Person a owl:Class ; phys:mappingStyle "COLLECTION" ; phys:collectionName "{_PEOPLE}" .
"""

# a has two emails (a list), b none, c one (a scalar).
_DOCS = [
    {"_key": "a", "_uri": EX + "a", "dept": "eng", "email": ["a@work.org", "yes@a.org"]},
    {"_key": "b", "_uri": EX + "b", "dept": "eng"},
    {"_key": "c", "_uri": EX + "c", "dept": "ops", "email": "c@work.org"},
]

_PREFIX = f"PREFIX : <{EX}>\n"


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
    if handle.has_collection(_PEOPLE):
        handle.delete_collection(_PEOPLE)
    handle.create_collection(_PEOPLE).insert_many(_DOCS)
    yield handle
    handle.delete_collection(_PEOPLE)


@pytest.fixture(scope="module")
def oxi_store() -> Any:
    lines = []
    for d in _DOCS:
        lines.append(f'<{d["_uri"]}> a <{EX}Person> ; <{EX}dept> "{d["dept"]}" .')
        emails = d.get("email", [])
        for email in [emails] if isinstance(emails, str) else emails:
            lines.append(f'<{d["_uri"]}> <{EX}email> "{email}" .')
    store = oxi.Store()
    store.load("\n".join(lines).encode("utf-8"), oxi.RdfFormat.TURTLE)
    return store


def _multiset(rows: list[dict[str, Any]]) -> list[str]:
    return sorted(json.dumps(r, sort_keys=True) for r in rows)


def _arango(db: Any, body: str) -> list[dict[str, Any]]:
    res = translate(_PREFIX + body, resolver=SchemaResolver.from_turtle(_TTL, fan_out_list_values=True))
    db.aql.validate(res.aql)
    return [drop_null_bindings(r) for r in db.aql.execute(res.aql, bind_vars=res.bind_vars)]


def _oxigraph(store: Any, body: str) -> list[dict[str, Any]]:
    return [normalize_oxi_row(r) for r in oxi_bindings(store, _PREFIX + body)]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        # The issue's repro: a's list is two solutions (it was one, holding the list).
        (
            "SELECT ?s ?e WHERE { ?s a :Person OPTIONAL { ?s :email ?e } }",
            [
                {"s": EX + "a", "e": "a@work.org"},
                {"s": EX + "a", "e": "yes@a.org"},
                {"s": EX + "b"},
                {"s": EX + "c", "e": "c@work.org"},
            ],
        ),
        # An inner FILTER tests each element; a person with no passing element
        # keeps one unbound solution.
        (
            'SELECT ?s ?e WHERE { ?s a :Person OPTIONAL { ?s :email ?e FILTER(STRSTARTS(?e, "yes")) } }',
            [{"s": EX + "a", "e": "yes@a.org"}, {"s": EX + "b"}, {"s": EX + "c"}],
        ),
        # Counts over it see every element (2 + 0 + 1; it was 2).
        ("SELECT (COUNT(?e) AS ?c) WHERE { ?s a :Person OPTIONAL { ?s :email ?e } }", [{"c": 3}]),
        (
            "SELECT ?d (COUNT(?e) AS ?c) WHERE { ?s a :Person ; :dept ?d OPTIONAL { ?s :email ?e } } GROUP BY ?d",
            [{"d": "eng", "c": 2}, {"d": "ops", "c": 1}],
        ),
        # Control, already right: the required triple fans out the same way.
        (
            "SELECT ?s ?e WHERE { ?s a :Person ; :email ?e }",
            [
                {"s": EX + "a", "e": "a@work.org"},
                {"s": EX + "a", "e": "yes@a.org"},
                {"s": EX + "c", "e": "c@work.org"},
            ],
        ),
    ],
)
def test_optional_list_attribute_matches_oxigraph(
    db: Any, oxi_store: Any, body: str, expected: list[dict[str, Any]]
) -> None:
    rows = _arango(db, body)
    assert _multiset(rows) == _multiset(expected)
    assert _multiset(rows) == _multiset(_oxigraph(oxi_store, body))
