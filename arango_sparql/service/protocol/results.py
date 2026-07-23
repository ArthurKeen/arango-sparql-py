"""W3C SPARQL Results serialisers — JSON, XML, CSV, TSV.

Wire-format references:

* SPARQL Results JSON — https://www.w3.org/TR/sparql11-results-json/
* SPARQL Results XML  — https://www.w3.org/TR/rdf-sparql-XMLres/
* SPARQL CSV / TSV    — https://www.w3.org/TR/sparql11-results-csv-tsv/

Each format ships in two flavours:

* **SELECT** form — variable list in the head, one ``result`` per
  binding row.
* **ASK** form — single ``boolean`` body.

CONSTRUCT / DESCRIBE produce RDF, which the visitors don't emit yet
(v1.1 work). The protocol route negotiates the matching media type
list (:data:`negotiate.CONSTRUCT_PRIORITY`) but currently surfaces
``E_TRANSLATE_UNSUPPORTED_ALGEBRA`` for those query forms.

------------------------------------------------------------
Value typing
------------------------------------------------------------

The AQL rows that ``/execute`` returns are weakly typed dicts —
``{"x": "http://example/Alice", "y": 42}`` — because the translation
layer doesn't carry RDF type metadata across the AQL boundary. The
W3C wire formats require us to declare each value as one of ``uri``,
``literal``, ``bnode``, or typed-literal, so :func:`_classify_value`
performs best-effort type inference matching the legacy
``arango-sparql`` Foxx service:

* ``None``                    → unbound (omitted from the binding map)
* ``bool``                    → typed literal ``xsd:boolean``
* ``int``                     → typed literal ``xsd:integer``
* ``float``                   → typed literal ``xsd:double``
* ``dict`` with ``_id``       → ``uri`` (the document's ArangoDB id)
* ``str`` matching IRI shape  → ``uri``
* anything else as a fallback → plain literal (``str(value)``)

This is intentionally lossy — operators who need round-trip type
fidelity will use the ``/execute`` JSON envelope, not the W3C
wire formats.
"""

from __future__ import annotations

import csv
import io
import json
import re
import xml.sax.saxutils as _xml_escape
from collections.abc import Iterable, Mapping
from typing import Any

__all__ = [
    "render_ask",
    "render_select",
    "MEDIA_TYPE_TO_FORMAT",
    "FORMAT_NAMES",
]


# Symbolic names for the four tabular formats. Used in the route
# layer's logging tags ("format=json") and in this module's
# dispatch table.
FORMAT_NAMES: tuple[str, ...] = ("json", "xml", "csv", "tsv")


MEDIA_TYPE_TO_FORMAT: dict[str, str] = {
    "application/sparql-results+json": "json",
    "application/sparql-results+xml": "xml",
    "text/csv": "csv",
    "text/tab-separated-values": "tsv",
}


# IRI shape — leading scheme of two-or-more letters, ``://``, then
# at least one non-whitespace character. Deliberately permissive
# (``urn:`` and ``mailto:`` style URIs do not match — by design,
# because every IRI an ArangoDB document or literal would carry is
# either an ``http(s)://`` namespace IRI or a synthesised
# ``arango://`` URI from the resolver).
_IRI_SHAPE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+\-.]*://\S+$")


def _looks_like_iri(value: str) -> bool:
    """Return ``True`` when *value* matches the IRI heuristic.

    Used by :func:`_classify_value` to decide whether a string-typed
    binding should serialise as a ``uri`` or a plain ``literal``.
    See module docstring for the heuristic's scope.
    """

    return bool(_IRI_SHAPE_RE.match(value))


# ---------------------------------------------------------------------------
# Value classification
# ---------------------------------------------------------------------------
#
# Each binding value is classified into one of the records below.
# Wire-format serialisers consume these records — they're cheap,
# immutable, and identical across formats so we don't have four
# parallel "is this an IRI?" implementations drifting apart.

