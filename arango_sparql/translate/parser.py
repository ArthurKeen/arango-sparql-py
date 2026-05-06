"""Thin wrapper over :mod:`rdflib.plugins.sparql` for SPARQL 1.1 parsing.

The rest of the codebase MUST use this module rather than touching
``rdflib`` directly so that:

1. Parser errors are converted to :class:`~arango_sparql.errors.SparqlParseError`
   with stable error codes for the HTTP layer.
2. The Algebra translation step (``translateQuery``) is always applied,
   so visitors see the optimized algebra rather than the raw parse tree.
3. The original projection-variable declaration order is preserved.
   ``rdflib.algebra.translateQuery`` collapses ``Project.PV`` into a
   set-iteration order that is **non-deterministic** across Python runs
   (set hash randomization). We capture the explicit projection list
   from the parsed query before that information is lost.

See ``.cursor/skills/sparql-to-aql/SKILL.md`` step 1 for the algebra
inspection workflow.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rdflib import Variable
from rdflib.plugins.sparql.algebra import translateQuery
from rdflib.plugins.sparql.parser import parseQuery

from ..errors import SparqlParseError


@dataclass
class ParsedSparql:
    """Result of :func:`parse_sparql`.

    ``algebra`` is the rdflib Algebra root node the visitor walks.
    ``explicit_projection`` is the declared projection variable list
    *iff* the query used an explicit ``SELECT ?a ?b`` form; ``None``
    when the query used ``SELECT *`` (in which case the visitor falls
    back to its own deterministic variable-binding order).
    """

    algebra: Any
    explicit_projection: list[Variable] | None = None


def parse_sparql(query: str) -> ParsedSparql:
    """Parse *query* and return the rdflib Algebra plus declared projection.

    Raises
    ------
    SparqlParseError
        If ``rdflib`` cannot parse the input string.
    """
    if not isinstance(query, str) or not query.strip():
        raise SparqlParseError("SPARQL query must be a non-empty string")
    try:
        parsed = parseQuery(query)
        translated = translateQuery(parsed)
    except Exception as exc:
        raise SparqlParseError(f"failed to parse SPARQL: {exc}") from exc
    explicit = _extract_explicit_projection(parsed)
    return ParsedSparql(algebra=translated.algebra, explicit_projection=explicit)


def _extract_explicit_projection(parsed: Any) -> list[Variable] | None:
    """Return the declared projection list, or ``None`` for ``SELECT *``.

    rdflib's ``parseQuery`` returns ``(prologue, query)``. For an
    explicit projection (``SELECT ?a ?b``) the query carries a
    ``projection`` key whose value is a list of ``vars`` records — each
    with a ``var`` slot pointing at the actual ``Variable``. ``SELECT *``
    omits the key entirely.
    """
    try:
        _, query = parsed
    except (ValueError, TypeError):
        return None
    if getattr(query, "name", None) != "SelectQuery":
        return None
    projection = query.get("projection")
    if not projection:
        return None
    out: list[Variable] = []
    for entry in projection:
        if not hasattr(entry, "get"):
            continue
        # Plain ``?x`` projection entries set ``var``; aliased
        # projections (``(<expr> AS ?x)``, including aggregates and
        # ``BIND``-shaped projections inside SELECT) set ``evar``.
        # Both produce the same downstream binding semantics — pick
        # whichever the entry carries.
        #
        # Subtlety: rdflib's ``CompValue.get`` returns the *key name*
        # as the default when the key is absent (instead of ``None``),
        # so we must verify the result is actually a ``Variable``
        # before treating it as one.
        var = entry.get("var")
        if not isinstance(var, Variable):
            var = entry.get("evar")
        if isinstance(var, Variable):
            out.append(var)
    return out or None
