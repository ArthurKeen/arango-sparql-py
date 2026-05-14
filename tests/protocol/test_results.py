"""Tests for :mod:`arango_sparql.service.protocol.results`.

Coverage:

* SELECT renderers — JSON, XML, CSV, TSV — produce W3C-compliant
  envelopes with the ``head.vars`` list and one ``binding`` entry
  per non-``None`` value.
* ASK renderers — JSON / XML carry the ``boolean`` payload exactly
  as the spec requires; CSV / TSV use the documented Jena/Virtuoso
  ``_askResult`` convention.
* Value typing — ints, floats, bools, dicts (with ``_id``), and
  strings classified into ``uri`` / ``literal`` / ``typed-literal``
  per the legacy Foxx service rules.
* Empty result sets carry an explicit ``vars`` head when the route
  passes ``explicit_vars`` (W3C spec §2.4.1).
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from typing import Any

import pytest

from arango_sparql.service.protocol.results import (
    FORMAT_NAMES,
    MEDIA_TYPE_TO_FORMAT,
    render_ask,
    render_select,
)

_NS = "http://www.w3.org/2005/sparql-results#"


# ---------------------------------------------------------------------------
# Format dispatch table
# ---------------------------------------------------------------------------


def test_format_names_and_media_types_are_in_sync() -> None:
    """Every format key in the dispatch table is one of the four
    documented ``FORMAT_NAMES``.
    """

    assert set(MEDIA_TYPE_TO_FORMAT.values()) == set(FORMAT_NAMES)
    assert FORMAT_NAMES == ("json", "xml", "csv", "tsv")


def test_render_select_rejects_unknown_media_type() -> None:
    with pytest.raises(ValueError, match="unsupported media type"):
        render_select("text/plain", [{"x": "value"}])


def test_render_ask_rejects_unknown_media_type() -> None:
    with pytest.raises(ValueError, match="unsupported media type"):
        render_ask("text/plain", True)


# ---------------------------------------------------------------------------
# JSON SELECT
# ---------------------------------------------------------------------------


def test_json_select_basic_shape() -> None:
    body = render_select(
        "application/sparql-results+json",
        [{"x": "http://ex.org/Alice", "y": "Alice"}],
    )
    payload = json.loads(body)
    assert payload["head"]["vars"] == ["x", "y"]
    bindings = payload["results"]["bindings"]
    assert len(bindings) == 1
    assert bindings[0]["x"] == {
        "type": "uri",
        "value": "http://ex.org/Alice",
    }
    assert bindings[0]["y"] == {"type": "literal", "value": "Alice"}


def test_json_select_classifies_typed_literals() -> None:
    body = render_select(
        "application/sparql-results+json",
        [{"age": 42, "active": True, "score": 9.5}],
    )
    payload = json.loads(body)
    binding = payload["results"]["bindings"][0]
    assert binding["age"]["type"] == "typed-literal"
    assert binding["age"]["value"] == "42"
    assert binding["age"]["datatype"].endswith("#integer")
    assert binding["active"]["datatype"].endswith("#boolean")
    assert binding["active"]["value"] == "true"
    assert binding["score"]["datatype"].endswith("#double")


def test_json_select_omits_unbound_variables() -> None:
    """W3C spec §3.3.2: an unbound variable has *no* binding entry
    in the results object — the variable name doesn't appear.
    """

    body = render_select(
        "application/sparql-results+json",
        [{"x": "http://ex.org/Alice", "y": None}],
        explicit_vars=["x", "y"],
    )
    payload = json.loads(body)
    binding = payload["results"]["bindings"][0]
    assert "x" in binding
    assert "y" not in binding


def test_json_select_unicode_round_trips() -> None:
    """Diacritics (Hebrew / Arabic / etc.) survive without
    \\uXXXX escape clutter — JSON is UTF-8 by default.
    """

    body = render_select(
        "application/sparql-results+json",
        [{"name": "Élise"}],
    )
    assert "Élise" in body
    assert json.loads(body)["results"]["bindings"][0]["name"]["value"] == "Élise"


def test_json_select_uses_explicit_vars_for_empty_results() -> None:
    """Empty result set still has to declare every projected var
    in the head (W3C spec §2.4.1) — clients use ``vars`` to
    pre-allocate column buckets even before any rows arrive.
    """

    body = render_select(
        "application/sparql-results+json",
        [],
        explicit_vars=["x", "y", "z"],
    )
    payload = json.loads(body)
    assert payload["head"]["vars"] == ["x", "y", "z"]
    assert payload["results"]["bindings"] == []


def test_json_select_classifies_arangodb_document_as_uri() -> None:
    body = render_select(
        "application/sparql-results+json",
        [{"doc": {"_id": "Person/123", "_key": "123", "name": "Alice"}}],
    )
    binding = json.loads(body)["results"]["bindings"][0]
    assert binding["doc"]["type"] == "uri"
    assert binding["doc"]["value"] == "Person/123"


# ---------------------------------------------------------------------------
# JSON ASK
# ---------------------------------------------------------------------------


def test_json_ask_true() -> None:
    body = render_ask("application/sparql-results+json", True)
    payload = json.loads(body)
    assert payload == {"head": {}, "boolean": True}


def test_json_ask_false() -> None:
    body = render_ask("application/sparql-results+json", False)
    assert json.loads(body)["boolean"] is False


# ---------------------------------------------------------------------------
# XML SELECT
# ---------------------------------------------------------------------------


def test_xml_select_parses_and_has_correct_namespace() -> None:
    body = render_select(
        "application/sparql-results+xml",
        [{"x": "http://ex.org/Alice", "n": 7}],
    )
    root = ET.fromstring(body)
    assert root.tag == f"{{{_NS}}}sparql"
    head = root.find(f"{{{_NS}}}head")
    assert head is not None
    var_names = [v.attrib["name"] for v in head.findall(f"{{{_NS}}}variable")]
    assert var_names == ["x", "n"]


def test_xml_select_emits_one_binding_per_variable() -> None:
    body = render_select(
        "application/sparql-results+xml",
        [{"x": "http://ex.org/Alice", "n": 7}],
    )
    root = ET.fromstring(body)
    result = root.find(f"{{{_NS}}}results/{{{_NS}}}result")
    assert result is not None
    bindings = result.findall(f"{{{_NS}}}binding")
    by_name: dict[str, Any] = {b.attrib["name"]: b for b in bindings}
    assert by_name["x"].find(f"{{{_NS}}}uri").text == "http://ex.org/Alice"
    literal = by_name["n"].find(f"{{{_NS}}}literal")
    assert literal.text == "7"
    assert literal.attrib["datatype"].endswith("#integer")


def test_xml_select_escapes_special_chars() -> None:
    body = render_select(
        "application/sparql-results+xml",
        [{"x": "<script>&amp;</script>"}],
    )
    # Raw payload must not contain the unescaped angle brackets.
    assert "<script>" not in body or "&lt;script&gt;" in body
    root = ET.fromstring(body)
    literal = root.find(f"{{{_NS}}}results/{{{_NS}}}result/{{{_NS}}}binding/{{{_NS}}}literal")
    assert literal.text == "<script>&amp;</script>"


def test_xml_select_omits_unbound_bindings() -> None:
    body = render_select(
        "application/sparql-results+xml",
        [{"x": "http://ex.org/Alice", "y": None}],
        explicit_vars=["x", "y"],
    )
    root = ET.fromstring(body)
    result = root.find(f"{{{_NS}}}results/{{{_NS}}}result")
    binding_names = {b.attrib["name"] for b in result.findall(f"{{{_NS}}}binding")}
    assert binding_names == {"x"}


# ---------------------------------------------------------------------------
# XML ASK
# ---------------------------------------------------------------------------


def test_xml_ask_true() -> None:
    body = render_ask("application/sparql-results+xml", True)
    root = ET.fromstring(body)
    assert root.find(f"{{{_NS}}}boolean").text == "true"


def test_xml_ask_false() -> None:
    body = render_ask("application/sparql-results+xml", False)
    root = ET.fromstring(body)
    assert root.find(f"{{{_NS}}}boolean").text == "false"


# ---------------------------------------------------------------------------
# CSV SELECT
# ---------------------------------------------------------------------------


def test_csv_select_header_and_row() -> None:
    body = render_select(
        "text/csv",
        [{"x": "http://ex.org/Alice", "y": "Alice"}],
    )
    lines = body.split("\r\n")
    assert lines[0] == "x,y"
    assert lines[1] == "http://ex.org/Alice,Alice"
    # Trailing CRLF after last row.
    assert lines[2] == ""


def test_csv_select_quotes_embedded_commas_and_quotes() -> None:
    """RFC 4180: a value containing ``,`` or ``"`` must be wrapped
    in double quotes; embedded ``"`` is doubled.
    """

    body = render_select("text/csv", [{"x": 'a,b "c"'}])
    # One quoted cell: ``"a,b ""c"""``
    assert '"a,b ""c"""' in body