_XSD_INTEGER = "http://www.w3.org/2001/XMLSchema#integer"
_XSD_DOUBLE = "http://www.w3.org/2001/XMLSchema#double"
_XSD_BOOLEAN = "http://www.w3.org/2001/XMLSchema#boolean"


def _classify_value(value: Any) -> tuple[str, str, str | None]:
    """Return ``(rdf_kind, lex_form, datatype)`` for *value*.

    ``rdf_kind`` is one of ``uri`` / ``literal`` / ``typed-literal``.
    ``lex_form`` is the string the wire format will emit verbatim.
    ``datatype`` is the IRI of the XSD datatype for ``typed-literal``
    bindings, ``None`` otherwise.

    ``None`` values are *not* expected to reach this function — the
    caller filters them out at the binding-row level so the W3C
    "no binding entry for an unbound variable" rule is honoured.
    """

    if isinstance(value, bool):
        # ``bool`` *must* precede ``int`` because ``isinstance(True, int)``
        # is ``True`` in Python — order matters here.
        return "typed-literal", "true" if value else "false", _XSD_BOOLEAN
    if isinstance(value, int):
        return "typed-literal", str(value), _XSD_INTEGER
    if isinstance(value, float):
        # ``repr`` keeps round-tripping precision; the W3C grammar
        # accepts any xsd:double lexical form so this is safe.
        return "typed-literal", repr(value), _XSD_DOUBLE
    if isinstance(value, Mapping):
        # ArangoDB document — prefer ``_id`` (collection-qualified)
        # over ``_key`` (collection-relative) so the IRI is unique
        # across the database. If the document has no ``_id`` (e.g.
        # an inline RETURN of a sub-object), fall back to a JSON
        # serialisation so the data isn't silently dropped.
        if "_id" in value and isinstance(value["_id"], str):
            return "uri", value["_id"], None
        return "literal", json.dumps(value, default=str, sort_keys=True), None
    if isinstance(value, str):
        if _looks_like_iri(value):
            return "uri", value, None
        return "literal", value, None
    # Lists / tuples / arbitrary objects — JSON-encode so we don't
    # crash on structured AQL return shapes, but mark them as plain
    # literals (the operator gets to see *something*).
    return "literal", json.dumps(value, default=str, sort_keys=True), None


# ---------------------------------------------------------------------------
# vars-list resolution
# ---------------------------------------------------------------------------


def _resolve_vars(
    bindings: Iterable[Mapping[str, Any]],
    explicit_vars: Iterable[str] | None,
) -> list[str]:
    """Pick the variable list for the head section.

    Priority:

    1. The explicit projection list, if the route supplies one
       (this is the only way to surface vars when ``bindings`` is
       empty — important for spec-compliant clients that *expect*
       a head even on a zero-row result).
    2. Otherwise the union of keys across all binding rows,
       preserving first-seen order.
    """

    if explicit_vars is not None:
        return list(explicit_vars)
    out: list[str] = []
    seen: set[str] = set()
    for row in bindings:
        for key in row:
            if key not in seen:
                seen.add(key)
                out.append(key)
    return out


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


def _bindings_json(
    rows: Iterable[Mapping[str, Any]], vars_list: list[str]
) -> list[dict[str, dict[str, str]]]:
    out: list[dict[str, dict[str, str]]] = []
    for row in rows:
        binding: dict[str, dict[str, str]] = {}
        for var in vars_list:
            if var not in row:
                continue
            value = row[var]
            if value is None:
                continue
            kind, lex, datatype = _classify_value(value)
            entry: dict[str, str] = {"type": kind, "value": lex}
            if datatype is not None:
                entry["datatype"] = datatype
            binding[var] = entry
        out.append(binding)
    return out


