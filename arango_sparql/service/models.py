"""Pydantic request / response models + ``_MAX_*`` length constants.

Mirror of ``arango_cypher.service.models``: every user-controlled
string field has a ``Field(..., max_length=_MAX_*)`` envelope so a
10 MB POST body cannot wedge the parser, balloon a SQLite store, or
push novel-length prompts at an LLM.

The ``_MAX_*`` constants live as module-level attributes (intentionally
underscore-prefixed) so an operator who needs to raise one for a
specific deployment can grep, monkeypatch, and rebuild the affected
model.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

# Stricter-than-type field bounds for the user-controlled string fields on
# every request model below. Real interactive payloads are well below these
# limits; the constants exist to bound an attack rather than restrict
# normal use.
_MAX_SPARQL_LENGTH = 100_000  # ~100 KB; a real interactive query is < 10 KB
_MAX_AQL_LENGTH = 100_000  # raw AQL on /execute-aql; same envelope
_MAX_NL_QUESTION_LENGTH = 4_000  # bounds LLM context-window cost
_MAX_TURTLE_LENGTH = 1_000_000  # OWL ontologies can be sizeable; 1 MB cap
_MAX_FIELD_LENGTH = 256  # urls, usernames, db names, tenant fields

# Hard cap on materialised /execute and /execute-aql cursor rows. Mirrors
# the Cypher project's policy of bounding an interactive run before it
# pages a multi-GB cursor through the FastAPI worker. Operators who need
# more should run the query against the database directly. The number is
# generous (10k rows is well above any UI table render budget) but
# bounded so a runaway query can't OOM the service host.
_MAX_RESULT_DOCS = 10_000


class ConnectRequest(BaseModel):
    url: str = Field(default="http://localhost:8529", max_length=_MAX_FIELD_LENGTH)
    database: str = Field(default="_system", max_length=_MAX_FIELD_LENGTH)
    username: str = Field(default="root", max_length=_MAX_FIELD_LENGTH)
    password: str = Field(default="", max_length=_MAX_FIELD_LENGTH)

    @field_validator("url")
    @classmethod
    def _url_shape(cls, v: str) -> str:
        # Defensive shape-check before the SSRF guard at /connect runs.
        # The /connect endpoint already rejects bad targets at runtime
        # via the SSRF allowlist; this validator catches the cheaper,
        # more obviously-broken cases at request-validation time so the
        # caller gets a 422 with a clear "url is malformed" hint instead
        # of a deeper 4xx/5xx after the connect machinery has spun up.
        if not v:
            return v
        lowered = v.lower()
        if not (lowered.startswith("http://") or lowered.startswith("https://")):
            raise ValueError("url must start with http:// or https://")
        return v


class ConnectResponse(BaseModel):
    token: str
    databases: list[str]


class TranslateRequest(BaseModel):
    sparql: str = Field(..., max_length=_MAX_SPARQL_LENGTH)
    ontology_ttl: str | None = Field(default=None, max_length=_MAX_TURTLE_LENGTH)
    mapping: dict[str, Any] | None = None
    params: dict[str, Any] | None = None


class TranslateResponse(BaseModel):
    aql: str
    bind_vars: dict[str, Any]
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    # Subset of :attr:`warnings` whose ``code`` starts with ``W_SCHEMA_``.
    # Duplicated as a convenience so the UI can render schema-mapping
    # advisories (unmapped IRI, default-collection fallback) in a
    # dedicated sidebar without re-filtering on every paint. The full
    # ``warnings`` field still carries every entry — this is purely a
    # projection. See :func:`arango_sparql.api.translate` for the
    # population point.
    schema_warnings: list[dict[str, Any]] = Field(default_factory=list)
    elapsed_ms: float | None = None


class SparqlExecuteRequest(BaseModel):
    sparql: str = Field(..., max_length=_MAX_SPARQL_LENGTH)
    ontology_ttl: str | None = Field(default=None, max_length=_MAX_TURTLE_LENGTH)
    mapping: dict[str, Any] | None = None
    params: dict[str, Any] | None = None
    database: str = Field(default="_system", max_length=_MAX_FIELD_LENGTH)


class SparqlExecuteResponse(BaseModel):
    bindings: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    aql: str | None = None
    bind_vars: dict[str, Any] | None = None
    elapsed_ms: float | None = None
    # Wall-clock time spent in the SPARQL → AQL transpiler on this
    # request. Surfaced separately from ``elapsed_ms`` (the
    # translate+execute total) so a UI can show both badges side-by-side
    # after a Run; otherwise users lose visibility into translation cost
    # the moment they execute.
    translate_ms: float | None = None
    exec_ms: float | None = None
    # ``True`` when the cursor materialisation was capped at
    # :data:`_MAX_RESULT_DOCS`. UI surfaces this as a "results truncated"
    # banner so the operator knows to refine the query rather than
    # assume they saw the full set.
    truncated: bool = False


class RawAqlRequest(BaseModel):
    """Raw-AQL pass-through payload for ``/execute-aql``.

    Mirrors the Cypher project's ``ExecuteAqlRequest`` — exposed so the
    UI can run an already-translated query without paying the
    parse/translate cost again, and so power users can hand-author AQL
    against the same session that ran a SPARQL execution.
    """

    aql: str = Field(..., max_length=_MAX_AQL_LENGTH)
    bind_vars: dict[str, Any] = Field(default_factory=dict)


class RawAqlResponse(BaseModel):
    results: list[Any] = Field(default_factory=list)
    aql: str
    bind_vars: dict[str, Any] = Field(default_factory=dict)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    exec_ms: float | None = None
    truncated: bool = False


class SparqlExplainResponse(BaseModel):
    """Response shape for ``POST /explain`` — the AQL execution plan
    produced by ``db.aql.explain()`` for the AQL transpiled from the
    incoming SPARQL.

    Mirrors the Cypher project's ``/explain`` envelope: the SPARQL
    source is echoed back so a UI can pair the plan with the originating
    query without holding extra request state, ``aql`` + ``bind_vars``
    are the artefacts of the SPARQL→AQL transpilation, and ``plan`` is
    the raw ArangoDB explain output (``{nodes, rules, collections,
    variables, estimatedCost, ...}``). The schema/topology of ``plan``
    follows ArangoDB's contract — we surface it verbatim rather than
    pinning it to a Pydantic shape because the operator-facing UI
    already renders against the native ArangoDB explain JSON.
    """

    sparql: str
    aql: str
    bind_vars: dict[str, Any] = Field(default_factory=dict)
    plan: dict[str, Any] = Field(default_factory=dict)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    translate_ms: float | None = None


class SparqlProfileResponse(BaseModel):
    """Response shape for ``POST /profile`` — the per-stage timing and
    actual result rows from executing the transpiled AQL with
    ``profile=2`` (full).

    ``bindings`` carries the materialised cursor rows (capped at
    :data:`_MAX_RESULT_DOCS`), ``profile`` is the ArangoDB profile
    object (``{plan, stats, profile, warnings, ...}``) returned by
    ``cursor.profile()``. We expose both because the UI shows the rows
    *and* the per-node timings side-by-side; without the rows the
    operator cannot tell whether the slow step actually produced the
    expected output.
    """

    sparql: str
    aql: str
    bind_vars: dict[str, Any] = Field(default_factory=dict)
    bindings: list[dict[str, Any]] = Field(default_factory=list)
    truncated: bool = False
    profile: dict[str, Any] = Field(default_factory=dict)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    translate_ms: float | None = None
    exec_ms: float | None = None


class ValidateRequest(BaseModel):
    sparql: str = Field(..., max_length=_MAX_SPARQL_LENGTH)
    ontology_ttl: str | None = Field(default=None, max_length=_MAX_TURTLE_LENGTH)
    mapping: dict[str, Any] | None = None
    params: dict[str, Any] | None = None


class ValidateResponse(BaseModel):
    """Result of a parse-only ``/validate`` round-trip.

    ``valid`` is ``True`` when the SPARQL string parses cleanly under
    ``rdflib.plugins.sparql``; ``False`` otherwise. ``errors`` carries
    a list of ``{code, message}`` records — one per error surfaced by
    the parser. ``warnings`` is reserved for non-fatal advisories the
    transpiler emits during validation (e.g. unmapped predicate IRIs)
    so a fully-passing ``valid=true`` can still ship guidance to the
    operator.
    """

    valid: bool
    errors: list[dict[str, str]] = Field(default_factory=list)
    warnings: list[dict[str, Any]] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    error: str
    code: str


class HealthResponse(BaseModel):
    status: str
    version: str


# ---------------------------------------------------------------------------
# NL → SPARQL pipeline request / response surface (frozen for round-3 UI).
# ---------------------------------------------------------------------------
#
# Defined inline here (rather than re-exported from
# :mod:`arango_sparql.nl2sparql.models`) to break the import cycle:
# the nl2sparql package imports the ``_MAX_*`` constants from this
# module, so the Pydantic models live here and the dataclass-only
# pipeline types (LLMResponse, LLMCallRecord, PipelineOutcome) live in
# the nl2sparql package. Tests can import either path — both names
# resolve to the same class identity.


class NlTranslateRequest(BaseModel):
    """Request body for ``POST /nl-translate``.

    Per rule 300, ``ontology_ttl`` is the canonical schema slot — the
    LLM reads Turtle better than any other RDF serialisation. The
    ``mapping`` slot is reserved for the JSON payload some callers
    already hold from an earlier endpoint round-trip; it is converted
    into a SchemaResolver via the same adapter as ``/translate``.
    """

    nl: str = Field(..., min_length=1, max_length=_MAX_NL_QUESTION_LENGTH)
    ontology_ttl: str | None = Field(default=None, max_length=_MAX_TURTLE_LENGTH)
    mapping: dict[str, Any] | None = None
    max_repairs: int = Field(default=2, ge=0, le=5)


class NlTranslateResponse(BaseModel):
    """Response body for ``POST /nl-translate``."""

    nl: str
    sparql: str
    aql: str
    bind_vars: dict[str, Any] = Field(default_factory=dict)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    llm_calls: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    repaired: bool = False


class NlExplainRequest(BaseModel):
    """Request body for ``POST /nl-explain``."""

    nl: str | None = Field(default=None, max_length=_MAX_NL_QUESTION_LENGTH)
    sparql: str | None = Field(default=None, max_length=_MAX_SPARQL_LENGTH)
    ontology_ttl: str | None = Field(default=None, max_length=_MAX_TURTLE_LENGTH)
    mapping: dict[str, Any] | None = None
    max_repairs: int = Field(default=2, ge=0, le=5)


class NlExplainResponse(NlTranslateResponse):
    """Response body for ``POST /nl-explain`` — adds an LLM-generated explanation."""

    explanation: str = ""


class NlExecuteRequest(NlTranslateRequest):
    """Request body for ``POST /nl-execute``.

    Auth comes from ``X-Arango-Session`` via the existing
    ``_get_session`` dependency — there is no token field in the body
    so a hostile caller cannot point one user's NL request at another
    user's database by guessing an unauthenticated body field.
    """

    database: str = Field(default="_system", max_length=_MAX_FIELD_LENGTH)


class NlExecuteResponse(NlTranslateResponse):
    """Response body for ``POST /nl-execute`` — adds execution telemetry."""

    bindings: list[dict[str, Any]] = Field(default_factory=list)
    truncated: bool = False
    exec_ms: int = 0
