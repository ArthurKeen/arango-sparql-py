"""NL → SPARQL pipeline core.

Mirrors :class:`arango_cypher.nl2cypher.NL2CypherResult` field-for-field
where the semantics carry over. Implementation is intentionally a
stub — fill in once the deterministic translator is past BGP/OPTIONAL.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

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


def nl_to_sparql(question: str, *, ontology_ttl: str | None = None) -> NL2SparqlResult:
    """Translate a natural language question into a SPARQL 1.1 query.

    TODO: port the LLM provider abstraction, BM25 fewshot index, and
    tenant guardrail from :mod:`arango_cypher.nl2cypher`. Today this
    just echoes the question as a comment so the route shape compiles.
    """
    return NL2SparqlResult(
        sparql=f"# nl_to_sparql not implemented yet — question was: {question!r}",
        method="rule_based",
    )
