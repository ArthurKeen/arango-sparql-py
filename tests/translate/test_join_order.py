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

from arango_sparql.translate.visitor import _order_bgp

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
