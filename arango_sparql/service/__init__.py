"""FastAPI HTTP service for ``arango-sparql-py``.

Mirror of ``arango_cypher.service`` — provides REST endpoints for
SPARQL translation, execution, validation, and connection
management. Serves as the backend for the SPARQL Workbench UI.

Usage::

    uvicorn arango_sparql.service:app --host 0.0.0.0 --port 8000

Package layout (mirrors arango-cypher-py one-to-one):

* :mod:`.app` — :class:`FastAPI` instance, CORS guard,
  :data:`_PUBLIC_MODE` flag, observability middleware install.
* :mod:`.security` — sessions, rate-limit token buckets, SSRF guard,
  error / log redaction, the Pydantic 422 handler.
* :mod:`.models` — every request / response Pydantic model + the
  ``_MAX_*`` length constants.
* :mod:`.mapping` — request-shape adapter that builds a
  :class:`SchemaResolver` from inline Turtle or a JSON mapping payload.
* :mod:`.registry` — process-wide extension registry (stub today; see
  module docstring for the shape it grows when SPARQL extensions land).
* :mod:`.observability` — correlation-ID middleware, structured logging
  helpers, endpoint-timing surface.
* :mod:`.routes` — endpoint cluster modules (``connect``, ``sparql``,
  ``health``). Importing the subpackage runs every endpoint's
  ``@app.post(...)`` / ``@app.get(...)`` decorator and registers it on
  :data:`.app.app`.

This file's job is to (a) execute the imports in the order required to
preserve the side-effect sequence, and (b) re-export the public +
underscore-prefixed names that external callers (tests, ``main.py``)
depend on, mirroring the Cypher package init.
"""

from __future__ import annotations

import sys as _sys
import time  # noqa: F401  (re-export for monkeypatch surface)

# Force submodule re-execution on package reload — same rationale as
# arango_cypher.service.__init__: a sys.modules.pop()-then-reimport
# would otherwise re-run only this __init__.py while leaving
# ``.app`` / ``.security`` / ``.routes.*`` frozen at the values from
# the very first import.
for _name in [n for n in list(_sys.modules) if n.startswith("arango_sparql.service.")]:
    del _sys.modules[_name]
del _sys

# 1) App factory + observability install. Importing :mod:`.app` runs
#    CORS guard, ``configure_observability()`` and
#    ``app.add_middleware(CorrelationIdMiddleware)`` as side effects.
#    ``ArangoClient`` is re-exposed off this package so the
#    ``monkeypatch.setattr("arango_sparql.service.ArangoClient", ...)``
#    pattern in tests keeps working — the connect route reads it
#    lazily via the package attribute.
from arango import ArangoClient  # noqa: F401  (re-export)

from .app import (
    _PUBLIC_MODE,
    _cors_credentials,
    _cors_origins,
    _svc_logger,
    app,
)

# 2) Pydantic models + length constants. Pure data — no side effects.
from .models import (
    _MAX_AQL_LENGTH,
    _MAX_FIELD_LENGTH,
    _MAX_NL_QUESTION_LENGTH,
    _MAX_RESULT_DOCS,
    _MAX_SPARQL_LENGTH,
    _MAX_TURTLE_LENGTH,
    ConnectRequest,
    ConnectResponse,
    ErrorResponse,
    HealthResponse,
    NlExecuteRequest,
    NlExecuteResponse,
    NlExplainRequest,
    NlExplainResponse,
    NlTranslateRequest,
    NlTranslateResponse,
    OwlClassModel,
    OwlExportRequest,
    OwlExportResponse,
    OwlImportRequest,
    OwlImportResponse,
    OwlPropertyModel,
    OwlSchemaResponse,
    RawAqlRequest,
    RawAqlResponse,
    SchemaFingerprintBlock,
    SchemaForceReacquireResponse,
    SchemaIntrospectResponse,
    SchemaInvalidateCacheResponse,
    SchemaPropertiesResponse,
    SchemaStatisticsResponse,
    SchemaStatusResponse,
    SchemaSummaryRequest,
    SchemaSummaryResponse,
    SparqlExecuteRequest,
    SparqlExecuteResponse,
    SparqlExplainResponse,
    SparqlProfileResponse,
    TranslateRequest,
    TranslateResponse,
    ValidateRequest,
    ValidateResponse,
)

