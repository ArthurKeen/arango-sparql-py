"""Parser-wrapper unit tests — confirm error mapping and algebra shape."""

from __future__ import annotations

import pytest

from arango_sparql.errors import SparqlParseError
from arango_sparql.translate.parser import parse_sparql


def test_select_parses_to_select_query_root() -> None:
    parsed = parse_sparql("SELECT ?s WHERE { ?s ?p ?o }")
    assert parsed.algebra.name in {"SelectQuery", "Project", "Slice"}


def test_explicit_projection_preserves_declaration_order() -> None:
    parsed = parse_sparql("SELECT ?b ?a WHERE { ?a ?p ?b }")
    assert parsed.explicit_projection is not None
    assert [str(v) for v in parsed.explicit_projection] == ["b", "a"]


def test_select_star_has_no_explicit_projection() -> None:
    parsed = parse_sparql("SELECT * WHERE { ?s ?p ?o }")
    assert parsed.explicit_projection is None


def test_empty_string_raises() -> None:
    with pytest.raises(SparqlParseError):
        parse_sparql("")


def test_garbage_raises() -> None:
    with pytest.raises(SparqlParseError):
        parse_sparql("THIS IS NOT SPARQL")


def test_nested_service_is_unsupported_not_a_parse_error() -> None:
    # Valid SPARQL that rdflib's parser recurses on without bound
    # (RDFLib/rdflib#3082, fixed by #3608, unreleased): the query is not at
    # fault, so it must not be reported as malformed (LDBC IC13/IC14 use it).
    from arango_sparql.errors import UnsupportedSparqlError

    query = "SELECT * WHERE { SERVICE <http://a.example/> { SERVICE <http://b.example/> { ?s ?p ?o } } }"
    with pytest.raises(UnsupportedSparqlError, match="nested SERVICE") as caught:
        parse_sparql(query)
    assert caught.value.code == "E_SPARQL_UNSUPPORTED"
    assert not isinstance(caught.value, SparqlParseError)


def test_a_single_service_block_still_parses() -> None:
    parse_sparql("SELECT * WHERE { SERVICE <http://a.example/> { ?s ?p ?o } }")
