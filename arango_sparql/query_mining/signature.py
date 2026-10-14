"""Compare what two queries returned: the "same results" pass rule.

Ported from arango-cypher-py's ``arango_cypher/query_mining/signature.py``,
with one change of substance, because a SPARQL result is not an AQL result.

The saved AQL query is reduced exactly as in cypher (:func:`signature_of`):
the sets of vertex and edge ``_id``\\ s found anywhere in its rows (inside
paths, nested objects and arrays), an edge's ``_from`` / ``_to`` counting as
vertices it touches, plus — for rows that carry no documents, such as counts —
the multiset of scalar rows.

A SPARQL query returns **bindings**, and a resource in a binding is not a
document but its *identity*: ``_uri`` when the document has one, else ``_id``
(``translate/builder.py::subject_ref`` — the translator's own rule). So the
source's vertices are first mapped to the identities SPARQL would bind for them
(:func:`resolve_identities`, one ``DOCUMENT()`` lookup through that same
expression), giving a :class:`Reference`; the SPARQL rows are then read against
it (:func:`bindings_signature`). SPARQL binds no edge documents, so edges count
only through their endpoints.

Rows that hold no resources are compared by their **values**, not their
column names — the saved AQL's ``{name: …}`` and the draft's ``?n`` name the
same column differently — as an order-free multiset per row.

Verdicts (cypher's names, so stored examples read alike in both repos)
--------------------------------------------------------------------------
``identical``
    Same resources, and the saved query returned no edges.
``same_vertices``
    Same resources; the saved query also returned edges, which SPARQL cannot.
``same_values``
    Neither side returned resources and the value rows match (order-free,
    floats compared to :data:`FLOAT_DIGITS` places).
``different``
    Anything else; the detail says what differs.

Only the first three pass.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ..translate.builder import subject_ref

FLOAT_DIGITS = 6
PASSING = frozenset({"identical", "same_vertices", "same_values"})

#: Bounds the identity lookup like every other mining query.
_IDENTITY_LOOKUP_RUNTIME_S = 30.0

# An absolute IRI — what ``_uri`` holds — has a scheme and no whitespace.
_IRI = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:\S+$")


@dataclass(frozen=True)
class ResultSignature:
    """What a saved AQL query returned (cypher's signature, unchanged)."""

    vertices: frozenset[str] = frozenset()
    edges: frozenset[str] = frozenset()
    #: Canonical JSON of rows that hold no documents (counts, projections).
    scalars: Counter[str] = field(default_factory=Counter)
    rows: int = 0

    @property
    def has_documents(self) -> bool:
        return bool(self.vertices or self.edges)

    def describe(self) -> str:
        return f"{self.rows} rows, {len(self.vertices)} vertices, {len(self.edges)} edges"


def _is_document(value: dict[str, Any]) -> bool:
    return isinstance(value.get("_id"), str) and "/" in value["_id"]


def _canonical(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, FLOAT_DIGITS)
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in sorted(value.items()) if not k.startswith("_")}
    if isinstance(value, list):
        return [_canonical(v) for v in value]
    return value


def _row_key(row: Any) -> str:
    """A value row as an order-free, name-free multiset (see module docstring)."""
    if isinstance(row, dict):
        values = [v for k, v in row.items() if not k.startswith("_")]
    elif isinstance(row, list):
        values = list(row)
    else:
        values = [row]
    return json.dumps(sorted(json.dumps(_canonical(v), sort_keys=True, default=str) for v in values))


def signature_of(rows: list[Any]) -> ResultSignature:
    """Reduce a saved AQL query's rows (cypher's rule)."""
    vertices: set[str] = set()
    edges: set[str] = set()
    scalars: Counter[str] = Counter()

    def walk(value: Any) -> bool:
        """Collect documents under *value*; True if any was found."""
        if isinstance(value, dict):
            if _is_document(value):
                if isinstance(value.get("_from"), str) and isinstance(value.get("_to"), str):
                    edges.add(value["_id"])
                    vertices.update((value["_from"], value["_to"]))
                else:
                    vertices.add(value["_id"])
                return True
            found = False
            for v in value.values():
                found = walk(v) or found
            return found
        if isinstance(value, list):
            found = False
            for v in value:
                found = walk(v) or found
            return found
        return False

    for row in rows:
        if not walk(row):
            scalars[_row_key(row)] += 1
    return ResultSignature(frozenset(vertices), frozenset(edges), scalars, len(rows))


