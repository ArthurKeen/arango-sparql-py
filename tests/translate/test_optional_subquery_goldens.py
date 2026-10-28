"""AQL shape of the general OPTIONAL emitter (``optional_subquery.py``).

Semantics are cross-checked against pyoxigraph on every model in
``tests/cross/test_multimodel_cross.py :: test_optional_subquery_matches_oxigraph``
(including a mutation check that dropping the ``[null]`` pad fails). This
pins the shape: the optional pattern as a correlated ``LET`` subquery that
sees the required side's document, then a ``[null]``-padded ``FOR``.
"""

from __future__ import annotations

import pytest

from arango_sparql.api import translate
from arango_sparql.errors import UnsupportedSparqlError
from arango_sparql.translate.resolver import SchemaResolver
from tests.cross.test_multimodel_cross import PG_ONTOLOGY


def test_a_cross_subject_optional_with_a_bind_is_a_padded_subquery() -> None:
    sparql = (
        "PREFIX : <http://ex.org/> SELECT ?t ?a2 WHERE { ?prj a :Project ; :title ?t ; :owner ?p . "
        "OPTIONAL { ?p a :Person ; :age ?a . BIND(?a * 2 AS ?a2) } }"
    )
    assert translate(sparql, resolver=SchemaResolver.from_turtle(PG_ONTOLOGY)).aql == (
        "FOR doc1 IN @@c1_Project\n"
        'FILTER HAS(doc1, "owner")\n'
        'FILTER HAS(doc1, "title")\n'
        "LET optsub6 = (\n"
        "  FOR doc2 IN @@c2_Person\n"
        "  FILTER NOT_NULL(doc2._uri, doc2._id) == doc1.owner\n"
        '  FILTER HAS(doc2, "age")\n'
        "  LET bv5 = (FOR num3 IN [doc2.age] FOR num4 IN [@_p1] RETURN IS_NUMBER(num3) && IS_NUMBER(num4) "
        "? (num3 * num4) : null)[0]\n"
        "  RETURN { a: doc2.age, a2: bv5 }\n"
        ")\n"
        "FOR optrow7 IN (LENGTH(optsub6) > 0 ? optsub6 : [null])\n"
        "RETURN { t: doc1.title, a2: optrow7.a2 }"
    )


def test_a_variable_predicate_in_an_optional_stays_refused() -> None:
    """ADR-0002 Option B (variable predicates in cross-subject OPTIONAL) is deferred."""
    with pytest.raises(UnsupportedSparqlError, match="cross-subject"):
        translate(
            "PREFIX : <http://ex.org/> SELECT ?o ?p2 WHERE { ?s :knows ?o . OPTIONAL { ?o ?p2 ?o2 } }",
            resolver=SchemaResolver.from_turtle("", default_collection="Document"),
        )
