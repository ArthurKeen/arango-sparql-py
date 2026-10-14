"""Issue #10 on a real ArangoDB — r2g-shaped LPG nodes with ``labels`` arrays.

Before the fix ``?p a :Person`` over ``labels: ["Person", "Customer"]`` returned
zero rows. Also checks multi-label semantics (one node answering two classes),
the live shape probe, and that the array form is served by r2g's ``labels[*]``
index. Gated like the rest of the integration suite (``RUN_INTEGRATION=1``).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest

from arango_sparql.api import translate
from arango_sparql.schema import acquire
from arango_sparql.translate.mapping import MappingBundle
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

_NODES, _TYPED = "Issue10LpgNodes", "Issue10Typed"


def _ttl(flag: str | None) -> str:
    extra = f' ; phys:typeFieldIsArray "{flag}"' if flag is not None else ""
    return f"""
@prefix :     <http://ex.org/> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .
:Person a owl:Class ; phys:mappingStyle "LABEL" ; phys:collectionName "{_NODES}" ;
  phys:typeField "labels" ; phys:typeValue "Person"{extra} .
:Customer a owl:Class ; phys:mappingStyle "LABEL" ; phys:collectionName "{_NODES}" ;
  phys:typeField "labels" ; phys:typeValue "Customer"{extra} .
:Order a owl:Class ; phys:mappingStyle "LABEL" ; phys:collectionName "{_TYPED}" ;
  phys:typeField "kind" ; phys:typeValue "order" .
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
    for name in (_NODES, _TYPED):
        if handle.has_collection(name):
            handle.delete_collection(name)
    nodes = handle.create_collection(_NODES)
    nodes.insert_many(
        [
            {"_key": "Person_1", "name": "Ada", "labels": ["Person", "Customer"]},
            {"_key": "Person_2", "name": "Bo", "labels": ["Person"]},
            {"_key": "Org_1", "name": "Acme", "labels": ["Organization"]},
        ]
    )
    nodes.add_index({"type": "persistent", "fields": ["labels[*]"]})  # what r2g provisions
    handle.create_collection(_TYPED).insert_many(
        [{"kind": "order", "name": "o-1"}, {"kind": "refund", "name": "r-1"}]
    )
    yield handle
    for name in (_NODES, _TYPED):
        handle.delete_collection(name)


def _run(db: Any, body: str, flag: str | None) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    res = translate("PREFIX : <http://ex.org/>\n" + body, resolver=SchemaResolver.from_turtle(_ttl(flag)))
    db.aql.validate(res.aql)
    return list(db.aql.execute(res.aql, bind_vars=res.bind_vars)), res.aql, res.bind_vars


@pytest.mark.parametrize("flag", [None, "true"], ids=["shape-unknown", "flagged-array"])
def test_label_array_nodes_are_found(db: Any, flag: str | None) -> None:
    rows, _, _ = _run(db, "SELECT ?n WHERE { ?p a :Person ; :name ?n }", flag)
    assert sorted(r["n"] for r in rows) == ["Ada", "Bo"]  # was [] before the fix


def test_one_node_answers_every_class_it_carries(db: Any) -> None:
    rows, _, _ = _run(db, "SELECT ?n WHERE { ?p a :Person , :Customer ; :name ?n }", "true")
    assert [r["n"] for r in rows] == ["Ada"]


def test_scalar_type_fields_still_work(db: Any) -> None:
    rows, _, _ = _run(db, "SELECT ?n WHERE { ?o a :Order ; :name ?n }", None)
    assert [r["n"] for r in rows] == ["o-1"]


def test_the_flagged_form_uses_the_label_array_index(db: Any) -> None:
    _, aql, bind_vars = _run(db, "SELECT ?n WHERE { ?p a :Person ; :name ?n }", "true")
    plan = db.aql.explain(aql, bind_vars=bind_vars)  # python-arango returns the plan dict itself
    indexes = [ix for node in plan["nodes"] for ix in node.get("indexes", [])]
    assert any(ix.get("fields") == ["labels[*]"] for ix in indexes), [n["type"] for n in plan["nodes"]]


def test_live_analysis_detects_the_shapes(db: Any) -> None:
    bundle = MappingBundle(
        conceptual_schema={},
        physical_mapping={
            "entities": {
                "Person": {
                    "style": "LABEL",
                    "collectionName": _NODES,
                    "typeField": "labels",
                    "typeValue": "Person",
                },
                "Order": {
                    "style": "LABEL",
                    "collectionName": _TYPED,
                    "typeField": "kind",
                    "typeValue": "order",
                },
            }
        },
        metadata={},
    )
    out = acquire._apply_label_shape_enrichment(db, bundle, when=datetime.now(UTC))
    assert out.physical_mapping["entities"]["Person"]["typeFieldIsArray"] is True
    assert out.physical_mapping["entities"]["Order"]["typeFieldIsArray"] is False
