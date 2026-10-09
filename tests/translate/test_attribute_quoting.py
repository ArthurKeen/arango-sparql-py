"""Attribute names that are not AQL identifiers are quoted — or refused.

Regression (prod.demo IAM, 2026-10-08): NL generated
``?document :phys:typeValue ?t``. The property is undeclared, so the resolver
fell back to the local name ``phys:typeValue`` as the document attribute and
the visitor spliced it in bare — ``doc1.phys:typeValue`` — which ArangoDB
rejects (ERR 1501). The hard rule is never to emit plausible-but-wrong AQL.

Now (``translate/builder.py::attribute_ref``, mirroring arango-cypher-py's
backtick rule): an identifier stays bare, anything else is backtick-quoted, a
name AQL cannot quote (backtick / backslash) raises ``UnsupportedSparqlError``,
and ``HAS()`` receives a non-identifier name as a bind variable.
"""

from __future__ import annotations

import pytest

from arango_sparql.api import translate
from arango_sparql.errors import UnsupportedSparqlError
from arango_sparql.translate.builder import attribute_ref, is_aql_identifier
from arango_sparql.translate.resolver import SchemaResolver

_TTL = """
@prefix :     <http://example.org/> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .

:Doc a owl:Class ;
  phys:mappingStyle "COLLECTION" ;
  phys:collectionName "Docs" .
"""

_PREFIX = "PREFIX : <http://example.org/>\n"


def _translate(body: str):
    return translate(_PREFIX + body, resolver=SchemaResolver.from_turtle(_TTL))


def test_identifier_names_stay_bare() -> None:
    assert attribute_ref("doc1", "name") == "doc1.name"
    assert attribute_ref("doc1", "_key") == "doc1._key"


@pytest.mark.parametrize("name", ["phys:typeValue", "first name", "é", "a-b", "1st"])
def test_non_identifier_names_are_backtick_quoted(name: str) -> None:
    assert not is_aql_identifier(name)
    assert attribute_ref("doc1", name) == f"doc1.`{name}`"


@pytest.mark.parametrize("name", ["a`b", "a\\b", ""])
def test_names_aql_cannot_quote_are_refused(name: str) -> None:
    with pytest.raises(UnsupportedSparqlError):
        attribute_ref("doc1", name)


def test_the_prod_demo_query_emits_valid_aql() -> None:
    res = _translate("SELECT DISTINCT ?t WHERE { ?d a :Doc . ?d :phys:typeValue ?t . }")
    assert "doc1.`phys:typeValue`" in res.aql
    assert "doc1.phys:typeValue" not in res.aql.replace("`phys:typeValue`", "")
    # The HAS() existence check takes the name as a bind variable, not text.
    assert "HAS(doc1, @" in res.aql
    assert "phys:typeValue" in res.bind_vars.values()
    assert any(w.get("code") == "W_SCHEMA_UNMAPPED_IRI" for w in res.warnings)


def test_a_constant_object_on_a_non_identifier_property_no_longer_crashes() -> None:
    # The bind-variable hint used to be the attribute name, and a non-
    # identifier hint raised ValueError -> an unhandled 500.
    res = _translate('SELECT ?d WHERE { ?d a :Doc . ?d :phys:typeValue "x" . }')
    assert "doc1.`phys:typeValue`" in res.aql
    assert "x" in res.bind_vars.values()


def test_identifier_attribute_output_is_unchanged() -> None:
    res = _translate("SELECT ?n WHERE { ?d a :Doc . ?d :file_name ?n . }")
    assert 'HAS(doc1, "file_name")' in res.aql
    assert "doc1.file_name" in res.aql
