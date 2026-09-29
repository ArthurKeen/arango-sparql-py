"""Optional single-page-app (SPA) static mount for standalone / BYOC deploys.

The transpiler ships a Vite/React workbench UI (``ui/``). In a library or
dev-server deployment the UI is served separately; in the **standalone / BYOC**
deployment (one container behind the ArangoDB Container Manager, see
``docs/BYOC_DEPLOYMENT.md``) the same process serves both the ``/sparql`` + NL
API *and* the UI.

This module mounts the built bundle when — and only when —
``ARANGO_SPARQL_UI_DIR`` points at a directory containing ``index.html``. Absent
or invalid, nothing is mounted and the service is exactly the bare API it was
before (so ``--no-ui`` deploys and every existing test are unaffected).

Two properties make this safe under a path-prefixed mount (the Container Manager
serves the app under ``/_service/uds/.../<instance>/``):

* The Vite build uses ``base: "./"`` (relative asset URLs, ``ui/vite.config.ts``),
  so ``index.html`` references ``./assets/…`` — which resolve against whatever
  prefix the page is served under, with no build-time prefix baked in.
* :data:`app` already honours ``ROOT_PATH`` for the API side.

The mount is installed **last** (after every route module has registered its
endpoints) so the catch-all static handler never shadows an API route.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger("arango_sparql.service")

_UI_DIR_ENV = "ARANGO_SPARQL_UI_DIR"


def resolve_ui_dir() -> Path | None:
    """Return the built-SPA directory to serve, or ``None`` to serve no UI.

    ``ARANGO_SPARQL_UI_DIR`` must name a directory holding ``index.html``. An
    unset/empty value means "no UI" (bare API); a set-but-invalid value is a
    misconfiguration we log loudly rather than silently ignore.
    """
    raw = os.getenv(_UI_DIR_ENV, "").strip()
    if not raw:
        return None
    directory = Path(raw)
    if (directory / "index.html").is_file():
        return directory
    logger.warning("%s=%s does not contain index.html — UI will not be served", _UI_DIR_ENV, raw)
    return None


def mount_spa(app: FastAPI) -> bool:
    """Mount the built SPA at ``/`` when configured; return whether it mounted.

    ``html=True`` serves ``index.html`` for the root and directory requests and
    404s for genuinely missing files (so ``/sparql`` etc. never resolve to the
    SPA — those are real API routes registered before this mount). Idempotent:
    a second call is a no-op if a ``spa`` mount already exists.
    """
    directory = resolve_ui_dir()
    if directory is None:
        return False
    if any(getattr(route, "name", None) == "spa" for route in app.router.routes):
        return True
    app.mount("/", StaticFiles(directory=str(directory), html=True), name="spa")
    logger.info("serving SPARQL workbench UI from %s at the app root", directory)
    return True
