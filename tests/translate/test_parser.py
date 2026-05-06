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
