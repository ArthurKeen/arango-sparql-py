"""Unit coverage for W3C RDF physicalisation profiles.

The live harness is Docker-gated; these tests pin the loader's document-edge
and RPT shapes with an in-memory database double so coverage regressions are
visible in the ordinary fast test loop.
"""

from __future__ import annotations

from typing import Any

from arango_sparql.translate.resolver import SchemaResolver
from tests.w3c.loader import load_w3c_data_to_arango


class _Collection:
    def __init__(self, *, edge: bool = False) -> None:
        self.edge = edge
        self.rows: list[dict[str, Any]] = []

    def insert_many(self, rows: list[dict[str, Any]]) -> None:
        self.rows.extend(rows)


class _Database:
    def __init__(self) -> None:
        self.collections: dict[str, _Collection] = {}

    def has_collection(self, name: str) -> bool:
        return name in self.collections

    def delete_collection(self, name: str) -> None:
        del self.collections[name]

    def create_collection(self, name: str, *, edge: bool = False) -> _Collection:
        collection = _Collection(edge=edge)
        self.collections[name] = collection
        return collection


def _data(tmp_path: Any) -> Any:
    path = tmp_path / "people.ttl"
    path.write_text(
        """
        @prefix : <http://example.test/> .

        :alice a :Person ; :name "Alice"@en ; :age 42 ; :knows :bob .
        :bob a :Person .
        """,
        encoding="utf-8",
    )
    return path


def test_document_edge_profile_preserves_object_triples_as_edges(tmp_path: Any) -> None:
    db = _Database()
    ontology, classes = load_w3c_data_to_arango(
        db,
        [_data(tmp_path)],
        "w3c_people_",
        storage_profile="document_edge",
    )

    assert classes == {"http://example.test/Person": "w3c_people_Person"}
    resolver = SchemaResolver.from_turtle(ontology, default_collection="w3c_people_Document")
    knows = resolver.resolve_property("http://example.test/knows")
    assert knows.is_object_property
    assert knows.edge_collection is not None
    assert db.collections[knows.edge_collection].edge

    edge_rows = db.collections[knows.edge_collection].rows
    # One default-document source plus the class-bound source replica.
    assert len(edge_rows) == 2
    assert {row["_from"].split("/")[0] for row in edge_rows} == {
        "w3c_people_Document",
        "w3c_people_Person",
    }
    assert {row["_to"].split("/")[0] for row in edge_rows} == {"w3c_people_Document"}


def test_rpt_profile_preserves_terms_and_maps_classes_to_one_triples_collection(tmp_path: Any) -> None:
    db = _Database()
    ontology, classes = load_w3c_data_to_arango(
        db,
        [_data(tmp_path)],
        "w3c_people_",
        storage_profile="rpt",
    )

    resolver = SchemaResolver.from_turtle(ontology, default_collection="w3c_people_Document")
    person = resolver.resolve_class("http://example.test/Person")
    assert person.style == "RPT"
    assert person.collection == "w3c_people_Triples"
    assert classes == {"http://example.test/Person": "w3c_people_Person"}

    rows = db.collections["w3c_people_Triples"].rows
    knows = next(row for row in rows if row["predicate"] == "http://example.test/knows")
    assert knows["object_uri"] == "http://example.test/bob"
    name = next(row for row in rows if row["predicate"] == "http://example.test/name")
    assert name["object_value"] == "Alice"
    assert name["object_language"] == "en"
    age = next(row for row in rows if row["predicate"] == "http://example.test/age")
    assert age["object_value"] == 42
    assert age["object_datatype"] == "http://www.w3.org/2001/XMLSchema#integer"


def test_document_edge_profile_preserves_blank_node_objects_as_terms(tmp_path: Any) -> None:
    path = tmp_path / "blank-object.ttl"
    path.write_text(
        """
        @prefix : <http://example.test/> .
        :subject :value 1, _:blank, 3 .
        """,
        encoding="utf-8",
    )
    db = _Database()

    load_w3c_data_to_arango(
        db,
        [path],
        "w3c_blank_",
        storage_profile="document_edge",
    )

    rows = db.collections["w3c_blank_Document"].rows
    subject = next(row for row in rows if row["_uri"] == "http://example.test/subject")
    assert 1 in subject["value"]
    assert 3 in subject["value"]
    assert any(isinstance(value, str) and value.startswith("_:") for value in subject["value"])
