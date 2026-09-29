"""Seed data for the ``schema_live`` tier (PRD §13.1).

One seed per physical model the detector must recognise. Each writes into
the collection names its schema fixture declares
(``tests/schema/fixtures/<name>.export.json``), so the fixture's
``physicalMapping`` is the oracle for which *style* every collection must be
detected as — the unit corpus and the live tier cannot drift apart without a
test failing.

Every seed stays under :data:`arango_sparql.schema.detect.DEFAULT_SAMPLE_SIZE`
rows per collection: PRD §6.3.1 specifies sampling, so a seed that relied on
row 21 would be testing the sample cap, not the classifier.

Shapes:

* ``pg`` — one collection per class, dedicated edge collection.
* ``lpg`` — one shared ``vertices`` collection discriminated by ``type``,
  one shared ``edges`` collection discriminated by ``type``.
* ``hybrid`` — PG ``users`` beside LPG ``vertices``; one generic edge
  collection spanning both (the cross-model endpoint case of §6.3.1 step 5).
* ``rpt`` — the legacy Foxx ``_triples`` layout with ``rdf:type`` rows and
  two object properties.
* ``rpt_pg_lpg_hybrid`` — all three models in one database, including an
  RPT object property whose subject lives in the LPG collection.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
NS = "http://example.org/live#"


def _iri(local: str) -> str:
    return f"{NS}{local}"


def _insert(db: Any, name: str, docs: list[dict[str, Any]], *, edge: bool = False) -> list[dict[str, Any]]:
    """Create collection *name* if needed, insert *docs*, return their metadata."""
    if not db.has_collection(name):
        # ``_triples`` starts with an underscore, which ArangoDB reserves for
        # system collections — the legacy Foxx layout relies on exactly that.
        db.create_collection(name, edge=edge, system=name.startswith("_"))
    return list(db.collection(name).insert_many(docs, return_new=False))


def _handles(meta: list[dict[str, Any]]) -> list[str]:
    return [m["_id"] for m in meta]


def seed_pg(db: Any) -> None:
    _insert(db, "docs", [{"title": f"Doc {i}"} for i in range(3)])
    _insert(db, "persons", [{"name": f"Person {i}"} for i in range(3)])
    _insert(db, "places", [{"city": f"City {i}"} for i in range(3)])
    users = _handles(_insert(db, "users", [{"handle": f"u{i}"} for i in range(4)]))
    _insert(
        db,
        "follows",
        [{"_from": users[i], "_to": users[(i + 1) % 4]} for i in range(4)],
        edge=True,
    )


def seed_lpg(db: Any) -> None:
    users = _handles(_insert(db, "vertices", [{"type": "User", "name": f"u{i}"} for i in range(4)]))
    docs = _handles(_insert(db, "vertices", [{"type": "Doc", "title": f"d{i}"} for i in range(3)]))
    _insert(db, "vertices", [{"type": "Note", "body": f"n{i}"} for i in range(2)])
    _insert(
        db,
        "edges",
        [{"_from": users[i], "_to": users[(i + 1) % 4], "type": "FOLLOWS"} for i in range(4)]
        + [{"_from": users[i], "_to": docs[i % 3], "type": "LIKES"} for i in range(4)],
        edge=True,
    )


def seed_hybrid(db: Any) -> None:
    users = _handles(_insert(db, "users", [{"handle": f"u{i}"} for i in range(4)]))
    docs = _handles(_insert(db, "vertices", [{"type": "Doc", "title": f"d{i}"} for i in range(3)]))
    _insert(db, "vertices", [{"type": "Note", "body": f"n{i}"} for i in range(2)])
    _insert(
        db,
        "edges",
        # FOLLOWS stays inside the PG collection; LIKES crosses PG -> LPG.
        [{"_from": users[i], "_to": users[(i + 1) % 4], "type": "FOLLOWS"} for i in range(4)]
        + [{"_from": users[i], "_to": docs[i % 3], "type": "LIKES"} for i in range(4)],
        edge=True,
    )


def _triple(s: str, p: str, *, o_uri: str | None = None, o_val: Any = None) -> dict[str, Any]:
    return {"subject_uri": s, "predicate": p, "object_uri": o_uri, "object_value": o_val}


def seed_rpt(db: Any) -> None:
    people = [_iri(f"person{i}") for i in range(3)]
    docs = [_iri(f"doc{i}") for i in range(2)]
    rows = [_triple(p, RDF_TYPE, o_uri=_iri("Person")) for p in people]
    rows += [_triple(d, RDF_TYPE, o_uri=_iri("Doc")) for d in docs]
    rows += [_triple(p, _iri("name"), o_val=f"Person {i}") for i, p in enumerate(people)]
    rows += [_triple(people[i], _iri("AUTHORED"), o_uri=docs[i % 2]) for i in range(3)]
    rows += [_triple(people[0], _iri("KNOWS"), o_uri=people[1])]
    rows += [_triple(people[1], _iri("KNOWS"), o_uri=people[2])]
    assert len(rows) <= 20, "seed must fit one detector sample"
    _insert(db, "_triples", rows)


def seed_rpt_pg_lpg_hybrid(db: Any) -> None:
    persons = _handles(_insert(db, "persons", [{"name": f"Person {i}"} for i in range(3)]))
    doc_meta = _insert(db, "vertices", [{"type": "Doc", "title": f"d{i}"} for i in range(3)])
    docs = _handles(doc_meta)
    notes = _handles(_insert(db, "vertices", [{"type": "Note", "body": f"n{i}"} for i in range(2)]))
    _insert(db, "authored", [{"_from": persons[i], "_to": docs[i]} for i in range(3)], edge=True)
    _insert(
        db,
        "edges",
        [{"_from": notes[i], "_to": docs[i], "type": "ANNOTATED_BY"} for i in range(2)],
        edge=True,
    )
    # The migration case: Doc moved to the LPG store, but its legacy triples
    # still assert its class and tag it — so TAGGED_WITH is typed Doc -> Tag
    # from rdf:type rows alone.
    doc_iris = [_iri(f"doc/{m['_key']}") for m in doc_meta]
    tags = [_iri(f"tag{i}") for i in range(2)]
    rows = [_triple(t, RDF_TYPE, o_uri=_iri("Tag")) for t in tags]
    rows += [_triple(t, _iri("label"), o_val=f"tag {i}") for i, t in enumerate(tags)]
    rows += [_triple(d, RDF_TYPE, o_uri=_iri("Doc")) for d in doc_iris]
    rows += [_triple(doc_iris[i], _iri("TAGGED_WITH"), o_uri=tags[i % 2]) for i in range(3)]
    assert len(rows) <= 20, "seed must fit one detector sample"
    _insert(db, "_triples", rows)


#: Fixture name -> seeder. Keys are the ``tests/schema/fixtures`` stems.
SEEDS: dict[str, Callable[[Any], None]] = {
    "pg": seed_pg,
    "lpg": seed_lpg,
    "hybrid": seed_hybrid,
    "rpt": seed_rpt,
    "rpt_pg_lpg_hybrid": seed_rpt_pg_lpg_hybrid,
}
