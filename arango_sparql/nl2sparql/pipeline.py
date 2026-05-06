"""End-to-end NL → SPARQL → AQL orchestration.

Wires :class:`~arango_sparql.nl2sparql.prompt.PromptBuilder` →
:class:`~arango_sparql.nl2sparql.client.LLMClient` →
:class:`~arango_sparql.nl2sparql.repair.RepairLoop` →
:func:`arango_sparql.api.translate` into a single ``run()`` call that
the FastAPI route layer (``/nl-translate``, ``/nl-execute``) and the
``/nl-explain`` second-pass call consume.

Pipeline contract:

1. Build the prompt from the inbound NL question + ontology Turtle.
2. Call the LLM once to get a candidate SPARQL string.
3. Translate that SPARQL through the deterministic transpiler.
4. If translation raises :class:`~arango_sparql.errors.SparqlError`,
   feed the error back to the LLM and retry — up to ``max_repairs``
   times.
5. Return a :class:`~arango_sparql.nl2sparql.models.PipelineOutcome`
   with the final SPARQL, AQL, bind vars, warnings, per-call
   ``LLMCallRecord`` audit trail, total wall-clock latency, and a
   ``repaired`` flag.

The pipeline never raises on translation failure — it returns the
outcome with empty AQL, ``repaired=True``, and a warning entry whose
``code`` is the underlying error code. The route layer surfaces a
422 in that case; the model surface mirrors the success path so a
client renderer doesn't need a second branch.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from ..api import TranslateResult
from ..api import translate as _translate
from ..errors import SparqlError
from ..translate.resolver import SchemaResolver
from .client import LLMClient
from .cost import estimate_llm_cost_usd
from .models import LLMCallRecord, LLMResponse, PipelineOutcome
from .prompt import PromptBuilder, build_explain_messages, extract_sparql_from_response
from .repair import RepairLoop

logger = logging.getLogger(__name__)


@dataclass
class _SingleCallOutcome:
    """Internal envelope for one LLM round-trip (carried into the audit trail)."""

    sparql: str
    record: LLMCallRecord
    raw_content: str


class NlPipeline:
    """End-to-end orchestrator for the NL → SPARQL → AQL flow.

    Construct once per request (the LLM client is typically a process-
    wide singleton, the resolver is per-request because the ontology
    can vary per call). Call :meth:`run` for ``/nl-translate`` and
    :meth:`explain` for ``/nl-explain``.

    Multitenancy / few-shot / entity-resolution hooks listed in rule
    300 are deliberately deferred — they will land as separate
    submodules (``tenant_guardrail.py``, ``fewshot.py``,
    ``entity_resolution.py``) once the deterministic translator is far
    enough along to validate the LLM's output against a real schema.
    The pipeline shape exposed here will absorb those hooks via
    constructor args without breaking the API contract.
    """

    def __init__(
        self,
        *,
        client: LLMClient,
        resolver: SchemaResolver,
        ontology_ttl: str = "",
        max_repairs: int = 2,
    ) -> None:
        self.client = client
        self.resolver = resolver
        self.ontology_ttl = ontology_ttl
        self.repair_loop = RepairLoop(max_repairs=max_repairs)

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    def run(
        self,
        nl: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> PipelineOutcome:
        """Translate ``nl`` → SPARQL → AQL with a bounded repair loop."""
        builder = PromptBuilder(ontology_ttl=self.ontology_ttl)
        records: list[LLMCallRecord] = []
        warnings: list[dict[str, Any]] = []
        t0 = time.perf_counter()

        first = self._call_llm(builder, nl, repair_context="")
        records.append(first.record)
        if first.record.error:
            return self._failure_outcome(
                nl=nl,
                sparql=first.sparql,
                records=records,
                warnings=warnings,
                t0=t0,
                repaired=False,
                reason=first.record.error,
            )

        translate_result: TranslateResult | None = None
        translate_error: SparqlError | None = None

        try:
            translate_result = _translate(first.sparql, resolver=self.resolver, params=params)
        except SparqlError as exc:
            translate_error = exc

        if translate_result is not None:
            return self._success_outcome(
                nl=nl,
                sparql=first.sparql,
                translate_result=translate_result,
                records=records,
                warnings=warnings,
                t0=t0,
                repaired=False,
            )

        # First attempt failed translation — drive the repair loop. The
        # loop calls back into ``self._call_llm`` for each retry; we
        # accumulate the records out-of-band so a successful repair
        # still preserves the failed first attempt in the audit trail.
        repair_records: list[LLMCallRecord] = []

        def _generator(repair_msg: str) -> str:
            local = PromptBuilder(ontology_ttl=self.ontology_ttl, repair_context=repair_msg)
            attempt = self._call_llm(local, nl, repair_context=repair_msg)
            repair_records.append(attempt.record)
            return attempt.sparql

        def _translator(sparql: str) -> Any:
            nonlocal translate_result
            translate_result = _translate(sparql, resolver=self.resolver, params=params)
            return translate_result

        # Seed the loop with the *first* failure — repair starts from
        # there, not from the original happy-path SPARQL, so the
        # ``RepairLoop`` invariants line up.
        outcome = self.repair_loop.run(
            first_sparql=first.sparql,
            translator=_translator,
            generator=_generator,
        )
        # Reset translate_result so the failure path doesn't accidentally
        # reuse a value from a previous, unrelated attempt.
        if not outcome.succeeded:
            translate_result = None
        records.extend(repair_records)

        if outcome.succeeded and translate_result is not None:
            warnings.append(
                {
                    "code": "W_NL_REPAIRED",
                    "message": (
                        f"NL → SPARQL translation succeeded after {outcome.attempts} repair attempt(s) "
                        f"(first error: {translate_error.code if translate_error else 'unknown'})."
                    ),
                }
            )
            return self._success_outcome(
                nl=nl,
                sparql=outcome.sparql,
                translate_result=translate_result,
                records=records,
                warnings=warnings,
                t0=t0,
                repaired=True,
            )

        return self._failure_outcome(
            nl=nl,
            sparql=outcome.sparql,
            records=records,
            warnings=warnings,
            t0=t0,
            repaired=outcome.attempts > 0,
            reason=str(outcome.last_error or translate_error or "translation failed"),
        )

    def explain(
        self,
        *,
        nl: str | None = None,
        sparql: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> PipelineOutcome:
        """Translate (if needed) and ask the LLM for a plain-English summary."""
        if not nl and not sparql:
            raise ValueError("explain() requires at least one of nl=, sparql=")

        if nl:
            outcome = self.run(nl, params=params)
        else:
            # SPARQL-only path — translate the user-supplied SPARQL
            # without an LLM call. Synthesises an empty audit trail so
            # the response shape stays uniform.
            outcome = self._translate_only(sparql or "", params=params)

        # Use the resulting SPARQL (translated or user-supplied) for
        # the explanation pass. Empty SPARQL → emit an empty
        # explanation with a warning rather than a second LLM call.
        target_sparql = outcome.sparql or sparql or ""
        if not target_sparql.strip():
            outcome.warnings.append(
                {
                    "code": "W_NL_EXPLAIN_EMPTY",
                    "message": "No SPARQL available to explain; skipping LLM round-trip.",
                }
            )
            return outcome

        record, content = self._call_llm_raw(build_explain_messages(target_sparql))
        outcome.llm_call_records.append(record)
        outcome.explanation = content.strip() if not record.error else ""
        if record.error:
            outcome.warnings.append(
                {
                    "code": "W_NL_EXPLAIN_FAILED",
                    "message": f"Explanation LLM call failed: {record.error}",
                }
            )
        return outcome

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _translate_only(self, sparql: str, *, params: dict[str, Any] | None) -> PipelineOutcome:
        """SPARQL-in / AQL-out without an LLM call (used by ``/nl-explain`` SPARQL path)."""
        t0 = time.perf_counter()
        try:
            tr = _translate(sparql, resolver=self.resolver, params=params)
        except SparqlError as exc:
            return PipelineOutcome(
                nl="",
                sparql=sparql,
                aql="",
                bind_vars={},
                warnings=[{"code": exc.code, "message": str(exc)}],
                latency_ms=int((time.perf_counter() - t0) * 1000),
                repaired=False,
            )
        return PipelineOutcome(
            nl="",
            sparql=sparql,
            aql=tr.aql,
            bind_vars=dict(tr.bind_vars),
            warnings=list(tr.warnings or []),
            latency_ms=int((time.perf_counter() - t0) * 1000),
            repaired=False,
        )

    def _call_llm(
        self,
        builder: PromptBuilder,
        nl: str,
        *,
        repair_context: str,
    ) -> _SingleCallOutcome:
        """Send one prompt to the LLM and parse the response into SPARQL."""
        builder.repair_context = repair_context
        messages = builder.render_messages(nl)
        record, content = self._call_llm_raw(messages)
        sparql = extract_sparql_from_response(content) if not record.error else ""
        record.sparql = sparql
        return _SingleCallOutcome(sparql=sparql, record=record, raw_content=content)

    def _call_llm_raw(self, messages: list[dict[str, str]]) -> tuple[LLMCallRecord, str]:
        """Invoke the underlying client; return (audit record, raw content).

        Wraps every transport / provider exception so a network blip in
        a long-running request doesn't propagate raw — the pipeline
        records the failure on the audit trail and the caller decides
        whether to surface a warning, a 502, or a 503.
        """
        provider = getattr(self.client, "provider", "unknown")
        model = getattr(self.client, "model", "unknown")
        t0 = time.perf_counter()
        try:
            response: LLMResponse = self.client.generate(messages)
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            logger.warning("LLM call failed: %s", exc)
            record = LLMCallRecord(
                provider=provider,
                model=model,
                sparql="",
                cost_usd=0.0,
                latency_ms=elapsed_ms,
                error=f"{type(exc).__name__}: {exc}",
            )
            return record, ""
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        cost = estimate_llm_cost_usd(provider, model, response.prompt_tokens, response.completion_tokens)
        record = LLMCallRecord(
            provider=provider,
            model=model,
            sparql="",
            cost_usd=cost,
            latency_ms=elapsed_ms,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            cached_tokens=response.cached_tokens,
        )
        return record, response.content

    def _success_outcome(
        self,
        *,
        nl: str,
        sparql: str,
        translate_result: TranslateResult,
        records: list[LLMCallRecord],
        warnings: list[dict[str, Any]],
        t0: float,
        repaired: bool,
    ) -> PipelineOutcome:
        merged_warnings = list(warnings) + list(translate_result.warnings or [])
        return PipelineOutcome(
            nl=nl,
            sparql=sparql,
            aql=translate_result.aql,
            bind_vars=dict(translate_result.bind_vars),
            warnings=merged_warnings,
            llm_call_records=records,
            latency_ms=int((time.perf_counter() - t0) * 1000),
            repaired=repaired,
        )

    def _failure_outcome(
        self,
        *,
        nl: str,
        sparql: str,
        records: list[LLMCallRecord],
        warnings: list[dict[str, Any]],
        t0: float,
        repaired: bool,
        reason: str,
    ) -> PipelineOutcome:
        warnings = list(warnings)
        warnings.append(
            {
                "code": "W_NL_TRANSLATION_FAILED",
                "message": f"NL → SPARQL → AQL translation failed: {reason}",
            }
        )
        return PipelineOutcome(
            nl=nl,
            sparql=sparql,
            aql="",
            bind_vars={},
            warnings=warnings,
            llm_call_records=records,
            latency_ms=int((time.perf_counter() - t0) * 1000),
            repaired=repaired,
        )