# 3) HTTP-shape mapping helpers — re-exported so tests can build
#    resolvers via the public surface without importing the submodule.
from .mapping import _resolver_from_request

# 4) Process-wide extension registry singleton (stub today — see
#    :mod:`.registry`). Re-exported so the route layer's import path
#    stays stable when extensions land.
from .registry import _build_registry, _default_registry

# 5) Observability surface. Re-exported so tests can do
#    ``from arango_sparql.service import log_endpoint_timing, correlation_id_var``
#    without reaching into the submodule. Note these are tail-end
#    imports — :mod:`.app` already called :func:`configure_observability`
#    and installed :class:`CorrelationIdMiddleware` at app-construction
#    time; these re-exports are the consumer-facing surface only.
from .observability import (
    CorrelationIdLogFilter,
    CorrelationIdMiddleware,
    configure_observability,
    correlation_id_var,
    current_llm_provider_and_model,
    estimate_llm_cost_usd,
    log_endpoint_timing,
    log_llm_call,
    time_endpoint,
)

# 6) Security primitives (sessions, rate limit, SSRF, error redaction,
#    422 handler). Imports :data:`.app.app` and registers the exception
#    handler via decorator side effect.
from .security import (
    _AUTH_HEADER_RE,
    _BLOCK_METADATA_HOSTS,
    _BLOCK_METADATA_IPS,
    _COLLECTION_NAME_RE,
    _CRED_RE,
    _HOST_PORT_RE,
    _PRIVATE_NETWORKS,
    _PROXY_ENV_VARS,
    _URL_RE,
    COMPUTE_RATE_LIMIT_PER_MINUTE,
    MAX_SESSIONS,
    NL_RATE_LIMIT_PER_MINUTE,
    SESSION_TTL_SECONDS,
    _check_compute_rate_limit,
    _check_connect_target,
    _check_nl_rate_limit,
    _client_key,
    _compute_bucket,
    _connect_allowed_hosts,
    _describe_connect_error,
    _evict_lru,
    _get_session,
    _nl_bucket,
    _prune_expired,
    _redact_value,
    _require_session_in_public_mode,
    _sanitize_error,
    _sanitize_pydantic_errors,
    _Session,
    _sessions,
    _TokenBucket,
    _translate_errors,
    _validation_error_handler,
    _walk_cause_chain,
)

# 7) Routes — importing the subpackage runs every endpoint's
#    ``@app.post(...)`` / ``@app.get(...)`` decorator and registers it
#    on :data:`.app.app`. Imported last so its log lines surface after
#    the security / observability setup noise.
from . import routes as _routes  # noqa: F401, E402

__all__ = [
    "app",
    # Re-exported request/response models
    "ConnectRequest",
    "ConnectResponse",
    "ErrorResponse",
    "HealthResponse",
    "NlExecuteRequest",
    "NlExecuteResponse",
    "NlExplainRequest",
    "NlExplainResponse",
    "NlTranslateRequest",
    "NlTranslateResponse",
    "OwlClassModel",
    "OwlExportRequest",
    "OwlExportResponse",
    "OwlImportRequest",
    "OwlImportResponse",
    "OwlPropertyModel",
    "OwlSchemaResponse",
    "RawAqlRequest",
    "RawAqlResponse",
    "SchemaFingerprintBlock",
    "SchemaForceReacquireResponse",
    "SchemaIntrospectResponse",
    "SchemaInvalidateCacheResponse",
    "SchemaPropertiesResponse",
    "SchemaStatisticsResponse",
    "SchemaStatusResponse",
    "SchemaSummaryRequest",
    "SchemaSummaryResponse",
    "SparqlExecuteRequest",
    "SparqlExecuteResponse",
    "SparqlExplainResponse",
    "SparqlProfileResponse",
    "TranslateRequest",
    "TranslateResponse",
    "ValidateRequest",
    "ValidateResponse",
]
