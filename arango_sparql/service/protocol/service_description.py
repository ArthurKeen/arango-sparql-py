"""W3C SPARQL 1.1 Service Description renderer (PRD §5.2).

Returned by ``GET /sparql`` when the client omits a ``query`` parameter
— browsers / SPARQL editors / federated-query planners use this to
discover the endpoint's capabilities before sending real queries.

We render the description directly as Turtle text (rather than
building an ``rdflib.Graph`` and serialising) for two reasons:

1. The structure is fixed and small; building a graph is overkill.
2. Variable-order in the rdflib Turtle serialiser is not stable
   across runs, which would defeat etag-based caching at any
   reverse proxy in front of the service.

The supported-features set, default graph URI, and result-format
list are sourced from PRD §5.2 — *not* from runtime configuration
— because they are properties of the *implementation*, not of the
deployment. A v1.1 release that adds CONSTRUCT support will update
this module's :data:`_SUPPORTED_RESULT_FORMATS` constant in lockstep
with the visitor.
"""

from __future__ import annotations

from collections.abc import Iterable

from ...translate.mapping import MappingBundle
from .negotiate import CONSTRUCT_PRIORITY, SELECT_PRIORITY

__all__ = [
    "render_service_description",
    "SD_NAMESPACE",
]


# Vocabulary IRIs — kept module-level so they're visible as
# constants in the generated Turtle and so tests can reference them
# without re-typing the namespace prefix.
SD_NAMESPACE = "http://www.w3.org/ns/sparql-service-description#"
FORMAT_NAMESPACE = "http://www.w3.org/ns/formats/"


# Mapping from the wire media type to the W3C ``formats`` namespace
# IRI used inside the Service Description. Keep in sync with
# :mod:`negotiate` — entries that don't appear in
# :data:`negotiate.SELECT_PRIORITY` or :data:`CONSTRUCT_PRIORITY`
# are silently omitted.
_FORMAT_IRIS: dict[str, str] = {
    "application/sparql-results+json": FORMAT_NAMESPACE + "SPARQL_Results_JSON",
    "application/sparql-results+xml": FORMAT_NAMESPACE + "SPARQL_Results_XML",
    "text/csv": FORMAT_NAMESPACE + "SPARQL_Results_CSV",
    "text/tab-separated-values": FORMAT_NAMESPACE + "SPARQL_Results_TSV",
    "text/turtle": FORMAT_NAMESPACE + "Turtle",
    "application/n-triples": FORMAT_NAMESPACE + "N-Triples",
    "application/rdf+xml": FORMAT_NAMESPACE + "RDF_XML",
    "application/ld+json": FORMAT_NAMESPACE + "JSON-LD",
}


# Per PRD §5.2:
# - ``sd:UnionDefaultGraph`` — the default graph is the union of
#   every named graph (matches the customary ArangoDB view of
#   "all collections at once").
# - ``sd:DereferencesURIs`` — IRIs that resolve to documents in the
#   bound database can be dereferenced to their content. Honest:
#   the resolver does this for every IRI mapped to an entity.
# - ``sd:BasicFederatedQuery`` is *not* listed — federation
#   (``SERVICE``) is out of scope per §5.3.
_SUPPORTED_FEATURES: tuple[str, ...] = (
    SD_NAMESPACE + "UnionDefaultGraph",
    SD_NAMESPACE + "DereferencesURIs",
)


# Default graph IRI when the bundle doesn't carry a configured one.
# ``urn:`` keeps the IRI dereferenceable-without-network and matches
# the legacy ``arango-sparql`` Foxx service's choice.
_DEFAULT_GRAPH_IRI = "urn:arango-sparql:default-graph"

