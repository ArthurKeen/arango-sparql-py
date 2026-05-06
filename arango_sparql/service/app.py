"""FastAPI app factory + startup-time guards.

Mirrors ``arango_cypher.service.app``:

1. The ``app = FastAPI(...)`` instance every route module decorates.
2. CORS middleware with the same credentialed-wildcard guardrail.
3. The ``ARANGO_SPARQL_PUBLIC_MODE`` flag readout that flips the
   service from local-dev defaults to public-internet defaults.
"""

from __future__ import annotations

import logging as _logging
import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="Arango SPARQL Transpiler",
    description="SPARQL 1.1 → AQL translation service for ArangoDB",
    version="0.1.0",
    root_path=os.getenv("ROOT_PATH", ""),
)

# Public-mode flag: matches arango-cypher-py's ARANGO_CYPHER_PUBLIC_MODE.
# Single switch that flips the service from "single-user / local-dev /
# inside-trusted-network" defaults to "shared / multi-user / public-internet"
# defaults. Read once at import time so the running config stays
# deterministic for an operator.
_PUBLIC_MODE = os.getenv("ARANGO_SPARQL_PUBLIC_MODE", "").lower() in ("true", "1", "yes")

_svc_logger = _logging.getLogger("arango_sparql.service")

_cors_origins_raw = os.getenv("CORS_ALLOWED_ORIGINS", "*")
_cors_origins = (
    ["*"]
    if _cors_origins_raw.strip() == "*"
    else [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]
)

# CORS credentialed-wildcard guardrail — see arango-cypher-py/service/app.py
# for the full rationale; the matrix is identical here.
_cors_credentials_raw = os.getenv("ARANGO_SPARQL_CORS_CREDENTIALS")
_cors_is_wildcard = _cors_origins == ["*"]
if _cors_is_wildcard and _cors_credentials_raw and _cors_credentials_raw.lower() in ("1", "true", "yes"):
    raise RuntimeError(
        "Refusing to start: CORS_ALLOWED_ORIGINS='*' combined with "
        "ARANGO_SPARQL_CORS_CREDENTIALS=true is unsafe. Pin an explicit "
        "origin list or unset ARANGO_SPARQL_CORS_CREDENTIALS."
    )
if _cors_is_wildcard:
    _cors_credentials = False
    if _cors_credentials_raw is None:
        _svc_logger.warning(
            "CORS_ALLOWED_ORIGINS='*' detected; allow_credentials forced off. "
            "Pin an explicit origin list to enable credentialed CORS."
        )
else:
    _cors_credentials = True
    if _cors_credentials_raw is not None:
        _cors_credentials = _cors_credentials_raw.lower() in ("1", "true", "yes")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Observability spine. Imported here (rather than from the package
# init) so the middleware install happens at app-construction time,
# alongside CORS, and the logging filter / handler attachment runs
# before any route module's import-time logger.* calls.
#
# Middleware order matters: ``CorrelationIdMiddleware`` is added *after*
# ``CORSMiddleware`` above, which means it runs *first* on the inbound
# path (Starlette wraps middlewares LIFO). That's deliberate — we want
# the correlation ID minted before the CORS preflight handler emits its
# log line, not after, so even rejected preflights carry an X-Request-Id
# in the log trail for cross-referencing client / server traces.
from .observability import CorrelationIdMiddleware, configure_observability  # noqa: E402

configure_observability()
app.add_middleware(CorrelationIdMiddleware)
