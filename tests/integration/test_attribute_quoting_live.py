"""Non-identifier attribute names produce AQL a real ArangoDB accepts.

Companion to ``tests/translate/test_attribute_quoting.py``: those assert the
emitted text; this proves ArangoDB parses and executes it. The prod.demo
regression was an AQL *syntax* error (``doc1.phys:typeValue`` → ERR 1501),
which only a real server catches. Gated like the rest of the integration
suite (``RUN_INTEGRATION=1``; ArangoDB from ``docker-compose.yml``).
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

_COLLECTION = "AttrQuotingDocs"

_TTL = f"""
@prefix :     <http://example.org/> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .

:Doc a owl:Class ;
  phys:mappingStyle "COLLECTION" ;
  phys:collectionName "{_COLLECTION}" .
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
    if handle.has_collection(_COLLECTION):
        handle.delete_collection(_COLLECTION)
    coll = handle.create_collection(_COLLECTION)
    coll.insert_many(
        [
            {"_key": "a", "phys:typeValue": "guide", "first name": "Ada"},
            {"_key": "b", "phys:typeValue": "faq", "first name": "Bo"},
            {"_key": "c", "file_name": "x.pdf"},
        ]
    )
    yield handle
    handle.delete_collection(_COLLECTION)


def _run(db: Any, body: str) -> list[dict[str, Any]]:
    res = translate("PREFIX : <http://example.org/>\n" + body, resolver=SchemaResolver.from_turtle(_TTL))
    db.aql.validate(res.aql)  # the regression: this raised ERR 1501
    return list(db.aql.execute(res.aql, bind_vars=res.bind_vars))


def test_colon_attribute_validates_and_reads_the_right_field(db: Any) -> None:
    rows = _run(db, "SELECT DISTINCT ?t WHERE { ?d a :Doc . ?d :phys:typeValue ?t . }")
    assert sorted(r["t"] for r in rows) == ["faq", "guide"]


def test_colon_attribute_with_a_constant_object(db: Any) -> None:
    # The constant-object path used to raise ValueError (bind hint) — and the
    # filter must hit the colon attribute, not a bare-parsed one.
    rows = _run(db, 'SELECT ?d WHERE { ?d a :Doc . ?d :phys:typeValue "guide" . }')
    assert len(rows) == 1