# A documentation URL the client can fetch to read the human-
# readable contract for what's supported. PRD §5.2 mandates a
# ``dcterms:description`` link — we point at the public PRD copy
# in the GitHub repo so any spec-compliant client (Protégé's
# "endpoint info" panel, YASGUI's "explore" tab) can deep-link to
# it.
_PRD_LINK = (
    "https://github.com/ArthurKeen/arango-sparql-py/blob/main/"
    "docs/architecture/PRD.md#52-w3c-sparql-11-protocol-endpoint"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _named_graph_iris_from_bundle(bundle: MappingBundle | None) -> list[str]:
    """Return the list of named-graph IRIs to surface as
    ``sd:availableGraphs``.

    PRD §5.2 says one named graph per declared ``phys:collectionName``;
    we extend this with edge-collection names so federated planners
    that follow ``sd:availableGraphs`` to plan ``GRAPH`` queries see
    every store the visitor can FROM/INTO.

    Returns a deduplicated, sorted list. Sorting matters — the
    Turtle output is etag-friendly when the entry order is
    deterministic.
    """

    if bundle is None:
        return []

    names: set[str] = set()
    for spec in bundle.entities().values():
        if not isinstance(spec, dict):
            continue
        cn = spec.get("collectionName")
        if isinstance(cn, str) and cn:
            names.add(cn)
        # RPT entities live in a triples collection.
        tc = spec.get("triplesCollection")
        if isinstance(tc, str) and tc:
            names.add(tc)
    for spec in bundle.relationships().values():
        if not isinstance(spec, dict):
            continue
        ecn = spec.get("edgeCollectionName")
        if isinstance(ecn, str) and ecn:
            names.add(ecn)

    # Map collection names to the canonical graph IRI shape
    # (``urn:arango-sparql:graph:<collection>``). The Foxx service
    # used the same pattern; keeping it consistent means existing
    # client integrations don't need to relearn the URI scheme.
    return sorted(f"urn:arango-sparql:graph:{n}" for n in names)


def _format_iris_for_select() -> list[str]:
    return [_FORMAT_IRIS[m] for m in SELECT_PRIORITY if m in _FORMAT_IRIS]


def _format_iris_for_construct() -> list[str]:
    return [_FORMAT_IRIS[m] for m in CONSTRUCT_PRIORITY if m in _FORMAT_IRIS]


def _format_iri_block(label: str, iris: Iterable[str], indent: str) -> str:
    """Render ``label sd:resultFormat <iri1>, <iri2> ;`` with each
    IRI on its own line for human readability.
    """

    iri_list = list(iris)
    if not iri_list:
        return ""
    rendered = " ,\n".join(f"{indent}    <{iri}>" for iri in iri_list)
    return f"{indent}{label}\n{rendered} ;\n"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def render_service_description(
    *,
    endpoint_url: str,
    bundle: MappingBundle | None = None,
) -> str:
    """Return the Turtle Service Description for *endpoint_url*.

    *bundle* is consulted for the ``sd:availableGraphs`` set; pass
    ``None`` (or a bundle without entity mappings) to advertise only
    the default graph, which is the right behaviour when the
    schema layer hasn't acquired a mapping yet.

    The endpoint URL is the absolute URL of the ``/sparql`` route as
    seen by the client (taken from the request's ``url_for`` /
    ``base_url`` at the route layer). Including it lets the
    description be self-describing — clients can re-issue the URL
    without parsing it from the request context.
    """

    named_graphs = _named_graph_iris_from_bundle(bundle)
    select_formats = _format_iris_for_select()
    construct_formats = _format_iris_for_construct()

    parts: list[str] = []
    parts.append("@prefix sd: <" + SD_NAMESPACE + "> .\n")
    parts.append('@prefix dcterms: <http://purl.org/dc/terms/> .\n')
    parts.append("\n")

    parts.append("<> a sd:Service ;\n")
    parts.append(f'    sd:endpoint <{endpoint_url}> ;\n')

    # Supported languages — only SPARQL 1.1 Query for v1.x. Update
    # is explicitly *not* listed so spec-compliant clients won't
    # try to send Update requests they'd just get 405 for.
    parts.append("    sd:supportedLanguage sd:SPARQL11Query ;\n")

    # Supported features set — see _SUPPORTED_FEATURES.
    feat_iris = " ,\n".join(f"        <{iri}>" for iri in _SUPPORTED_FEATURES)
    parts.append("    sd:feature\n")
    parts.append(feat_iris + " ;\n")

    # Result-format declarations. We emit both SELECT/ASK and
    # CONSTRUCT/DESCRIBE format IRIs even though CONSTRUCT isn't
    # implemented yet — the wire negotiation path supports them
    # already, and advertising them now keeps clients from
    # second-guessing the endpoint's capabilities once the
    # CONSTRUCT visitor lands.
    parts.append(_format_iri_block("sd:resultFormat", select_formats, "    "))
    parts.append(_format_iri_block("sd:resultFormat", construct_formats, "    "))

    # Default graph + named-graph entries. The Service Description
    # vocabulary nests these under ``sd:defaultDataset``.
    parts.append("    sd:defaultDataset [\n")
    parts.append("        a sd:Dataset ;\n")
    parts.append("        sd:defaultGraph [\n")
    parts.append("            a sd:Graph ;\n")
    parts.append(f'            dcterms:identifier <{_DEFAULT_GRAPH_IRI}>\n')
    parts.append("        ]")
    if named_graphs:
        # Repeating ``sd:namedGraph`` blank-node objects: use ``;``
        # to repeat the predicate (Turtle predicate-list separator),
        # *not* ``,`` (which would only repeat the *object* without
        # a fresh predicate token and produce ``[…] , [ a … ]``,
        # which is grammatically invalid).
        for iri in named_graphs:
            parts.append(" ;\n")
            parts.append(
                "        sd:namedGraph [\n"
                "            a sd:NamedGraph ;\n"
                f"            sd:name <{iri}>\n"
                "        ]"
            )
    parts.append("\n    ] ;\n")

    # PRD link as a dcterms:description so spec-compliant clients
    # can deep-link to the human-readable contract.
    parts.append(f'    dcterms:description <{_PRD_LINK}> .\n')

    return "".join(parts)
