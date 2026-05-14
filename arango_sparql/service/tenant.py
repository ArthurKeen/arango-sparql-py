"""Per-request tenant resolution shared by every SPARQL route.

PRD §6.5.1 mandates that every read of a tenant-scoped entity gets a
``FILTER doc.<tenant_field> == @tenant`` predicate emitted by the
translator. The bind value is sourced from the session's
``X-Tenant-Id`` header (production path) or the
``ARANGO_SPARQL_DEFAULT_TENANT`` env variable (dev / single-tenant
fallback). This module owns that lookup so every route — Protocol
``/sparql``, RPC ``/translate``, ``/execute``, ``/explain``,
``/profile`` — sees the same precedence and the same edge-case
handling (whitespace-only headers, empty env strings, etc.).
"""

from __future__ import annotations

import os

from fastapi import Request

TENANT_HEADER = "X-Tenant-Id"
TENANT_DEFAULT_ENV = "ARANGO_SPARQL_DEFAULT_TENANT"


def resolve_tenant_id(request: Request) -> str | None:
    """Resolve the per-request tenant identifier (PRD §6.5.1).

    Sources, in priority order:

    1. ``X-Tenant-Id`` header — the production path.
    2. ``ARANGO_SPARQL_DEFAULT_TENANT`` env var — dev / single-tenant
       fallback so an operator running the service against a
       multi-tenant DB without per-request tenant headers still gets
       a deterministic tenant scope rather than a silent leak.
    3. ``None`` — no tenant context. Tenant-scoped ontologies that
       resolve a class with ``phys:tenantField`` set will then raise
       :class:`~arango_sparql.errors.CrossTenantJoinError` from the
       visitor; single-tenant ontologies (no class declares
       ``phys:tenantField``) ignore this case entirely.

    Header values are stripped — empty / whitespace-only headers fall
    through to the env default rather than committing to ``""`` as a
    valid tenant name (a tenant id of ``""`` would silently match
    documents with no ``tenant_field`` at all).
    """
    raw = request.headers.get(TENANT_HEADER)
    if raw is not None:
        stripped = raw.strip()
        if stripped:
            return stripped
    env_value = os.environ.get(TENANT_DEFAULT_ENV)
    if env_value is not None:
        env_stripped = env_value.strip()
        if env_stripped:
            return env_stripped
    return None