def resolve_identities(db: Any, ids: Iterable[str]) -> dict[str, str]:
    """``_id`` → the identity a SPARQL result binds for that document.

    One read-only lookup through :func:`~arango_sparql.translate.builder.subject_ref`,
    the expression the translator itself projects, so the two cannot disagree.
    An id whose document is gone keeps its ``_id``.
    """
    wanted = sorted(set(ids))
    if not wanted:
        return {}
    query = f"FOR id IN @ids LET d = DOCUMENT(id) RETURN [id, d == null ? id : {subject_ref('d')}]"
    cursor = db.aql.execute(query, bind_vars={"ids": wanted}, max_runtime=_IDENTITY_LOOKUP_RUNTIME_S)
    return {str(pair[0]): str(pair[1]) for pair in cursor}


@dataclass(frozen=True)
class Reference:
    """What the saved query returned, in the terms a SPARQL result uses."""

    identities: frozenset[str] = frozenset()
    #: The saved query returned edges (SPARQL can match only their endpoints).
    had_edges: bool = False
    scalars: Counter[str] = field(default_factory=Counter)
    rows: int = 0

    @property
    def has_resources(self) -> bool:
        return bool(self.identities)

    def describe(self) -> str:
        return f"{self.rows} rows, {len(self.identities)} resources"


def reference_of(source: ResultSignature, identity_of: Mapping[str, str]) -> Reference:
    """*source* with each vertex replaced by its SPARQL identity."""
    identities = frozenset(identity_of.get(v, v) for v in source.vertices)
    return Reference(identities, bool(source.edges), source.scalars, source.rows)


def bindings_signature(rows: list[Any], *, reference: Reference, collections: frozenset[str]) -> Reference:
    """Reduce a SPARQL draft's binding rows against *reference*.

    A string is read as a resource when the saved query returned resources and
    the string is one of them, an ``_id`` of an existing collection, or an
    absolute IRI. When the saved query returned no resources, nothing is read
    as one — a URL-valued property is then just a value, as in the source.
    """

    def is_resource(value: str) -> bool:
        if value in reference.identities or _IRI.match(value):
            return True
        head, sep, tail = value.partition("/")
        return bool(sep and tail and head in collections)

    identities: set[str] = set()
    scalars: Counter[str] = Counter()

    def walk(value: Any) -> bool:
        if isinstance(value, str):
            if is_resource(value):
                identities.add(value)
                return True
            return False
        if isinstance(value, dict):
            found = False
            for k, v in value.items():
                if not k.startswith("_"):
                    found = walk(v) or found
            return found
        if isinstance(value, list):
            found = False
            for v in value:
                found = walk(v) or found
            return found
        return False

    for row in rows:
        if not (reference.has_resources and walk(row)):
            scalars[_row_key(row)] += 1
    return Reference(frozenset(identities), False, scalars, len(rows))


@dataclass(frozen=True)
class Verdict:
    kind: str
    detail: str = ""

    @property
    def passed(self) -> bool:
        return self.kind in PASSING


def compare(reference: Reference, candidate: Reference) -> Verdict:
    """Did the SPARQL draft (*candidate*) return what the saved query did?"""
    if not reference.has_resources and not candidate.has_resources:
        if reference.scalars == candidate.scalars:
            return Verdict("same_values")
        return Verdict(
            "different", f"values differ: saved query {reference.describe()}, yours {candidate.describe()}"
        )
    if reference.identities != candidate.identities:
        missing = len(reference.identities - candidate.identities)
        extra = len(candidate.identities - reference.identities)
        return Verdict("different", f"resources differ: {missing} missing, {extra} extra")
    return Verdict("same_vertices" if reference.had_edges else "identical")
