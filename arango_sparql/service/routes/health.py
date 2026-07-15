"""``/health`` and ``/version`` endpoints — liveness + readiness.

``/health`` stays a static liveness check (process is up, event loop
serves requests). ``/health/ready`` is the readiness probe: when the
deployment configures a default ArangoDB (``ARANGO_URL``), it issues a
cheap ``GET /_api/version``-level ping and returns 503 until the
server responds — so an orchestrator keeps the pod out of rotation
while the database is still booting. BYOC deployments with no default
server get 200 (each session brings its own credentials; there is no
server-level dependency to wait on).
"""

from __future__ import annotations

import logging

from fastapi import Response

from ... import __version__
from ..._env import read_arango_url
from ..app import app
from ..models import HealthResponse, ReadyResponse

logger = logging.getLogger(__name__)

_READY_TIMEOUT_SECONDS = 2.0


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)


@app.get("/health/ready", response_model=ReadyResponse)
def health_ready(response: Response) -> ReadyResponse:
    url = read_arango_url(caller="health_ready")
    if not url:
        return ReadyResponse(status="ok", version=__version__, arango="unconfigured")
    # stdlib on purpose — the probe must not add a runtime dependency
    # to the ``service`` extra. ``/_api/version`` requires auth on most
    # deployments; 401/403 still proves the server is up and accepting
    # connections, which is all readiness needs (session auth is
    # per-request).
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(  # noqa: S310 — URL comes from deployment env, not user input
            f"{url.rstrip('/')}/_api/version",
            timeout=_READY_TIMEOUT_SECONDS,
        ) as ping:
            reachable = ping.status < 500
    except urllib.error.HTTPError as exc:
        reachable = exc.code < 500
    except Exception as exc:  # noqa: BLE001 — any transport failure means not ready
        logger.info("readiness probe: ArangoDB at %s unreachable: %s", url, exc)
        reachable = False
    if not reachable:
        response.status_code = 503
        return ReadyResponse(status="degraded", version=__version__, arango="unreachable")
    return ReadyResponse(status="ok", version=__version__, arango="ok")
