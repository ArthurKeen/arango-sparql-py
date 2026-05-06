"""Models for the NL → SPARQL pipeline.

Two flavors of types live here:

* **Pydantic** request / response models (:class:`NlTranslateRequest`,
  :class:`NlTranslateResponse`, :class:`NlExplainRequest`,
  :class:`NlExplainResponse`, :class:`NlExecuteRequest`,
  :class:`NlExecuteResponse`) form the **frozen** API contract
  consumed by the FastAPI routes and the round-3 frontend. They are
  re-exported from :mod:`arango_sparql.service.models` so callers
  can import them via either path — both names resolve to the same
  class identity.
* **Dataclass-only** wire types (:class:`LLMResponse`,
  :class:`LLMCallRecord`, :class:`PipelineOutcome`) live exclusively
  here — they are pipeline internals, not surfaced over HTTP, and so
  do not need the Pydantic envelope cost.

The Pydantic models are *defined* on the service module (where the
``_MAX_*`` length constants live) to avoid a circular import; this
file re-exports them so the rule-300 expectation that
``arango_sparql.nl2sparql.models.NlTranslateRequest`` resolves keeps
holding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..service.models import (
    NlExecuteRequest,
    NlExecuteResponse,
    NlExplainRequest,
    NlExplainResponse,
    NlTranslateRequest,
    NlTranslateResponse,
)

__all__ = [
    "LLMCallRecord",
    "LLMResponse",
    "NlExecuteRequest",
    "NlExecuteResponse",
    "NlExplainRequest",
    "NlExplainResponse",
    "NlTranslateRequest",
    "NlTranslateResponse",
    "PipelineOutcome",
]


@dataclass
class LLMResponse:
    """Single LLM round-trip envelope.

    ``cached_tokens`` is the provider-side prefix-cache hit count
    surfaced by OpenAI (``prompt_tokens_details.cached_tokens``) and
    Anthropic (``cache_read_input_tokens``); ``0`` for providers that
    don't expose cache telemetry, never ``None`` so downstream
    arithmetic stays branch-free.
    """

    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0


@dataclass
class LLMCallRecord:
    """Audit trail for a single LLM round-trip inside a pipeline run.

    Every attempt — happy or repaired — appends one of these to
    :attr:`PipelineOutcome.llm_call_records`. Tests assert on the
    record count to verify the repair loop fired the expected number
    of times.
    """

    provider: str
    model: str
    sparql: str
    cost_usd: float
    latency_ms: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    error: str | None = None


@dataclass
class PipelineOutcome:
    """Result of a NL → SPARQL → AQL pipeline run.

    ``warnings`` carries advisories from both the LLM step (e.g.
    repair loop fired) and the deterministic translator (e.g.
    unmapped predicate IRI). ``llm_call_records`` is the per-attempt
    audit trail; ``cost_usd`` and ``llm_calls`` are convenience
    aggregates over those records.
    """

    nl: str
    sparql: str
    aql: str
    bind_vars: dict[str, Any] = field(default_factory=dict)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    llm_call_records: list[LLMCallRecord] = field(default_factory=list)
    latency_ms: int = 0
    repaired: bool = False
    explanation: str = ""

    @property
    def llm_calls(self) -> int:
        return len(self.llm_call_records)

    @property
    def cost_usd(self) -> float:
        return round(sum(r.cost_usd for r in self.llm_call_records), 6)
