"""``/health`` and ``/version`` endpoints — cheap liveness checks."""

from __future__ import annotations

from ... import __version__
from ..app import app
from ..models import HealthResponse


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)
