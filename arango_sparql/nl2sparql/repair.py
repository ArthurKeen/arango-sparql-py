"""Bounded repair loop for the NL → SPARQL pipeline.

When the deterministic translator (``arango_sparql.api.translate``)
rejects an LLM-emitted SPARQL query — typically because the parser
chokes on malformed syntax (:class:`SparqlParseError`) or the visitor
hits an unsupported algebra node (:class:`UnsupportedSparqlError`) —
the repair loop:

1. Captures the error message.
2. Re-renders the prompt with the error appended to the *user* turn
   (system stays byte-stable — important for provider-side prompt
   caching).
3. Sends the prompt back to the same LLM.
4. Tries the translator again.

Repair is bounded: the default cap is :data:`DEFAULT_MAX_REPAIRS`
(2) — overridable per-request via the ``max_repairs`` field on the
API contract or process-wide via the ``NL2SPARQL_MAX_REPAIRS`` env
var. Exhausting the cap raises the *last* error encountered so the
caller sees the most informative diagnostic.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass

from ..errors import SparqlError, UnsupportedSparqlError

logger = logging.getLogger(__name__)


def _env_default_max_repairs() -> int:
    """Read ``NL2SPARQL_MAX_REPAIRS`` once per call, with a safe fallback.

    Read at call time (not import time) so an operator can flip the
    knob in tests via ``monkeypatch.setenv`` without re-importing the
    module. Invalid values fall back to the static default and emit a
    warning — never crash the request.
    """
    raw = os.getenv("NL2SPARQL_MAX_REPAIRS")
    if raw is None:
        return DEFAULT_MAX_REPAIRS
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "NL2SPARQL_MAX_REPAIRS=%r is not an integer; using default %d", raw, DEFAULT_MAX_REPAIRS
        )
        return DEFAULT_MAX_REPAIRS
    if value < 0:
        return 0
    return min(value, MAX_REPAIRS_CEILING)


DEFAULT_MAX_REPAIRS = 2
MAX_REPAIRS_CEILING = 5


@dataclass
class RepairOutcome:
    """Result envelope returned from :class:`RepairLoop.run`.

    ``attempts`` is ``0`` when the first attempt succeeds, ``N`` when
    the loop fired N repair iterations before succeeding, or
    ``max_repairs`` when every attempt failed (in which case
    ``last_error`` is set to the most recent ``SparqlError`` and
    ``sparql`` is the last attempted query).
    """

    sparql: str
    attempts: int
    succeeded: bool
    last_error: SparqlError | None = None


# Translator callable: takes a SPARQL string, returns "ok" or raises a SparqlError.
_TranslatorFn = Callable[[str], object]
# Generator callable: takes a repair-context message, returns the next SPARQL string.
_GeneratorFn = Callable[[str], str]


class RepairLoop:
    """Drive the SPARQL → translate → re-prompt loop with a bounded budget.

    The loop is intentionally translator-agnostic: it accepts a
    *generator* that produces SPARQL given a repair-context string and
    a *translator* that validates SPARQL by raising
    :class:`SparqlError` on failure. The pipeline wires the LLM call
    into the generator and ``arango_sparql.api.translate`` into the
    translator; tests substitute scripted callables that simulate the
    failure modes without touching either.
    """

    def __init__(self, *, max_repairs: int | None = None) -> None:
        if max_repairs is None:
            max_repairs = _env_default_max_repairs()
        self.max_repairs = max(0, min(max_repairs, MAX_REPAIRS_CEILING))

    def run(
        self,
        *,
        first_sparql: str,
        translator: _TranslatorFn,
        generator: _GeneratorFn,
    ) -> RepairOutcome:
        """Try ``first_sparql`` against ``translator``; on failure, repair.

        ``generator(repair_context)`` is called for each retry with
        the formatted error message — the pipeline's bridge to the
        LLM keeps the system prompt stable and only mutates the user
        turn, preserving any prefix-cache hit.
        """
        sparql = first_sparql
        last_error: SparqlError | None = None
        for attempt in range(self.max_repairs + 1):
            try:
                translator(sparql)
                return RepairOutcome(sparql=sparql, attempts=attempt, succeeded=True)
            except SparqlError as exc:
                last_error = exc
                if attempt == self.max_repairs:
                    logger.warning(
                        "RepairLoop exhausted budget (%d repairs); surfacing %s: %s",
                        self.max_repairs,
                        exc.code,
                        exc,
                    )
                    return RepairOutcome(
                        sparql=sparql,
                        attempts=attempt,
                        succeeded=False,
                        last_error=exc,
                    )
                logger.info(
                    "RepairLoop attempt %d/%d failed (%s); re-prompting LLM",
                    attempt + 1,
                    self.max_repairs + 1,
                    exc.code,
                )
                repair_msg = format_repair_context(exc)
                sparql = generator(repair_msg)
        # Unreachable — the loop always returns inside the body. The
        # guard is here because static analysis on a try/except inside
        # a for/range can't always prove control flow.
        return RepairOutcome(
            sparql=sparql,
            attempts=self.max_repairs,
            succeeded=False,
            last_error=last_error,
        )


def format_repair_context(error: SparqlError) -> str:
    """Render a repair message for the LLM that includes the stable error code.

    Includes the ``code`` so the LLM can disambiguate parse-shape vs.
    semantic-shape failures. Length-bounded to keep the second prompt
    from blowing past the model's context window when the underlying
    error message is itself a giant traceback. Unsupported-feature
    errors get a hint nudging the model toward SPARQL 1.1 alternatives.
    """
    msg = str(error)
    if len(msg) > 600:
        msg = msg[:600] + " …"
    suffix = ""
    if isinstance(error, UnsupportedSparqlError):
        suffix = (
            "\n\nThis SPARQL feature is not yet supported by the deterministic "
            "translator. Try a SPARQL 1.1 alternative that uses BGPs, OPTIONAL, "
            "FILTER, UNION, GROUP BY, ORDER BY, or LIMIT instead."
        )
    return f"[{error.code}] {msg}{suffix}"
