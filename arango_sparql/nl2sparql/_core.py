"""NL → SPARQL library entry point, backed by the shared engine.

``nl_to_sparql`` composes the ``arango_query_core.nl`` engine with this
repo's :class:`~arango_sparql.nl2sparql.adapter.SparqlLanguageAdapter`
(the five language seams — grammar prompt, corpus, transpiler-backed
validation, repair rules, guardrails). This replaces the original
bootstrap stub and is the surface the contextual-data-fabric's NL
front-end (M5 WP-D1) consumes as a library.

The FastAPI ``/nl`` routes still run :class:`~arango_sparql.nl2sparql.pipeline.NlPipeline`,
which carries richer per-call telemetry (``LLMCallRecord`` list, cost
envelope) than the engine's aggregate counters; folding NlPipeline onto
the engine follows once the cypher-side re-point settles the seam API
(nl-engine-extraction proposal, steps 2/3).

:class:`NL2SparqlResult` mirrors ``arango_cypher.nl2cypher.NL2CypherResult``
field-for-field where the semantics carry over.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..translate.resolver import SchemaResolver

logger = logging.getLogger(__name__)


@dataclass
class NL2SparqlResult:
    """Result of a natural language → SPARQL translation."""

    sparql: str
    explanation: str = ""
    confidence: float = 0.0
    method: str = "rule_based"
    schema_context: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    retries: int = 0


def nl_to_sparql(
    question: str,
    *,
    ontology_ttl: str | None = None,
    resolver: SchemaResolver | None = None,
    provider: Any | None = None,
    tenant_id: str | None = None,
    max_retries: int = 2,
) -> NL2SparqlResult:
    """Translate a natural-language question into a SPARQL 1.1 query.

    Parameters
    ----------
    question:
        The natural-language question.
    ontology_ttl:
        OWL ontology (Turtle) describing the conceptual schema — both
        the LLM's system-prompt context and, when *resolver* is not
        given, the source of the validation resolver.
    resolver:
        An explicit :class:`~arango_sparql.translate.resolver.SchemaResolver`;
        overrides the one derived from *ontology_ttl*. A candidate
        query counts as valid only when it parses AND translates
        against this resolver.
    provider:
        An ``arango_query_core.nl.LLMProvider``. Defaults to
        environment resolution (``LLM_PROVIDER`` /
        ``OPENAI_API_KEY`` / ``OPENROUTER_API_KEY`` /
        ``ANTHROPIC_API_KEY``). With no provider available the result
        has ``method="unavailable"`` and an explanatory ``explanation``
        — never a silent empty query.
    tenant_id:
        Forwarded to validation-time ``translate()`` so tenant-scoped
        mappings reject cross-tenant queries during repair, not at
        execution.
    max_retries:
        Validate→repair round-trips after the first attempt.
    """
    from arango_query_core.nl import NLQueryEngine, get_llm_provider

    from ..translate.resolver import SchemaResolver
    from .adapter import SparqlLanguageAdapter

    ttl = ontology_ttl or ""
    if resolver is None:
        resolver = SchemaResolver.from_turtle(ttl, permissive_class_resolution=True)
    llm = provider or get_llm_provider()
    if llm is None:
        logger.info("nl_to_sparql: no LLM provider configured; returning method='unavailable'")
        return NL2SparqlResult(
            sparql="",
            method="unavailable",
            explanation=(
                "No LLM provider is configured. Set LLM_PROVIDER / "
                "OPENAI_API_KEY / OPENROUTER_API_KEY / ANTHROPIC_API_KEY, "
                "or pass provider= explicitly."
            ),
            schema_context=ttl,
        )

    adapter = SparqlLanguageAdapter(resolver=resolver, ontology_ttl=ttl, tenant_id=tenant_id)
    engine = NLQueryEngine(provider=llm, adapter=adapter, max_retries=max_retries)
    outcome = engine.generate(question, schema_context=ttl)

    return NL2SparqlResult(
        sparql=outcome.query,
        explanation="" if outcome.ok else outcome.error,
        # Crude but honest: clean first-shot validations score higher
        # than repaired ones; failures score zero.
        confidence=0.0 if not outcome.ok else max(0.3, 0.9 - 0.2 * outcome.retries),
        method="llm" if outcome.ok else "llm_failed",
        schema_context=ttl,
        prompt_tokens=outcome.prompt_tokens,
        completion_tokens=outcome.completion_tokens,
        total_tokens=outcome.total_tokens,
        cached_tokens=outcome.cached_tokens,
        retries=outcome.retries,
    )
