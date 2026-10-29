"""Live load of the LDBC data set into all three layouts (WP-LDBC-DATA).

Gated on ``RUN_INTEGRATION=1`` and ``RUN_LDBC=1`` (see ``conftest.py``).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from arango_sparql.schema.acquire import acquire_mapping_bundle
from tests.ldbc import loader, mappings, vocab

pytestmark = [pytest.mark.integration, pytest.mark.ldbc]


@pytest.mark.parametrize("layout", loader.LAYOUTS)
def test_layout_holds_exactly_what_the_model_says(
    layout: str, ldbc_db: Callable[[str], Any], ldbc_data: tuple[Path, Any]
) -> None:
    db = ldbc_db(layout)
    meta = db.collection(loader.META_COLLECTION).get("load")
    assert meta["data_sha256"] == ldbc_data[1].sha256
    for collection, expected in meta["counts"].items():
        assert db.collection(collection).count() == expected, collection


def test_rdf_classes_survive_into_every_layout(
    ldbc_db: Callable[[str], Any], ldbc_data: tuple[Path, Any]
) -> None:
    """RDF == RPT == LPG == PG per class, with PG's one documented exception."""
    _nt, summary = ldbc_data
    for cls in vocab.NODE_CLASSES:
        rdf = summary.class_counts.get(cls.iri, 0)
        rpt = (
            ldbc_db("rpt")
            .aql.execute(
                "RETURN LENGTH(FOR t IN triples FILTER t.predicate == @p AND t.object_uri == @c RETURN 1)",
                bind_vars={"p": vocab.RDF_TYPE, "c": cls.iri},
            )
            .next()
        )
        lpg = (
            ldbc_db("lpg")
            .aql.execute(
                "RETURN LENGTH(FOR n IN nodes FILTER @c IN n.labels RETURN 1)", bind_vars={"c": cls.name}
            )
            .next()
        )
        pg = ldbc_db("pg").collection(cls.pg_collection).count()
        if cls.name != "Tag":  # tags are typed by tag-class nodes, not by a class IRI
            assert rpt == rdf, cls.name
        assert lpg == rdf, cls.name
        # A node typed both Country and Continent (dbpedia:Australia) lives in
        # PG's country collection only (vocab.PG_CLASS_PRIORITY).
        assert pg == rdf - (1 if cls.name == "Continent" else 0), cls.name


def test_rdf_relationships_survive_into_every_layout(
    ldbc_db: Callable[[str], Any], ldbc_data: tuple[Path, Any]
) -> None:
    """RDF == RPT == LPG == PG per relationship (PRD LDBC-2).

    A plain relationship is one triple per edge; a reified one is one entry
    triple (``?person snvoc:knows ?node``) per edge. ``rdf:type`` is an edge
    only from a tag to its tag class, whose IRI is a skolemised blank node.
    """
    _nt, summary = ldbc_data
    rpt, lpg, pg = (ldbc_db(layout) for layout in ("rpt", "lpg", "pg"))

    def rpt_count(predicate: str, object_prefix: str = "") -> int:
        return rpt.aql.execute(
            "RETURN LENGTH(FOR t IN triples FILTER t.predicate == @p"
            " AND (@o == '' OR STARTS_WITH(t.object_uri, @o)) RETURN 1)",
            bind_vars={"p": predicate, "o": object_prefix},
        ).next()

    def lpg_count(name: str) -> int:
        return lpg.aql.execute(
            "RETURN LENGTH(FOR e IN edges FILTER e.type == @t RETURN 1)", bind_vars={"t": name}
        ).next()

    edges = [(r.name, r.iri, r.pg_collection) for r in vocab.RELATIONSHIPS] + [
        (r.name, r.entry, r.pg_collection) for r in vocab.REIFIED
    ]
    for name, iri, collection in edges:
        if iri == vocab.RDF_TYPE:
            rdf = in_rpt = rpt_count(iri, vocab.GENID)
        else:
            rdf, in_rpt = summary.predicate_counts.get(iri, 0), rpt_count(iri)
        assert rdf > 0, name
        assert (in_rpt, lpg_count(name), pg.collection(collection).count()) == (rdf, rdf, rdf), name


#: Where the committed fixture and the live acquisition legitimately differ.
#: Each entry is (layout, what, why); anything else that differs fails.
_KNOWN_MAPPING_DIFFERENCES = {
    ("lpg", "type_field"): "the fixture reads the `labels` array, so a node that is both "
    "Country and Continent matches both; acquisition picks the single `type` field",
    ("pg", "reified"): "plan D8: data-carrying relationships have no mapping yet",
    ("lpg", "reified"): "plan D8: data-carrying relationships have no mapping yet",
}


def _mapping_view(layout: str, resolve: Callable[[str], Any], entities: bool) -> dict[str, Any]:
    if entities:
        return {
            c.name: {
                "collection": (r := resolve(c.iri)).collection,
                "style": r.style,
                "type_field": r.type_field,
                "type_value": r.type_value,
            }
            for c in vocab.NODE_CLASSES
        }
    return {
        r.name: {
            "collection": (p := resolve(r.iri)).edge_collection,
            "style": p.mapping_style,
            "type_field": p.type_field,
            "type_value": p.type_value,
        }
        for r in vocab.RELATIONSHIPS
        if r.iri != vocab.RDF_TYPE  # an rdf:type edge is read as a class assertion, not a property
    }


