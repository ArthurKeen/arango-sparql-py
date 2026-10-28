"""``xsd:dateTime ± xsd:duration`` (SPARQL 1.1 §17.3, XPath op:add-*-to-dateTime).

Lowered to AQL ``DATE_ADD`` / ``DATE_SUBTRACT`` with the duration bound as
its ISO 8601 lexical form. Before, the arithmetic went through the numeric
guard (``IS_NUMBER(a) && IS_NUMBER(b) ? a + b : null``), which made every
date window empty, and the duration was bound as a Python ``timedelta``
that the driver could not serialise (LDBC IC3 / IC4, PRD §13.6). Live
semantics are checked in ``tests/integration/test_date_arithmetic_live.py``.
"""

from __future__ import annotations

import json

import pytest

from arango_sparql.api import translate
from arango_sparql.errors import UnsupportedSparqlError
from arango_sparql.translate.resolver import SchemaResolver

_ONTOLOGY = """
@prefix : <http://ex.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .
:Event a owl:Class ; phys:collectionName "Event" .
:at a owl:DatatypeProperty .
"""
_PREFIX = "PREFIX : <http://ex.org/> PREFIX xsd: <http://www.w3.org/2001/XMLSchema#> "
_START = '"2010-12-01T00:00:00.000Z"^^xsd:dateTime'


def _translate(body: str):  # noqa: ANN202 — TranslateResult
    return translate(_PREFIX + body, resolver=SchemaResolver.from_turtle(_ONTOLOGY))


def test_date_plus_duration_is_date_add_with_the_lexical_duration() -> None:
    result = _translate(f'SELECT ?e WHERE {{ BIND({_START} + "P30D"^^xsd:duration AS ?e) }}')
    assert result.aql == (
        "FOR empty1 IN [1]\n"
        "LET bv3 = (FOR dt2 IN [@_p1] RETURN IS_STRING(dt2) ? DATE_ADD(dt2, @_p2_duration) : null)[0]\n"
        "RETURN { e: bv3 }"
    )
    assert result.bind_vars == {"_p1": "2010-12-01T00:00:00.000Z", "_p2_duration": "P30D"}
    json.dumps(result.bind_vars)  # the driver must be able to send it


def test_duration_plus_date_commutes() -> None:
    result = _translate(f'SELECT ?e WHERE {{ BIND("P1D"^^xsd:duration + {_START} AS ?e) }}')
    assert "DATE_ADD(dt2, @_p2_duration)" in result.aql
    assert result.bind_vars["_p2_duration"] == "P1D"


def test_date_minus_duration_is_date_subtract() -> None:
    result = _translate(f'SELECT ?e WHERE {{ BIND({_START} - "PT36H"^^xsd:dayTimeDuration AS ?e) }}')
    assert "DATE_SUBTRACT(dt2, @_p2_duration)" in result.aql
    assert result.bind_vars["_p2_duration"] == "PT36H"


def test_a_date_window_filter_uses_the_shifted_date() -> None:
    result = _translate(
        f'SELECT ?x WHERE {{ ?x a :Event ; :at ?at . FILTER(?at >= {_START} && ?at < {_START} + "P30D"^^xsd:duration) }}'
    )
    assert "DATE_ADD(" in result.aql and "IS_NUMBER" not in result.aql


@pytest.mark.parametrize(
    "body",
    [
        'SELECT ?e WHERE { BIND("P1D"^^xsd:duration AS ?e) }',
        'SELECT ?x WHERE { ?x a :Event ; :at ?at . FILTER(?at = "P1D"^^xsd:duration) }',
        f'SELECT ?e WHERE {{ BIND({_START} * "P1D"^^xsd:duration AS ?e) }}',
    ],
)
def test_a_duration_anywhere_else_fails_closed(body: str) -> None:
    with pytest.raises(UnsupportedSparqlError, match="xsd:duration"):
        _translate(body)