def _render_json_select(rows: list[Mapping[str, Any]], vars_list: list[str]) -> str:
    payload = {
        "head": {"vars": vars_list},
        "results": {"bindings": _bindings_json(rows, vars_list)},
    }
    # ``ensure_ascii=False`` so unicode literals (Person names with
    # diacritics, Hebrew labels in a Bible ontology, etc.) round-trip
    # without xN-escape clutter. The protocol response is always
    # UTF-8 anyway.
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _render_json_ask(value: bool) -> str:
    payload = {"head": {}, "boolean": bool(value)}
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------------------
# XML
# ---------------------------------------------------------------------------
#
# We hand-roll the XML rather than building an ElementTree because
# the SPARQL XML format has a fixed shape and the namespace handling
# is simpler when we control the literal output. The escape function
# from xml.sax.saxutils handles ``<``, ``>``, ``&``; we extend it for
# ``"`` inside attribute values.

_SPARQL_XML_NS = "http://www.w3.org/2005/sparql-results#"


def _xml_attr(value: str) -> str:
    """Escape a value for use in an XML attribute (``"…"``)."""

    return _xml_escape.escape(value, {'"': "&quot;"})


def _xml_text(value: str) -> str:
    """Escape a value for use in XML text content."""

    return _xml_escape.escape(value)


def _render_xml_select(rows: list[Mapping[str, Any]], vars_list: list[str]) -> str:
    parts: list[str] = ['<?xml version="1.0" encoding="UTF-8"?>\n']
    parts.append(f'<sparql xmlns="{_xml_attr(_SPARQL_XML_NS)}">\n')
    parts.append("  <head>\n")
    for var in vars_list:
        parts.append(f'    <variable name="{_xml_attr(var)}"/>\n')
    parts.append("  </head>\n")
    parts.append("  <results>\n")
    for row in rows:
        parts.append("    <result>\n")
        for var in vars_list:
            if var not in row:
                continue
            value = row[var]
            if value is None:
                continue
            kind, lex, datatype = _classify_value(value)
            attr = f' name="{_xml_attr(var)}"'
            parts.append(f"      <binding{attr}>")
            if kind == "uri":
                parts.append(f"<uri>{_xml_text(lex)}</uri>")
            elif kind == "typed-literal":
                # The W3C SPARQL XML format §3.4 says the datatype
                # IRI goes on the ``literal`` element as a
                # ``datatype`` attribute — ``typed-literal`` was
                # the 2008 spec name and is no longer emitted.
                parts.append(f'<literal datatype="{_xml_attr(datatype or "")}">{_xml_text(lex)}</literal>')
            else:
                parts.append(f"<literal>{_xml_text(lex)}</literal>")
            parts.append("</binding>\n")
        parts.append("    </result>\n")
    parts.append("  </results>\n")
    parts.append("</sparql>\n")
    return "".join(parts)


def _render_xml_ask(value: bool) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<sparql xmlns="{_xml_attr(_SPARQL_XML_NS)}">\n'
        "  <head/>\n"
        f"  <boolean>{'true' if value else 'false'}</boolean>\n"
        "</sparql>\n"
    )


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------
#
# Per https://www.w3.org/TR/sparql11-results-csv-tsv/#csv:
# - Header row: variable names (NOT prefixed with ``?``).
# - Each cell: the *lexical form* of the value, with no type tag.
# - Unbound variables: empty cell.
# - Quote/escape per RFC 4180 — Python's ``csv`` module does this
#   for us, including doubling embedded ``"`` characters.


def _render_csv_select(rows: list[Mapping[str, Any]], vars_list: list[str]) -> str:
    buf = io.StringIO()
    # ``\r\n`` is the spec-mandated line terminator (RFC 4180 §2.1);
    # the ``csv`` module's default ``\r\n`` matches.
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(vars_list)
    for row in rows:
        out_row: list[str] = []
        for var in vars_list:
            value = row.get(var)
            if value is None:
                out_row.append("")
                continue
            _, lex, _ = _classify_value(value)
            out_row.append(lex)
        writer.writerow(out_row)
    return buf.getvalue()


