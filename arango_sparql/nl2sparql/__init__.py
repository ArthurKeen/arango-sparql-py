"""NL → SPARQL pipeline.

Mirrors :mod:`arango_cypher.nl2cypher` for cross-repo telemetry and
tooling. Public surface:

* :class:`NlPipeline` — end-to-end orchestrator.
* :class:`PromptBuilder` — system / user message renderer; deterministic
  for prefix-cache stability.
* :class:`LLMClient` — protocol every backend (OpenAI, Anthropic,
  scripted test double) implements.
* :class:`OpenAICompatibleClient`, :class:`AnthropicClient` — shipping
  HTTP backends.
* :class:`ScriptedLLMClient` — test double exposing the protocol.
* :func:`get_default_client` — env-driven factory.
* :func:`estimate_llm_cost_usd` — pricing arithmetic.
* :class:`RepairLoop` — bounded translator-rejection retry surface.
* :class:`PipelineOutcome`, :class:`LLMCallRecord`, :class:`LLMResponse` —
  result envelopes.

The legacy stub :func:`nl_to_sparql` returning :class:`NL2SparqlResult`
is kept on the surface for backward compatibility with the bootstrap
tests; new callers should use :class:`NlPipeline` directly.

See ``.cursor/rules/300-nl2sparql.mdc``.
"""

from __future__ import annotations

from ._core import NL2SparqlResult, nl_to_sparql
from .client import (
    AnthropicClient,
    LLMClient,
    OpenAICompatibleClient,
    ScriptedLLMClient,
    get_default_client,
)
from .cost import estimate_llm_cost_usd, known_pricing
from .models import (
    LLMCallRecord,
    LLMResponse,
    NlExecuteRequest,
    NlExecuteResponse,
    NlExplainRequest,
    NlExplainResponse,
    NlTranslateRequest,
    NlTranslateResponse,
    PipelineOutcome,
)
from .pipeline import NlPipeline
from .prompt import PromptBuilder, build_explain_messages, extract_sparql_from_response
from .repair import RepairLoop, RepairOutcome, format_repair_context
from .samples import suggest_nl_queries

__all__ = [
    "AnthropicClient",
    "LLMCallRecord",
    "LLMClient",
    "LLMResponse",
    "NL2SparqlResult",
    "NlExecuteRequest",
    "NlExecuteResponse",
    "NlExplainRequest",
    "NlExplainResponse",
    "NlPipeline",
    "NlTranslateRequest",
    "NlTranslateResponse",
    "OpenAICompatibleClient",
    "PipelineOutcome",
    "PromptBuilder",
    "RepairLoop",
    "RepairOutcome",
    "ScriptedLLMClient",
    "build_explain_messages",
    "estimate_llm_cost_usd",
    "extract_sparql_from_response",
    "format_repair_context",
    "get_default_client",
    "known_pricing",
    "nl_to_sparql",
    "suggest_nl_queries",
]
