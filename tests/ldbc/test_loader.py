"""Unit tests for the pure half of :mod:`tests.ldbc.loader` (no database)."""

from __future__ import annotations

from pathlib import Path

import pyoxigraph as ox
import pytest

from tests.ldbc import loader, normalize, vocab
from tests.ldbc.fixtures import write_mini

SN = vocab.SN
DBR = "http://dbpedia.org/resource/"


@pytest.fixture
def mini(tmp_path: Path) -> tuple[Path, normalize.NormalizeSummary, loader.LdbcGraph]:
    nt_path, summary = normalize.normalize(write_mini(tmp_path), tmp_path / "out")
    return nt_path, summary, loader.build_graph(loader.read_triples(nt_path))


def _edges(graph: loader.LdbcGraph, name: str) -> list[loader.Edge]:
    return [e for e in graph.edges if e.name == name]


def test_nodes_get_their_concrete_classes(mini) -> None:
    _, _, graph = mini
    assert graph.class_counts() == {
        "Person": 2,
        "Forum": 1,
        "Post": 1,
        "Comment": 1,
        "Tag": 1,
        "TagClass": 2,
        "City": 1,
        "Country": 1,
        "Continent": 1,
        "University": 1,
    }


def test_a_node_with_two_concrete_classes_keeps_both_labels_but_one_pg_collection(mini) -> None:
    _, _, graph = mini
    land = graph.nodes[DBR + "Land"]
    assert land.classes == {"Country", "Continent"}
    assert land.pg_class.name == "Country"
    assert loader.lpg_vertex(land)["labels"] == ["Continent", "Country", "Place"]


def test_reified_relationships_become_edges_with_their_attributes(mini) -> None:
    _, _, graph = mini
    (knows,) = _edges(graph, "KNOWS")
    assert (knows.source, knows.target) == (SN + "pers1", SN + "pers2")
    assert knows.attributes == {"creationDate": "2010-01-02T03:04:05.006Z"}
    assert knows.uri == vocab.GENID + "knows1"
    (member,) = _edges(graph, "HAS_MEMBER")
    assert (member.source, member.target) == (SN + "forum1", SN + "pers2")
    assert member.attributes == {"joinDate": "2010-02-01T00:00:00.000Z"}
    (study,) = _edges(graph, "STUDY_AT")
    assert study.attributes == {"classYear": 2005}


def test_likes_reaches_both_posts_and_comments(mini) -> None:
    _, _, graph = mini
    assert {e.target for e in _edges(graph, "LIKES")} == {SN + "post1", SN + "comm1"}


def test_tag_typing_becomes_has_type_and_tag_class_hierarchy_is_subclass_of(mini) -> None:
    _, _, graph = mini
    (has_type,) = _edges(graph, "HAS_TYPE")
    assert has_type.target == vocab.GENID + "tagclass2"
    (sub,) = _edges(graph, "IS_SUBCLASS_OF")
    assert (sub.source, sub.target) == (vocab.GENID + "tagclass2", vocab.GENID + "tagclass1")


def test_values_and_arrays(mini) -> None:
    _, _, graph = mini
    ann = loader.pg_vertex(graph.nodes[SN + "pers1"])
    assert ann["speaks"] == ["en", "fr"]  # always an array, sorted
    assert ann["id"] == 1 and ann["_key"] == "1" and ann["_uri"] == SN + "pers1"
    uni = loader.pg_vertex(graph.nodes[DBR + "Uni"])
    assert uni["url"] == DBR + "Uni_url"  # IRI-valued, stored as a string


def test_direct_knows_is_rdf_only(mini) -> None:
    _, _, graph = mini
    assert graph.rdf_only_triples == 2
    assert not any(e.name == "DIRECT_KNOWS" for e in graph.edges)


def test_pg_and_lpg_documents_reference_the_right_vertices(mini) -> None:
    _, _, graph = mini
    pg = [doc for coll, is_edge, doc in loader.pg_documents(graph) if coll == "knows"]
    assert pg == [
        {
            "_from": "person/1",
            "_to": "person/2",
            "creationDate": "2010-01-02T03:04:05.006Z",
            "_uri": vocab.GENID + "knows1",
        }
    ]
    lpg = [doc for coll, is_edge, doc in loader.lpg_documents(graph) if is_edge and doc["type"] == "KNOWS"]
    assert lpg[0]["_from"] == "nodes/Person_1" and lpg[0]["_to"] == "nodes/Person_2"


def test_expected_counts_cover_every_collection_written(mini) -> None:
    nt_path, summary, graph = mini
    for layout, documents in (
        ("pg", loader.pg_documents(graph)),
        ("lpg", loader.lpg_documents(graph)),
        ("rpt", loader.rpt_documents(loader.read_triples(nt_path))),
    ):
        written: dict[str, int] = {}
        for coll, _is_edge, _doc in documents:
            written[coll] = written.get(coll, 0) + 1
        assert loader.expected_counts(layout, graph, summary.triples_out) == written, layout


def test_rpt_rows_split_iris_and_literals() -> None:
    iri = loader.rpt_row(ox.Triple(ox.NamedNode(SN + "a"), ox.NamedNode(vocab.KNOWS), ox.NamedNode(SN + "b")))
    assert iri == {
        "subject_uri": SN + "a",
        "predicate": vocab.KNOWS,
        "object_uri": SN + "b",
        "object_value": None,
    }
    lit = loader.rpt_row(
        ox.Triple(
            ox.NamedNode(SN + "a"),
            ox.NamedNode(vocab.SNVOC + "id"),
            ox.Literal("7", datatype=ox.NamedNode(vocab.XSD + "long")),
        )
    )
    assert lit["object_value"] == 7 and lit["object_uri"] is None


def test_unknown_predicates_and_malformed_relationships_fail_loudly() -> None:
    def triple(s: str, p: str, o: str) -> ox.Triple:
        return ox.Triple(ox.NamedNode(s), ox.NamedNode(p), ox.NamedNode(o))

    person = triple(SN + "p", vocab.RDF_TYPE, vocab.SNVOC + "Person")
    with pytest.raises(loader.LoadError, match="unknown object property"):
        loader.build_graph([person, triple(SN + "p", vocab.SNVOC + "mystery", SN + "p")])
    with pytest.raises(loader.LoadError, match="has 0 targets"):
        loader.build_graph([person, triple(SN + "p", vocab.KNOWS, SN + "k")])
    with pytest.raises(loader.LoadError, match="no concrete LDBC class"):
        loader.build_graph([triple(SN + "p", vocab.RDF_TYPE, vocab.DBO + "Place")])
