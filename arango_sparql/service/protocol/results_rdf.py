"""W3C SPARQL RDF result serialisers — CONSTRUCT / DESCRIBE wire formats.

Wire-format references:

* RDF/Turtle      — https://www.w3.org/TR/turtle/
* N-Triples       — https://www.w3.org/TR/n-triples/
* RDF/XML         — https://www.w3.org/TR/rdf-syntax-grammar/
* JSON-LD 1.1     — https://www.w3.org/TR/json-ld11/

This module is the CONSTRUCT/DESCRIBE counterpart of
:mod:`arango_sparql.service.protocol.results`. The two split cleanly:
:mod:`.results` handles SELECT/ASK (tabular bindings); this module
handles RDF (a set of triples), serialised by ``rdflib`` so the wire
formats stay W3C-conformant without our owning four serialiser stacks.

------------------------------------------------------------
Input shape
------------------------------------------------------------

The visitor emits one of two AQL ``RETURN`` shapes, both producing
**a list of triple dicts per cursor row**:

* CONSTRUCT::

    RETURN [{subject:…, predicate:…, object:…}, {subject:…, …}, …]

* DESCRIBE  (single described variable)::

    RETURN (FOR k IN ATTRIBUTES(<alias>) FILTER … RETURN {subject:…})

* DESCRIBE  (multiple described variables)::

    RETURN APPEND(<sub1>, <sub2>, …)

Every cursor row is therefore *iterable* and yields ``{subject,
predicate, object}`` dicts. :func:`render_construct` flattens every
row into a single :class:`rdflib.Graph` (set semantics dedupe
identical triples — RDF's natural behaviour) and serialises against
the negotiated media type.

------------------------------------------------------------
Value rehydration
------------------------------------------------------------

The AQL row carries each component as a weakly-typed JSON scalar.
:func:`_term_for_value` rehydrates each scalar into the appropriate
rdflib term using a heuristic matching the legacy
``arango-sparql`` Foxx service:

* ``None``                       → drop the whole triple (incomplete row)
* ``bool``                       → typed literal ``xsd:boolean``
* ``int``                        → typed literal ``xsd:integer``
* ``float``                      → typed literal ``xsd:double``
* ``dict`` with ``_id``          → IRI from the document's ArangoDB id
* ``str`` shaped like ``_:foo``  → :class:`rdflib.BNode`
* ``str`` matching IRI shape     → :class:`rdflib.URIRef`
* anything else                  → :class:`rdflib.Literal` (plain string)

The predicate position is special — RDF requires a URI/IRI, never a
literal. We coerce strings into ``URIRef`` for the predicate slot
even when they don't match the IRI heuristic (a CONSTRUCT template
with a constant predicate IRI binds it as a plain string at AQL
emit time — the IRI shape gets stripped of the ``http://`` prefix
when rdflib expands ``rdf:type`` etc., and we'd otherwise mis-classify
those as literals).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

from rdflib import BNode, Graph, Literal, URIRef

__all__ = [
    "render_construct",
    "MEDIA_TYPE_TO_RDF_FORMAT",
    "RDF_FORMAT_NAMES",
]


# Symbolic format names mirror the SELECT/ASK module's ``FORMAT_NAMES``
# so the route layer can log them uniformly ("format=ttl" alongside
# "format=json"). Kept as a tuple for the negotiation tests to assert
# against a stable shape.
RDF_FORMAT_NAMES: tuple[str, ...] = ("ttl", "nt", "rdfxml", "jsonld")


MEDIA_TYPE_TO_RDF_FORMAT: dict[str, str] = {
    "text/turtle": "ttl",
    "application/n-triples": "nt",
    "application/rdf+xml": "rdfxml",
    "application/ld+json": "jsonld",
}


# rdflib's serialiser names are not 1:1 with the IANA media types; this
# table maps our symbolic format names to the rdflib parameter so the
# rest of the module never has to think about it.
_RDFLIB_FORMAT: dict[str, str] = {
    "ttl": "turtle",
    "nt": "nt",
    "rdfxml": "xml",
    "jsonld": "json-ld",
}


# IRI shape — same regex as :mod:`.results`, kept in sync deliberately.
# Two implementations would drift; one canonical regex matches both
# wire-format families (SELECT/ASK and CONSTRUCT/DESCRIBE).
_IRI_SHAPE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+\-.]*://\S+$")

# Blank-node lexical prefix. SPARQL serialises blank nodes as ``_:<id>``;
# the visitor binds them as plain strings in this form so we can
# recognise them here and re-hydrate without external state.
_BNODE_PREFIX = "_:"


def _looks_like_iri(value: str) -> bool:
    """Return ``True`` when *value* matches the IRI heuristic."""

    return bool(_IRI_SHAPE_RE.match(value))


def _term_for_value(value: Any, *, predicate: bool = False) -> Any | None:
    """Rehydrate an AQL-row scalar into an rdflib term.

    Returns ``None`` if the value is itself ``None`` — the caller drops
    the whole triple in that case (a CONSTRUCT/DESCRIBE row with an
    unbound component is *not* a triple per RDF set semantics).

    ``predicate`` distinguishes the predicate slot (must yield a URIRef)
    from subject/object. RDF allows BNode subjects and Literal objects,
    but predicates are constrained to URIs — we coerce string
    predicates into ``URIRef`` even when they don't pass the IRI
    heuristic (e.g. a CONSTRUCT template with a constant predicate that
    rdflib expanded into a plain prefixed name).
    """

    if value is None:
        return None
    # Booleans before ints — ``isinstance(True, int)`` is True in Python.
    if isinstance(value, bool):
        return Literal(value)
    if isinstance(value, int):
        return Literal(value)
    if isinstance(value, float):
        return Literal(value)
    if isinstance(value, Mapping):
        # ArangoDB document — prefer ``_uri`` (RDF-style IRI baked by
        # the schema mapper) over ``_id`` (collection-relative key).
        # Both are IRI-shaped; we pick ``_uri`` because the mapper
        # synthesises it as the canonical RDF identity for the doc.
        candidate = value.get("_uri") or value.get("_id")
        if isinstance(candidate, str) and candidate:
            return URIRef(candidate)
        # Inline sub-document with no identity — serialise as JSON so
        # the operator sees *something* rather than the row being
        # silently dropped.
        return Literal(json.dumps(value, default=str, sort_keys=True))
    if isinstance(value, str):
        if value.startswith(_BNODE_PREFIX):
            return BNode(value[len(_BNODE_PREFIX) :] or None)
        if predicate or _looks_like_iri(value):
            return URIRef(value)
        return Literal(value)
    # Lists / arbitrary objects — JSON-encode so we don't crash on
    # structured AQL return shapes. Plain literal.
    return Literal(json.dumps(value, default=str, sort_keys=True))


def _flatten_rows(rows: Iterable[Any]) -> Iterable[Mapping[str, Any]]:
    """Walk the cursor output and yield each ``{s, p, o}`` triple dict.

    Both CONSTRUCT and DESCRIBE emit *lists* of triple dicts per row
    (CONSTRUCT because the template has N triples per binding;
    DESCRIBE because an attribute fan-out produces N rows per
    described document). We flatten transparently — a stray non-list
    row is treated as a singleton so a hand-rolled emitter that only
    ever produces one triple per row still works.
    """

    for row in rows:
        if row is None:
            continue
        if isinstance(row, Mapping):
            # Single triple in the row (atypical but valid).
            yield row
            continue
        if isinstance(row, list):
            for entry in row:
                if isinstance(entry, Mapping):
                    yield entry
            continue
        # APPEND() of empty sub-lists can produce ``[]`` rows which are
        # not Mappings or lists — skip silently.


def _rows_to_graph(rows: Iterable[Any]) -> Graph:
    """Materialise a :class:`rdflib.Graph` from the cursor rows.

    Triples with any unbound component (``None`` after rehydration)
    are dropped — a SPARQL CONSTRUCT template with a variable that
    failed to bind in a given row is allowed to "skip" that triple
    per the W3C spec, and DESCRIBE's attribute fan-out can also
    produce ``object: null`` rows for null-valued document fields.
    """

    g = Graph()
    for triple in _flatten_rows(rows):
        s = _term_for_value(triple.get("subject"))
        p = _term_for_value(triple.get("predicate"), predicate=True)
        o = _term_for_value(triple.get("object"))
        if s is None or p is None or o is None:
            continue
        # rdflib requires the predicate to be a URIRef; if our
        # heuristic produced a Literal we conservatively coerce it
        # rather than reject (the W3C wire formats would otherwise
        # bubble the type error all the way to the client).
        if not isinstance(p, URIRef):
            p = URIRef(str(p))
        g.add((s, p, o))
    return g


def render_construct(
    media_type: str,
    rows: Iterable[Any],
) -> str:
    """Serialise *rows* as the RDF wire format for *media_type*.

    Used for both CONSTRUCT and DESCRIBE — the visitor produces the
    same row shape for both (a list of ``{subject, predicate, object}``
    dicts per cursor row). rdflib handles the wire-format writing so
    we get Turtle/N-Triples/RDF-XML/JSON-LD without owning four
    serialisers ourselves.

    Raises :class:`ValueError` for an unknown media type — the route
    layer must always negotiate against the supported list before
    calling, so an unknown type here is a programming error.
    """

    fmt = MEDIA_TYPE_TO_RDF_FORMAT.get(media_type)
    if fmt is None:
        raise ValueError(
            f"unsupported media type for CONSTRUCT/DESCRIBE: {media_type!r}"
        )
    g = _rows_to_graph(rows)
    # JSON-LD wants ``json-ld`` format; rdflib's ``serialize`` returns
    # ``bytes`` by default — we coerce to ``str`` so the route's
    # PlainTextResponse can use it directly without binary handling.
    output = g.serialize(format=_RDFLIB_FORMAT[fmt])
    if isinstance(output, bytes):
        return output.decode("utf-8")
    return output
