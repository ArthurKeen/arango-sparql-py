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

:func:`nl_to_sparql` (returning :class:`NL2SparqlResult`) is the
library entry point backed by the SHARED engine
(``arango_query_core.nl`` + this repo's
:class:`~arango_sparql.nl2sparql.adapter.SparqlLanguageAdapter` — the
five language seams). :class:`NlPipeline`, which the service's ``/nl-*``
routes run, drives the same engine, adding per-call audit and cost
records through :class:`~arango_sparql.nl2sparql.engine_adapter.EngineProviderBridge`.
The contextual-data-fabric does **not** call either yet: its NL front-end
uses only this package's model client (:mod:`.client`) with a loop of its
own, and moving it onto the shared engine is tracked in
arango-solutions-org/contextual-data-fabric#176. The adapter is imported
lazily (``arango_sparql.nl2sparql.adapter``) so this package stays
import-safe without the ``nl`` extra installed.

Caller-supplied **postconditions** — semantic invariants enforced through the
shared engine's retry loop (see :mod:`arango_query_core.nl.postconditions`) —
are passed to :func:`nl_to_sparql` via its ``postconditions=`` parameter. Two
illustrative SPARQL invariants ship in
:mod:`arango_sparql.nl2sparql.postconditions`
(:class:`~arango_sparql.nl2sparql.postconditions.RequireResultLimit`,
:class:`~arango_sparql.nl2sparql.postconditions.ForbidUnboundProjection`). That
submodule is imported explicitly (not re-exported here) so this package stays
import-safe without the ``nl`` extra — same discipline as the adapter.

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
