"""Unit tests for the visitor's binding-aware BGP join order (``_order_bgp``).

A BGP is a conjunction, so the order never changes an answer; it decides how
many rows each nested FOR touches. Found by the LDBC harness (PRD §13.6): the
previous fixed order put constant subjects last, so IS1 scanned every
``snvoc:id`` row before applying its one person. AQL-level effects are pinned
by the goldens (``rpt.yml :: triple_store_constant_subject_chain_is_followed``,
``hybrid.yml``, ``join.yml``, ``edge_traversal.yml``).
"""

from __future__ import annotations

from rdflib import RDF, Literal, URIRef, Variable

from arango_sparql.translate.visitor import _iter_bgp_order, _join_rank, _order_bgp

EX = "http://ex.org/"


def _t(s: str, p: str, o: str):  # noqa: ANN202 — an rdflib triple
    def term(x: str):  # noqa: ANN202
        if x.startswith("?"):
            return Variable(x[1:])
        if x == "a":
            return RDF.type
        if x.startswith('"'):
            return Literal(x.strip('"'))
        return URIRef(EX + x)

    return (term(s), term(p), term(o))


def test_a_known_subject_comes_first_and_its_object_leads_the_chain() -> None:
    city_id = _t("?c", "id", "?id")
    person_city = _t("alice", "city", "?c")
    assert _order_bgp([city_id, person_city], set()) == [person_city, city_id]


def test_a_type_pattern_anchors_an_unbound_subject_before_its_properties() -> None:
    name = _t("?p", "name", "?n")
    typed = _t("?p", "a", "Person")
    assert _order_bgp([name, typed], set()) == [typed, name]


def test_a_type_check_on_a_known_subject_is_just_a_filter() -> None:
    knows = _t("alice", "knows", "?f")
    typed = _t("?f", "a", "Person")
    other = _t("?x", "a", "Project")
    assert _order_bgp([other, typed, knows], set()) == [knows, typed, other]


def test_a_known_object_is_preferred_over_a_blind_scan() -> None:
    scan = _t("?m", "content", "?c")
    reverse = _t("?m", "creator", "alice")
    assert _order_bgp([scan, reverse], set()) == [reverse, scan]


def test_ranks_put_a_backward_walk_between_known_subjects_and_type_scans() -> None:
    creator = _t("?m", "creator", "?f")
    is_post = _t("?m", "a", "Post")
    assert _join_rank(is_post, {"m"}, set(), set()) == 0  # a class test, no scan
    assert _join_rank(creator, {"f"}, set(), set(), {creator}) == 2  # walk back from ?f
    assert _join_rank(is_post, set(), set(), set()) == 3  # a scan of the class
    assert _join_rank(creator, {"f"}, set(), set()) == 4  # ?f known, but no walk


def test_a_walkable_edge_comes_before_the_type_scan_it_narrows() -> None:
    # LDBC IC10: walk hasCreator back from each friend, then test the posts.
    is_post = _t("?m", "a", "Post")
    creator = _t("?m", "creator", "?f")
    assert _order_bgp([is_post, creator], {"f"}, walkable={creator}) == [creator, is_post]
    # Not walkable (no range to find ?f in): the class anchors ?m first.
    assert _order_bgp([is_post, creator], {"f"}) == [is_post, creator]


def test_walkability_is_asked_again_after_each_emitted_triple() -> None:
    # Whether ?f is a document is only known once the triple binding it is emitted.
    named = _t("alice", "friend", "?f")
    is_post = _t("?m", "a", "Post")
    creator = _t("?m", "creator", "?f")
    emitted: list = []

    def can_walk(triple) -> bool:  # noqa: ANN001 — an rdflib triple
        return triple == creator and named in emitted

    for triple in _iter_bgp_order([named, is_post, creator], set(), can_walk=can_walk):
        emitted.append(triple)
    assert emitted == [named, creator, is_post]


def test_outer_bindings_count_as_known() -> None:
    scan = _t("?x", "p", "?y")
    from_outer = _t("?m", "q", "?z")
    assert _order_bgp([scan, from_outer], {"m"}) == [from_outer, scan]


def test_ties_keep_rdflibs_order() -> None:
    triples = [_t("?a", "p", "?b"), _t("?c", "q", "?d"), _t("?e", "r", '"x"')]
    assert _order_bgp(triples[:2], set()) == triples[:2]
    assert _order_bgp(list(reversed(triples[:2])), set()) == list(reversed(triples[:2]))


def test_every_triple_is_emitted_once() -> None:
    triples = [_t("?a", "p", "?b"), _t("?b", "a", "C"), _t("x", "q", "?a"), _t("?c", "r", "?d")]
    ordered = _order_bgp(triples, set())
    assert sorted(map(str, ordered)) == sorted(map(str, triples))