def _render_csv_ask(value: bool) -> str:
    # The CSV spec doesn't define an ASK form. We follow the
    # convention used by Apache Jena's Fuseki and Virtuoso —
    # emit a single header ``_askResult`` followed by the boolean.
    # Simple, machine-parseable, and lets a `pandas.read_csv` consumer
    # treat the response uniformly with SELECT.
    return f"_askResult\r\n{'true' if value else 'false'}\r\n"


# ---------------------------------------------------------------------------
# TSV
# ---------------------------------------------------------------------------
#
# Per https://www.w3.org/TR/sparql11-results-csv-tsv/#tsv:
# - Header row: ``?var`` per column, tab-separated.
# - Each cell: the value rendered in *Turtle / N-Triples short form*
#   — IRIs in angle brackets, literals quoted with N-Triples
#   escape rules.
# - Unbound: empty cell.


_NT_ESCAPE = {
    "\\": "\\\\",
    '"': '\\"',
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def _nt_literal(value: str) -> str:
    """Encode *value* as the body of an N-Triples literal — the
    interior of a ``"…"`` pair, with all metacharacters escaped.
    """

    return "".join(_NT_ESCAPE.get(ch, ch) for ch in value)


def _tsv_cell(value: Any) -> str:
    if value is None:
        return ""
    kind, lex, datatype = _classify_value(value)
    if kind == "uri":
        return f"<{lex}>"
    if kind == "typed-literal":
        return f'"{_nt_literal(lex)}"^^<{datatype}>'
    return f'"{_nt_literal(lex)}"'


def _render_tsv_select(rows: list[Mapping[str, Any]], vars_list: list[str]) -> str:
    lines: list[str] = []
    lines.append("\t".join(f"?{v}" for v in vars_list))
    for row in rows:
        lines.append("\t".join(_tsv_cell(row.get(v)) for v in vars_list))
    return "\n".join(lines) + "\n"


def _render_tsv_ask(value: bool) -> str:
    # Same convention as CSV (Jena / Virtuoso) — single header +
    # value. ``?_askResult`` keeps it parseable as a 1-variable
    # binding row by clients that don't special-case ASK.
    return f"?_askResult\n{'true' if value else 'false'}\n"


# ---------------------------------------------------------------------------
# Public dispatch
# ---------------------------------------------------------------------------


_SELECT_RENDERERS = {
    "json": _render_json_select,
    "xml": _render_xml_select,
    "csv": _render_csv_select,
    "tsv": _render_tsv_select,
}

_ASK_RENDERERS = {
    "json": _render_json_ask,
    "xml": _render_xml_ask,
    "csv": _render_csv_ask,
    "tsv": _render_tsv_ask,
}


def render_select(
    media_type: str,
    rows: Iterable[Mapping[str, Any]],
    *,
    explicit_vars: Iterable[str] | None = None,
) -> str:
    """Serialise *rows* as the W3C SELECT-form payload for
    *media_type*.

    *explicit_vars* lets the route pass the projected variable list
    so the head's ``vars`` is correct even when ``rows`` is empty
    (W3C spec §2.4.1: a head MUST list every projected variable).
    When ``None``, the var list is derived from the row keys.

    Raises ``ValueError`` for unknown media types — the route
    layer must always negotiate against the supported list before
    calling this, so an unknown type here is a programming error,
    not a user error.
    """

    fmt = MEDIA_TYPE_TO_FORMAT.get(media_type)
    if fmt is None:
        raise ValueError(f"unsupported media type for SELECT: {media_type!r}")
    rows_list = list(rows)
    vars_list = _resolve_vars(rows_list, explicit_vars)
    return _SELECT_RENDERERS[fmt](rows_list, vars_list)


def render_ask(media_type: str, value: bool) -> str:
    """Serialise an ASK boolean as *media_type*. See
    :func:`render_select` for the unknown-media-type contract.
    """

    fmt = MEDIA_TYPE_TO_FORMAT.get(media_type)
    if fmt is None:
        raise ValueError(f"unsupported media type for ASK: {media_type!r}")
    return _ASK_RENDERERS[fmt](bool(value))
