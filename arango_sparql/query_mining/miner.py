"""Mine one saved query, or a whole database, into verified examples.

Ported from arango-cypher-py's ``arango_cypher/query_mining/miner.py``.

Per saved query: run the source as the reference (:mod:`.binding`), map the
documents it returned to the identities a SPARQL result binds
(:func:`.signature.resolve_identities`), draft a question + SPARQL
(:mod:`.generate`), transpile and execute the SPARQL, and keep the draft only
if its results match the source's (:mod:`.signature`). A failed attempt — the
draft does not parse, does not translate, does not run, or returns different
resources — is fed back to the model, up to :data:`MAX_ATTEMPTS` times.

The SPARQL is translated with the resolver the caller passes — the CLI builds
it the way the Workbench does (the bundle's OWL merged with its physical
mapping, ``service/mapping.py::_resolver_from_request``), so an example
verified here translates the same way when a user runs it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from arango.exceptions import ArangoError
from rdflib.plugins.sparql.parser import parseQuery

from ..api import translate
from ..errors import SchemaResolutionError, SparqlError
from ..translate.mapping import MappingBundle
from ..translate.resolver import SchemaResolver
from .binding import SourceRejected, SourceRun, run_read_only, run_source
from .generate import Draft, GenerationError, ProviderError, build_system_prompt, generate_draft
from .harvest import SavedQuery, harvest_saved_queries
from .signature import (
    Reference,
    ResultSignature,
    Verdict,
    bindings_signature,
    compare,
    reference_of,
    resolve_identities,
)

if TYPE_CHECKING:
    # Type-only: nl2sparql's package import loads the service app (models.py).
    from ..nl2sparql.client import LLMClient

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
#: A batch stops after this many queries in a row fail at the provider: a bad
#: key or exhausted quota would fail every remaining query the same way.
MAX_CONSECUTIVE_PROVIDER_FAILURES = 2
#: Properties that identify a selected start node to the model, in preference
#: order; the first few present are shown.
IDENTIFYING_PROPERTIES = ("name", "title", "external_id", "arn", "id", "email", "label")
MAX_IDENTIFYING = 3


def _query_form(sparql: str) -> str | None:
    """``SelectQuery`` / ``AskQuery`` / …, or ``None`` when it does not parse
    (the translator then reports the parse error as the feedback)."""
    try:
        return str(parseQuery(sparql)[1].name)
    except Exception:  # noqa: BLE001 - any parse failure is reported by translate()
        return None


@dataclass(frozen=True)
class MinedExample:
    question: str
    sparql: str
    aql: str  # the transpiled AQL that was verified
    bind_vars: dict[str, Any]
    verdict: Verdict
    source: SavedQuery
    source_signature: ResultSignature
    reference: Reference
    attempts: int


@dataclass(frozen=True)
class Attempt:
    """One failed draft and why it failed — the retry feedback, and the report."""

    failure: str
    question: str = ""
    sparql: str = ""


@dataclass
class Outcome:
    query: SavedQuery
    example: MinedExample | None = None
    reason: str = ""
    failed_attempts: list[Attempt] = field(default_factory=list)
    #: The LLM call failed outright; the query itself was never judged.
    provider_failed: bool = False

    @property
    def verified(self) -> bool:
        return self.example is not None


def start_properties(db: Any, start: str | None) -> dict[str, Any] | None:
    """The identifying properties of a canvas action's sampled start node."""
    if not start:
        return None
    try:
        doc = next(iter(db.aql.execute("RETURN DOCUMENT(@id)", bind_vars={"id": start})), None)
    except ArangoError as exc:
        logger.warning("could not read start node %s: %s", start, exc)
        return None
    if not isinstance(doc, dict):
        return None
    shown = {
        k: doc[k]
        for k in IDENTIFYING_PROPERTIES
        if k in doc and isinstance(doc[k], (str, int, float)) and str(doc[k]).strip()
    }
    picked = dict(list(shown.items())[:MAX_IDENTIFYING])
    return picked or {"_key": doc.get("_key")}


def _translation_feedback(exc: SparqlError) -> str:
    feedback = f"the SPARQL does not translate: {exc}"
    # An undeclared term is the model inventing a name; say where real ones are.
    # (Other resolution errors — e.g. an untyped subject — explain themselves.)
    if isinstance(exc, SchemaResolutionError) and "is not declared" in str(exc):
        feedback += ". Use only the classes and properties the schema lists, written as :Name"
    return feedback


@dataclass(frozen=True)
class _Checked:
    """A draft that translated, ran and matched the reference."""

    aql: str
    bind_vars: dict[str, Any]
    verdict: Verdict