def test_csv_select_unbound_is_empty_cell() -> None:
    body = render_select(
        "text/csv",
        [{"x": "Alice", "y": None}],
        explicit_vars=["x", "y"],
    )
    lines = body.split("\r\n")
    assert lines[0] == "x,y"
    assert lines[1] == "Alice,"


def test_csv_ask_uses_documented_convention() -> None:
    body_true = render_ask("text/csv", True)
    assert body_true == "_askResult\r\ntrue\r\n"
    body_false = render_ask("text/csv", False)
    assert body_false == "_askResult\r\nfalse\r\n"


# ---------------------------------------------------------------------------
# TSV SELECT
# ---------------------------------------------------------------------------


def test_tsv_select_header_uses_question_mark() -> None:
    body = render_select(
        "text/tab-separated-values",
        [{"x": "http://ex.org/Alice"}],
    )
    lines = body.split("\n")
    assert lines[0] == "?x"
    # IRI in angle brackets per N-Triples short form.
    assert lines[1] == "<http://ex.org/Alice>"


def test_tsv_select_literal_quoted_with_nt_escapes() -> None:
    body = render_select(
        "text/tab-separated-values",
        [{"x": "Alice\nO'Reilly\t\"escaped\""}],
    )
    lines = body.split("\n")
    # ``\n``, ``\t``, ``"`` all escaped per N-Triples; the literal
    # is wrapped in ``"…"``.
    assert lines[1].startswith('"')
    assert lines[1].endswith('"')
    # Only ``\n`` / ``\r`` / ``\t`` / ``\\`` / ``\"`` need escaping.
    assert "\\n" in lines[1]
    assert "\\t" in lines[1]
    assert '\\"' in lines[1]


