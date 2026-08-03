"""SPARQL adapter for the shared NL engine (``arango_query_core.nl``).

Implements the five :class:`~arango_query_core.nl.seams.QueryLanguageAdapter`
seams for SPARQL, delegating to this repo's existing pieces rather than
inventing parallel ones:

* **Grammar prompt** (seam 1) — :data:`arango_sparql.nl2sparql.prompt._SYSTEM_PROMPT`
  via :class:`PromptBuilder`, with the OWL Turtle as the schema context.
* **Corpus** (seam 2) — ``corpora/*.yml`` in the shared
  ``(question, query)`` format, retrieved by the core's BM25 index.
* **Validator** (seam 3) — the transpiler itself: a candidate is valid
  only when :func:`arango_sparql.translate.parser.parse_sparql` accepts
  it AND :func:`arango_sparql.api.translate` emits AQL against the
  session's resolver. The LLM can never hand the caller a query the
  deterministic engine chokes on.
* **Repair rules** (seam 4) — :func:`arango_sparql.nl2sparql.repair.format_repair_context`,
  which embeds the stable error code and nudges unsupported-feature
  failures toward translatable SPARQL 1.1 alternatives.
* **Guardrails** (seam 5) — allow-all in v1: the fabric P1 deployment
  is single-tenant, and tenant gating already happens deterministically
  inside ``translate()`` (``CrossTenantJoinError``). A SPARQL-algebra
  tenant-scope validator (the analog of cypher-py's ``tenant_ast_*``)
  slots in here when multi-tenant NL arrives.

This module is why the adapter lives in THIS repo and not in
arango-query-core: seam 3 needs the whole transpiler stack.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from arango_query_core.nl import FewShotIndex, GuardrailVerdict, ValidationResult

from ..errors import SparqlError
from ..translate.resolver import SchemaResolver
from .prompt import PromptBuilder
from .repair import format_repair_context

_CORPORA_DIR = Path(__file__).resolve().parent / "corpora"


@dataclass
class SparqlLanguageAdapter:
    """``QueryLanguageAdapter`` implementation for SPARQL 1.1.

    ``resolver`` is the session's schema resolver — validation
    translates against it, so "valid" means *executable against this
    deployment's mapping*, not merely parseable. ``ontology_ttl`` is
    the Turtle block rendered into the system prompt (usually the same
    ontology the resolver wraps).
    """

    resolver: SchemaResolver
    ontology_ttl: str = ""
    tenant_id: str | None = None
    corpus_paths: list[Path] = field(default_factory=lambda: sorted(_CORPORA_DIR.glob("*.yml")))

    language: str = "sparql"
    # Few-shot retrieval backend (arango-query-core FewShotIndex.mode): "auto"
    # picks DenseRetriever when sentence-transformers is installed, else BM25
    # (safe default, no breakage); "dense" forces PJ's DenseRetriever and
    # hard-raises if the [dense] extra is missing (measurement integrity for the
    # eval sweep); "bm25" forces lexical. Override via ARANGO_SPARQL_FEWSHOT_MODE.
    few_shot_mode: str = field(
        default_factory=lambda: os.environ.get("ARANGO_SPARQL_FEWSHOT_MODE", "auto")
    )
    _few_shot: FewShotIndex | None = field(default=None, repr=False)

    def grammar_prompt_section(self, schema_context: str) -> str:
        # The engine passes schema_context through verbatim; prefer the
        # explicit constructor ontology when given, so route callers can
        # build the adapter once per session.
        ttl = self.ontology_ttl or schema_context
        return PromptBuilder(ontology_ttl=ttl).render_system()

    def few_shot_index(self) -> FewShotIndex | None:
        if self._few_shot is None:
            self._few_shot = FewShotIndex.from_corpus_files(
                list(self.corpus_paths), mode=self.few_shot_mode
            )
        return self._few_shot

    def validate(self, query: str) -> ValidationResult:
        if not query.strip():
            return ValidationResult(ok=False, error="empty response — no SPARQL query found", code="E_EMPTY")
        try:
            from ..api import translate

            translate(query, resolver=self.resolver, tenant_id=self.tenant_id)
        except SparqlError as exc:
            return ValidationResult(ok=False, error=format_repair_context(exc), code=exc.code)
        return ValidationResult(ok=True)

    def repair_hint(self, query: str, failure: ValidationResult) -> str:
        # validate() already rendered the code-tagged, length-bounded
        # repair message via format_repair_context — reuse it verbatim.
        return failure.error

    def guardrails(self, query: str, context: dict[str, Any]) -> GuardrailVerdict:
        return GuardrailVerdict(allowed=True)
