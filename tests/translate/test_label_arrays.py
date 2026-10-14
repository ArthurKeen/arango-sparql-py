"""Issue #10 — a multi-class (LABEL) collection whose type field is an ARRAY.

r2g's LPG layout stores a node's labels as ``labels: ["Person", "Customer"]``.
The translator emitted ``FILTER doc.labels == @v``, which is false for every
such document: zero rows, no error. ``builder.type_predicate`` now picks the
form by the field's shape (``phys:typeFieldIsArray``), and live analysis
records that shape (``schema/acquire.py::_apply_label_shape_enrichment``).
``tests/integration/test_label_arrays_live.py`` proves it on ArangoDB.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from arango_sparql.api import translate
from arango_sparql.schema import acquire
from arango_sparql.translate.builder import parse_flag, type_predicate
from arango_sparql.translate.mapping import MappingBundle
from arango_sparql.translate.resolver import SchemaResolver


def _ttl(flag: str | None) -> str:
    extra = f' ; phys:typeFieldIsArray "{flag}"' if flag is not None else ""
    return f"""
@prefix :     <http://ex.org/> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .
:Person a owl:Class ; phys:mappingStyle "LABEL" ; phys:collectionName "LpgNodes" ;
  phys:typeField "labels" ; phys:typeValue "Person"{extra} .
"""


def _aql(flag: str | None) -> str:
    q = "PREFIX : <http://ex.org/>\nSELECT ?n WHERE { ?p a :Person ; :name ?n }"
    return translate(q, resolver=SchemaResolver.from_turtle(_ttl(flag))).aql


def test_array_shape_uses_membership_served_by_the_array_index() -> None:
    assert type_predicate("d", "labels", "@v", is_array=True) == "@v IN d.labels"
    assert "FILTER @_p1_labels IN doc1.labels" in _aql("true")


def test_scalar_shape_keeps_plain_equality() -> None:
    assert type_predicate("d", "type", "@v", is_array=False) == "d.type == @v"
    assert "FILTER doc1.labels == @_p1_labels" in _aql("false")


def test_unknown_shape_is_correct_for_both() -> None:
    assert type_predicate("d", "f", "@v", is_array=None) == "(d.f == @v || @v IN d.f)"
    assert "FILTER (doc1.labels == @_p1_labels || @_p1_labels IN doc1.labels)" in _aql(None)


def test_a_non_identifier_type_field_is_quoted() -> None:
    assert type_predicate("d", "node:labels", "@v", is_array=True) == "@v IN d.`node:labels`"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("true", True),
        ("True", True),
        ("1", True),
        ("false", False),
        ("False", False),
        (None, None),
        ("x", None),
    ],
)
def test_flag_parsing(raw: str | None, expected: bool | None) -> None:
    assert parse_flag(raw) is expected


def test_the_flag_travels_through_a_mapping_bundle() -> None:
    bundle = MappingBundle(
        conceptual_schema={"entities": [{"name": "Person"}]},
        physical_mapping={
            "entities": {
                "Person": {
                    "style": "LABEL",
                    "collectionName": "LpgNodes",
                    "typeField": "labels",
                    "typeValue": "Person",
                    "typeFieldIsArray": True,
                }
            }
        },
        metadata={},
    )
    resolver = SchemaResolver.from_mapping_bundle(bundle)
    assert resolver.resolve_class("urn:arango-sparql:concept#Person").type_field_is_array is True
    q = "SELECT ?p WHERE { ?p a <urn:arango-sparql:concept#Person> }"
    assert "IN doc1.labels" in translate(q, resolver=resolver).aql


class _FakeAql:
    """``StandardDatabase.aql`` slice: ``execute(query, ..., bind_vars=...)``."""

    def __init__(self, shapes: dict[tuple[str, str], Any]) -> None:
        self._shapes = shapes
        self.calls: list[dict[str, Any]] = []

    def execute(
        self,
        query: str,
        count: bool = False,
        batch_size: int | None = None,
        ttl: Any = None,
        bind_vars: dict[str, Any] | None = None,
        **_kw: Any,
    ) -> list[Any]:
        bind_vars = bind_vars or {}
        self.calls.append(bind_vars)
        result = self._shapes.get((bind_vars["@coll"], bind_vars["field"]))
        if isinstance(result, Exception):
            raise result
        return [] if result is None else [result]


class _FakeDb:
    def __init__(self, shapes: dict[tuple[str, str], Any]) -> None:
        self.aql = _FakeAql(shapes)


def _label_bundle() -> MappingBundle:
    return MappingBundle(
        conceptual_schema={},
        physical_mapping={
            "entities": {
                "Person": {
                    "style": "LABEL",
                    "collectionName": "LpgNodes",
                    "typeField": "labels",
                    "typeValue": "Person",
                },
                "Order": {
                    "style": "LABEL",
                    "collectionName": "Orders",
                    "typeField": "kind",
                    "typeValue": "order",
                },
                "Empty": {
                    "style": "LABEL",
                    "collectionName": "Empties",
                    "typeField": "kind",
                    "typeValue": "x",
                },
                "Plain": {"style": "COLLECTION", "collectionName": "Plain"},
            }
        },
        metadata={},
        owl_turtle="@prefix : <http://ex.org/> .\n:Person a <http://www.w3.org/2002/07/owl#Class> .\n",
    )


def test_live_analysis_records_each_label_fields_shape() -> None:
    db = _FakeDb({("LpgNodes", "labels"): True, ("Orders", "kind"): False})  # Empties: no doc has the field
    out = acquire._apply_label_shape_enrichment(db, _label_bundle(), when=datetime.now(UTC))
    ents = out.physical_mapping["entities"]
    assert ents["Person"]["typeFieldIsArray"] is True
    assert ents["Order"]["typeFieldIsArray"] is False
    assert "typeFieldIsArray" not in ents["Empty"]  # unknown stays unknown (still-correct form)
    assert "typeFieldIsArray" not in ents["Plain"]  # COLLECTION style is never probed
    assert len(db.aql.calls) == 3
    assert ":Person phys:typeFieldIsArray true ." in out.owl_turtle
    assert ":Order phys:typeFieldIsArray false ." in out.owl_turtle
    # The appended OWL parses and the resolver reads the flag back.
    resolver = SchemaResolver.from_turtle(
        out.owl_turtle
        + '\n:Person <http://arangodb.com/schema/physical#collectionName> "LpgNodes" ; <http://arangodb.com/schema/physical#typeField> "labels" ; <http://arangodb.com/schema/physical#typeValue> "Person" ; <http://arangodb.com/schema/physical#mappingStyle> "LABEL" .\n'
    )
    aql = translate("PREFIX : <http://ex.org/>\nSELECT ?p WHERE { ?p a :Person }", resolver=resolver).aql
    assert "IN doc1.labels" in aql and "doc1.labels ==" not in aql


def test_a_failing_probe_leaves_the_bundle_correct() -> None:
    db = _FakeDb({("LpgNodes", "labels"): RuntimeError("forbidden"), ("Orders", "kind"): False})
    out = acquire._apply_label_shape_enrichment(db, _label_bundle(), when=datetime.now(UTC))
    assert "typeFieldIsArray" not in out.physical_mapping["entities"]["Person"]
    assert out.physical_mapping["entities"]["Order"]["typeFieldIsArray"] is False
