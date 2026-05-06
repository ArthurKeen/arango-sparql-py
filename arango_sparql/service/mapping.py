"""HTTP-shaped mapping helper for ``arango_sparql.service``.

Mirror of ``arango_cypher.service.mapping`` — single-function module
that adapts the wire JSON / inline Turtle payload on a translate /
execute / validate request into the in-memory
:class:`~arango_sparql.translate.resolver.SchemaResolver` the visitor
consumes.

Two payload shapes are recognised, in order of precedence:

1. ``req.ontology_ttl`` — a Turtle string carrying the OWL ontology
   produced by ``arango-schema-mapper``. Parsed into a fresh
   :class:`rdflib.Graph` and wrapped in a
   :class:`~arango_sparql.translate.resolver.SchemaResolver`. This is
   the path the ``/translate`` and ``/execute`` endpoints take today
   because it lets the UI ship a self-contained request without
   depending on a server-side cache.

2. ``req.mapping`` — a JSON dict. Today only the ``{"ttl": "<turtle>"}``
   key is honoured (which lets a JSON-only client tunnel a Turtle blob
   through the same field the Cypher project uses for its dict
   mapping). When the SPARQL service grows a richer wire shape — e.g.
   ``{"classes": [...], "properties": [...]}`` for a fully JSON-native
   mapping — extend :func:`_mapping_from_dict` here rather than at
   every call site.

Returns an *empty* resolver (graph with no triples) when neither
field is set. The resolver's unmapped-property fallback degrades any
bare URI to its local-name attribute, so simple SELECT queries
against an unmapped collection still work — see
``arango_sparql.translate.resolver.SchemaResolver.resolve_property``
for the contract. Callers that require a populated ontology should
validate the request before calling this helper.
"""

from __future__ import annotations

from typing import Any

from rdflib import Graph

from ..translate.resolver import SchemaResolver


def _mapping_from_dict(d: dict[str, Any] | None) -> SchemaResolver | None:
    """Adapt a JSON mapping payload to a :class:`SchemaResolver`.

    Returns ``None`` when ``d`` is ``None`` or empty so the caller can
    short-circuit before touching the graph builder. Today the only
    recognised key is ``"ttl"`` — a Turtle blob tunnelled through a
    JSON envelope; future shapes get wired in here without changing
    the route signatures.
    """
    if not d:
        return None
    ttl = d.get("ttl")
    if isinstance(ttl, str) and ttl.strip():
        return SchemaResolver.from_turtle(ttl)
    return None


def _resolver_from_request(req: Any) -> SchemaResolver:
    """Build a :class:`SchemaResolver` from the request envelope.

    Accepts any request model carrying an ``ontology_ttl`` (str | None)
    or ``mapping`` (dict | None) attribute — the ``/translate``,
    ``/execute`` and ``/validate`` request models all do. Inline
    Turtle wins over a JSON ``mapping`` payload when both are
    present so the UI's "edit ontology" affordance can override a
    cached mapping for one-off queries without first having to mutate
    the cache.

    Falls back to an empty :class:`rdflib.Graph` resolver (which lets
    bare-URI predicates degrade to local-name attributes) when neither
    field is supplied, matching the legacy behaviour of the original
    ``_resolver_from_ttl`` helper that this function replaces.
    """
    ttl = getattr(req, "ontology_ttl", None)
    if isinstance(ttl, str) and ttl.strip():
        return SchemaResolver.from_turtle(ttl)
    mapping = getattr(req, "mapping", None)
    if isinstance(mapping, dict) and mapping:
        resolver = _mapping_from_dict(mapping)
        if resolver is not None:
            return resolver
    return SchemaResolver(ontology=Graph())
