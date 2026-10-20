"""OPTIONAL over a list-valued attribute under ``fan_out_list_values`` (issue #53).

With the setting on, a required triple over a list attribute binds once per
element (``FOR lv IN FLATTEN([attr])``). An OPTIONAL over the same attribute
bound the whole list as one value. It now iterates the elements in the padded
row subquery the edge OPTIONAL uses (#50), so each element is a solution and a
missing attribute leaves one with the variable unbound. Off (the default), a
list is one value in both, and the OPTIONAL is unchanged. The YAML golden
harness has no fan-out switch, so these goldens are inline.
"""

from __future__ import annotations

from arango_sparql.api import translate
from arango_sparql.translate.resolver import SchemaResolver

_TTL = """
@prefix : <http://ex.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .
:Person a owl:Class ; phys:collectionName "Person" .
"""


def _aql(sparql: str, *, fan_out: bool) -> tuple[str, dict]:
    res = translate(sparql, resolver=SchemaResolver.from_turtle(_TTL, fan_out_list_values=fan_out))
    return res.aql, dict(res.bind_vars)


def test_an_optional_attribute_fans_out_to_one_row_per_element() -> None:
    aql, binds = _aql(
        "PREFIX : <http://ex.org/> SELECT ?s ?e WHERE { ?s a :Person OPTIONAL { ?s :email ?e } }",
        fan_out=True,
    )
    assert aql == (
        "FOR doc1 IN @@c1_Person\n"
        "LET optsub3 = (\n"
        "  FOR lv2 IN FLATTEN([doc1.email])\n"
        "  FILTER lv2 != null\n"
        "  RETURN {f0: lv2}\n"
        ")\n"
        "FOR optrow4 IN (LENGTH(optsub3) > 0 ? optsub3 : [null])\n"
        "RETURN { s: NOT_NULL(doc1._uri, doc1._id), e: optrow4.f0 }"
    )
    assert binds == {"@c1_Person": "Person"}


def test_an_inner_filter_tests_each_element() -> None:
    aql, binds = _aql(
        'PREFIX : <http://ex.org/> SELECT ?s ?e WHERE { ?s a :Person OPTIONAL { ?s :email ?e FILTER(STRSTARTS(?e, "y")) } }',
        fan_out=True,
    )
    assert (
        "  FOR lv2 IN FLATTEN([doc1.email])\n  FILTER lv2 != null\n  FILTER STARTS_WITH(lv2, @_p1)\n" in aql
    )
    assert binds == {"@c1_Person": "Person", "_p1": "y"}


def test_without_fan_out_the_optional_is_unchanged() -> None:
    aql, _ = _aql(
        "PREFIX : <http://ex.org/> SELECT ?s ?e WHERE { ?s a :Person OPTIONAL { ?s :email ?e } }",
        fan_out=False,
    )
    assert aql == "FOR doc1 IN @@c1_Person\nRETURN { s: NOT_NULL(doc1._uri, doc1._id), e: doc1.email }"