def test_tsv_select_typed_literal_has_datatype_suffix() -> None:
    body = render_select(
        "text/tab-separated-values",
        [{"n": 42}],
    )
    lines = body.split("\n")
    assert lines[1] == '"42"^^<http://www.w3.org/2001/XMLSchema#integer>'


def test_tsv_select_unbound_is_empty_cell() -> None:
    body = render_select(
        "text/tab-separated-values",
        [{"x": "Alice", "y": None}],
        explicit_vars=["x", "y"],
    )
    lines = body.split("\n")
    assert lines[0] == "?x\t?y"
    # Trailing tab + empty cell.
    assert lines[1] == '"Alice"\t'


def test_tsv_ask_uses_documented_convention() -> None:
    assert render_ask("text/tab-separated-values", True) == "?_askResult\ntrue\n"
    assert render_ask("text/tab-separated-values", False) == "?_askResult\nfalse\n"


# ---------------------------------------------------------------------------
# Vars resolution
# ---------------------------------------------------------------------------


def test_vars_inferred_from_first_binding_when_explicit_is_none() -> None:
    body = render_select(
        "application/sparql-results+json",
        [{"a": "1", "b": "2"}, {"a": "3"}],
    )
    payload = json.loads(body)
    # Insertion order from the first row.
    assert payload["head"]["vars"] == ["a", "b"]


def test_vars_explicit_overrides_inference() -> None:
    body = render_select(
        "application/sparql-results+json",
        [{"a": "1", "b": "2"}],
        explicit_vars=["x", "a"],
    )
    payload = json.loads(body)
    assert payload["head"]["vars"] == ["x", "a"]
    # ``a`` is bound; ``x`` is unbound (not in row); ``b`` is dropped
    # because it isn't in the explicit vars list.
    binding = payload["results"]["bindings"][0]
    assert "a" in binding
    assert "x" not in binding
    assert "b" not in binding


# ---------------------------------------------------------------------------
# Iterable input — render_* must accept any Iterable[Mapping]
# ---------------------------------------------------------------------------


def _generator_rows() -> Iterable[Mapping[str, Any]]:
    yield {"x": "1"}
    yield {"x": "2"}


def test_render_select_accepts_generator() -> None:
    body = render_select("application/sparql-results+json", _generator_rows())
    payload = json.loads(body)
    assert len(payload["results"]["bindings"]) == 2
