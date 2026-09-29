"""Top-level package for ``arango-sparql-py``.

Public entry points:

- :func:`arango_sparql.api.translate` — SPARQL → AQL translation.
- :mod:`arango_sparql.service` — the FastAPI application.
- :mod:`arango_sparql.nl2sparql` — natural-language → SPARQL pipeline.
"""

from __future__ import annotations

# Single source of truth for the package version. Kept in lockstep with
# pyproject.toml [project].version (tests/test_version_parity.py enforces it);
# the BYOC deploy script (scripts/byoc_deploy.py) reads THIS value to tag and
# verify releases, and the FastAPI app reports it in openapi.json.
__version__ = "0.2.0"
