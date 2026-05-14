"""Typed error hierarchy for ``arango-sparql-py``.

Every error carries a stable ``code`` string so the FastAPI layer can
surface it to clients without leaking internals. See
``.cursor/rules/100-backend-python.mdc`` §"Errors" for the contract.
"""

from __future__ import annotations


class SparqlError(Exception):
    """Base class for every domain error in ``arango-sparql-py``."""

    code: str = "E_SPARQL"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class SparqlParseError(SparqlError):
    """Raised when ``rdflib`` cannot parse the SPARQL query string."""

    code = "E_SPARQL_PARSE"


class UnsupportedSparqlError(SparqlError):
    """Raised when the visitor encounters an Algebra node we do not yet
    translate. Never silently degrade — we want callers to know."""

    code = "E_SPARQL_UNSUPPORTED"


class SchemaResolutionError(SparqlError):
    """Raised when a SPARQL IRI cannot be resolved against the loaded OWL
    ontology to a physical ArangoDB collection / property."""

    code = "E_SCHEMA_RESOLVE"


class AqlEmitError(SparqlError):
    """Raised when the AQL builder cannot emit a well-formed query
    (e.g. dangling alias, unbound projection variable)."""

    code = "E_AQL_EMIT"


class CrossTenantJoinError(SparqlError):
    """Raised when a single SPARQL query joins two entities scoped
    under different ``tenantEntity`` roots.

    A cross-tenant join would broadcast across customer boundaries —
    a privacy / compliance violation that PRD §6.5.1 (and the
    §8.6 T12 mitigation) explicitly forbids. The HTTP layer maps
    this to ``422 E_TRANSLATE_CROSS_TENANT_JOIN``.
    """

    code = "E_TRANSLATE_CROSS_TENANT_JOIN"