def _check(
    db: Any,
    resolver: SchemaResolver,
    draft: Draft,
    reference: Reference,
    collections: frozenset[str],
) -> _Checked | str:
    """The verified translation of *draft*, or why it failed (retry feedback)."""
    form = _query_form(draft.sparql)
    if form is not None and form != "SelectQuery":
        # Only bindings can be compared with the saved query's results.
        return f"the SPARQL is a {form.removesuffix('Query').upper()} query; write a SELECT"
    try:
        transpiled = translate(draft.sparql, resolver=resolver)
    except SparqlError as exc:
        return _translation_feedback(exc)
    except Exception as exc:  # noqa: BLE001 - a translator crash is feedback for the model, not a crash here
        return f"the SPARQL does not translate: {type(exc).__name__}: {exc}"
    bind = dict(transpiled.bind_vars)
    try:
        rows = run_read_only(db, transpiled.aql, bind)
    except SourceRejected as exc:
        return f"the translated query {exc}"
    candidate = bindings_signature(rows, reference=reference, collections=collections)
    verdict = compare(reference, candidate)
    if not verdict.passed:
        return (
            f"its results differ from the saved query's ({verdict.detail}; saved query: "
            f"{reference.describe()}; yours: {candidate.describe()})"
        )
    return _Checked(transpiled.aql, bind, verdict)


def _collections(db: Any) -> frozenset[str]:
    """Names an ``_id`` in a binding may start with."""
    try:
        return frozenset(c["name"] for c in db.collections() if isinstance(c, dict) and "name" in c)
    except ArangoError as exc:
        logger.warning("could not list collections: %s", exc)
        return frozenset()


def mine_query(
    db: Any,
    query: SavedQuery,
    client: LLMClient,
    *,
    resolver: SchemaResolver,
    system_prompt: str,
    edge_definitions: list[dict[str, Any]] | None = None,
    collections: frozenset[str] | None = None,
    max_attempts: int = MAX_ATTEMPTS,
) -> Outcome:
    outcome = Outcome(query)
    try:
        run: SourceRun = run_source(db, query, edge_definitions=edge_definitions)
    except SourceRejected as exc:
        outcome.reason = f"source: {exc}"
        return outcome
    try:
        reference = reference_of(run.signature, resolve_identities(db, run.signature.vertices))
    except ArangoError as exc:
        outcome.reason = f"source: could not read the identities of its documents: {exc}"
        return outcome
    known = collections if collections is not None else _collections(db)

    props = start_properties(db, run.start)
    feedback: str | None = None
    previous: Draft | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            draft = generate_draft(
                client,
                query,
                run.bind_vars,
                system_prompt=system_prompt,
                start_properties=props,
                feedback=feedback,
                previous=previous,
            )
        except GenerationError as exc:
            feedback = str(exc)
            outcome.failed_attempts.append(Attempt(feedback))
            continue
        except ProviderError as exc:
            outcome.reason = f"LLM provider failed: {exc}"
            outcome.provider_failed = True
            return outcome
        checked = _check(db, resolver, draft, reference, known)
        if isinstance(checked, _Checked):
            outcome.example = MinedExample(
                question=draft.question,
                sparql=draft.sparql,
                aql=checked.aql,
                bind_vars=checked.bind_vars,
                verdict=checked.verdict,
                source=query,
                source_signature=run.signature,
                reference=reference,
                attempts=attempt,
            )
            return outcome
        feedback, previous = checked, draft
        outcome.failed_attempts.append(Attempt(checked, draft.question, draft.sparql))
    outcome.reason = (
        f"no verified SPARQL after {max_attempts} attempts: {outcome.failed_attempts[-1].failure}"
    )
    return outcome


def mine_database(
    db: Any,
    bundle: MappingBundle,
    client: LLMClient,
    *,
    resolver: SchemaResolver,
    schema_summary: str,
    namespace: str,
    graph: str | None = None,
    include_builtins: bool = False,
    max_attempts: int = MAX_ATTEMPTS,
) -> tuple[list[Outcome], list[tuple[str, str]]]:
    """Outcomes for every saved query in *db* (scoped to *graph* when given),
    plus the harvest's skips. Visualizer built-ins are left out unless asked:
    "fetch edges of type X" teaches nothing about the domain."""
    report = harvest_saved_queries(db, graph=graph)
    edge_definitions: list[dict[str, Any]] = []
    if graph:
        edge_definitions = list(db.graph(graph).properties().get("edge_definitions") or [])
    system_prompt = build_system_prompt(bundle, schema_summary=schema_summary, namespace=namespace)
    collections = _collections(db)
    outcomes: list[Outcome] = []
    skipped = list(report.skipped)
    provider_failures = 0
    for query in report.queries:
        where = f"{query.source}/{query.key}"
        if query.builtin and not include_builtins:
            skipped.append((where, "visualizer built-in"))
            continue
        if provider_failures >= MAX_CONSECUTIVE_PROVIDER_FAILURES:
            skipped.append((where, "not attempted: the LLM provider kept failing"))
            continue
        logger.info("mining %s %r", where, query.name)
        outcome = mine_query(
            db,
            query,
            client,
            resolver=resolver,
            system_prompt=system_prompt,
            edge_definitions=edge_definitions,
            collections=collections,
            max_attempts=max_attempts,
        )
        provider_failures = provider_failures + 1 if outcome.provider_failed else 0
        outcomes.append(outcome)
    return outcomes, skipped