@pytest.mark.parametrize("layout", ["lpg", "pg"])
def test_the_live_mapping_agrees_with_the_committed_fixture(
    layout: str, ldbc_db: Callable[[str], Any]
) -> None:
    """PRD LDBC-2: the mapping acquired from the database is diffed against the fixture."""
    fixture = mappings.resolver(layout)  # type: ignore[arg-type]
    live = acquire_mapping_bundle(ldbc_db(layout)).physical_mapping

    def live_index(kind: str) -> dict[tuple[str, str | None], dict[str, Any]]:
        """The acquired entries keyed by what they point at, not by the name acquisition chose."""
        collection = "collectionName" if kind == "entities" else "edgeCollectionName"
        index = {}
        for e in live[kind].values():
            entry = {
                "collection": e[collection],
                "style": e["style"],
                "type_field": e.get("typeField"),
                "type_value": e.get("typeValue"),
            }
            index[(entry["collection"], entry["type_value"])] = entry
        return index

    differences = []
    for kind, resolve in (("entities", fixture.resolve_class), ("relationships", fixture.resolve_property)):
        index = live_index(kind)
        for name, want in _mapping_view(layout, resolve, kind == "entities").items():
            got = index.get((want["collection"], want["type_value"]))
            if got is None:
                differences.append(f"{kind} {name}: {want} is not in the live mapping")
                continue
            for field, value in want.items():
                if got[field] != value and (layout, field) not in _KNOWN_MAPPING_DIFFERENCES:
                    differences.append(f"{kind} {name}.{field}: fixture {value!r}, live {got[field]!r}")
    # The reified relationships exist in the data and in the live mapping only (D8).
    reified = {r.name for r in vocab.REIFIED}
    assert reified <= set(live["relationships"]), "the data lost a reified relationship"
    assert (layout, "reified") in _KNOWN_MAPPING_DIFFERENCES
    assert not differences, "\n".join(differences)


def test_the_live_rpt_mapping_finds_the_triples_table(ldbc_db: Callable[[str], Any]) -> None:
    fixture = mappings.resolver("rpt").triple_store_class()
    live = acquire_mapping_bundle(ldbc_db("rpt")).physical_mapping
    assert fixture is not None and fixture.collection == loader.RPT_COLLECTION
    rpt = [name for name, e in live["entities"].items() if e["style"] == "RPT"]
    assert rpt == [loader.RPT_COLLECTION]


def test_a_second_load_is_a_no_op(ldbc_db: Callable[[str], Any], ldbc_data: tuple[Path, Any]) -> None:
    nt_path, summary = ldbc_data
    db = ldbc_db("pg")
    report = loader.load_layout(
        db, "pg", nt_path, data_sha256=summary.sha256, triple_count=summary.triples_out
    )
    assert report.skipped


def test_declared_domains_match_the_data(ldbc_db: Callable[[str], Any]) -> None:
    """``vocab.DOMAINS`` (the fixtures' ``rdfs:domain``) is what the data says.

    The translator routes an untyped subject by its property's domain, so a
    wrong domain would send it to the wrong collection; check every
    predicate's subjects against the classes declared for it.

    Routing needs every subject to be in *a* declared class, not in only
    declared classes: a node typed both Country and Continent
    (``dbpedia:Australia``) has ``isPartOf`` as a Country, and is found
    there in PG and LPG. So: every subject has a declared class, and every
    declared class has a subject.
    """
    tag_iri = vocab.CLASS_BY_NAME["Tag"].iri
    for predicate, declared in vocab.DOMAINS.items():
        # One row per distinct combination of classes among the subjects.
        combinations = list(
            ldbc_db("rpt").aql.execute(
                """
                FOR s IN (FOR t IN triples FILTER t.predicate == @p RETURN DISTINCT t.subject_uri)
                  LET classes = SORTED_UNIQUE(
                    FOR ty IN triples FILTER ty.subject_uri == s AND ty.predicate == @rdftype
                    RETURN STARTS_WITH(ty.object_uri, @genid) ? @tag : ty.object_uri)
                  COLLECT combination = classes
                  RETURN combination
                """,
                bind_vars={"p": predicate, "rdftype": vocab.RDF_TYPE, "genid": vocab.GENID, "tag": tag_iri},
            )
        )
        named = [
            {vocab.CLASS_BY_IRI[c].name for c in combo if c in vocab.CLASS_BY_IRI} for combo in combinations
        ]
        # An untyped subject is a reified relationship's node (``_:knows`` carries
        # creationDate): an edge in PG and LPG, reached through its entry
        # predicate or the relationship the fixtures add to the domain (plan D8),
        # so it is not among the node classes checked here.
        reified_attributes = {a for r in vocab.REIFIED for a in r.attributes}
        if predicate in reified_attributes:
            named = [classes for classes in named if classes]
        assert all(classes & set(declared) for classes in named), (predicate, named)
        assert set().union(*(classes & set(declared) for classes in named)) == set(declared), predicate
