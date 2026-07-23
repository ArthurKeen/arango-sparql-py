"""Multi-tenancy header forwarding tests for the W3C SPARQL Protocol
endpoint (PRD §6.5.1).

Lives next to the other ``/sparql`` integration tests so it inherits
the same fakes and isolation fixtures from
:mod:`tests.sparql_protocol.conftest`. The visitor's tenant logic is
covered in detail by ``tests/translate/test_translate_multitenancy_goldens.py``;
this module's job is the *route layer* — that ``/sparql`` (both GET and
POST) reads ``X-Tenant-Id`` (or ``ARANGO_SPARQL_DEFAULT_TENANT``) and
threads it through to the visitor so the AQL the fake driver receives
is tenant-scoped.
"""

from __future__ import annotations

from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from arango_sparql.service import _sessions
from arango_sparql.service.routes import schema as _schema
from arango_sparql.translate.mapping import MappingBundle, MappingSource

from .conftest import set_aql_rows

# Tenant-scoped ontology — every Person row carries a ``tenant_id``
# column the visitor must FILTER on. Cross-tenant joins (e.g. Person +
# ExternalAudit) are rejected by the visitor regardless of header.
_TENANT_OWL_TTL = """
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .
@prefix ex:   <http://ex.org/> .

ex:Person a owl:Class ;
    phys:collectionName "Person" ;
    phys:mappingStyle "COLLECTION" ;
    phys:tenantField "tenant_id" ;
    phys:tenantEntity "Org" .

ex:ExternalAudit a owl:Class ;
    phys:collectionName "ExternalAudit" ;
    phys:mappingStyle "COLLECTION" ;
    phys:tenantField "audit_tenant" ;
    phys:tenantEntity "ExternalOrg" .
"""


_TENANT_BUNDLE = MappingBundle(
    physical_mapping={
        "entities": {
            "Person": {
                "style": "COLLECTION",
                "collectionName": "Person",
                "tenantField": "tenant_id",
                "tenantEntity": "Org",
            },
            "ExternalAudit": {
                "style": "COLLECTION",
                "collectionName": "ExternalAudit",
                "tenantField": "audit_tenant",
                "tenantEntity": "ExternalOrg",
            },
        },
        "relationships": {},
    },
    owl_turtle=_TENANT_OWL_TTL,
    source=MappingSource(kind="manual"),
)


_TENANT_SELECT = "SELECT ?x WHERE { ?x a <http://ex.org/Person> } LIMIT 5"
_CROSS_TENANT_SELECT = (
    "SELECT ?x ?a WHERE { ?x a <http://ex.org/Person> . ?a a <http://ex.org/ExternalAudit> } LIMIT 5"
)


@pytest.fixture
def tenant_session_token(client: TestClient, fake_arango: type) -> str:
    """Like ``session_token`` but seeds the cache with the
    tenant-scoped bundle so ``/sparql`` exercises the tenant guard
    instead of the single-tenant happy path."""
    resp = client.post(
        "/connect",
        json={
            "url": "http://localhost:8529",
            "database": "test_db",
            "username": "root",
            "password": "<test-stub-pw>",
        },
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["token"]
    _schema._resolve_schema_cache().put("test_db", _TENANT_BUNDLE)
    return token


def _last_call(token: str) -> tuple[str, dict]:
    """Return the (aql, bind_vars) tuple of the most recent execute()
    on the session's fake driver. Tests assert against the AQL string
    because the protocol endpoint serialises results, not the AQL —
    the AQL is the contract surface for the visitor's tenant FILTER.
    """
    session = _sessions[token]
    calls = session.db.aql.calls
    assert calls, "no AQL execute() recorded — request never reached the driver"
    aql, bind_vars, _kwargs = calls[-1]
    return aql, bind_vars


def test_get_sparql_forwards_tenant_header(client: TestClient, tenant_session_token: str) -> None:
    """``GET /sparql?query=…`` reads ``X-Tenant-Id`` and the AQL the
    driver sees carries ``FILTER doc.tenant_id == @<bind>`` with the
    header value as the bind."""
    set_aql_rows(tenant_session_token, [])
    resp = client.get(
        f"/sparql?query={quote(_TENANT_SELECT)}",
        headers={
            "X-Arango-Session": tenant_session_token,
            "X-Tenant-Id": "tenant-protocol-get",
        },
    )
    assert resp.status_code == 200, resp.text
    aql, bind_vars = _last_call(tenant_session_token)
    assert "FILTER doc1.tenant_id == @" in aql
    assert "tenant-protocol-get" in bind_vars.values()


def test_post_sparql_forwards_tenant_header(client: TestClient, tenant_session_token: str) -> None:
    """``POST /sparql`` (application/sparql-query body) threads the
    header the same way as the GET form."""
    set_aql_rows(tenant_session_token, [])
    resp = client.post(
        "/sparql",
        content=_TENANT_SELECT,
        headers={
            "X-Arango-Session": tenant_session_token,
            "X-Tenant-Id": "tenant-protocol-post",
            "Content-Type": "application/sparql-query",
        },
    )
    assert resp.status_code == 200, resp.text
    aql, bind_vars = _last_call(tenant_session_token)
    assert "FILTER doc1.tenant_id == @" in aql
    assert "tenant-protocol-post" in bind_vars.values()


def test_sparql_falls_back_to_env_default_tenant(
    client: TestClient,
    tenant_session_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No header but ``ARANGO_SPARQL_DEFAULT_TENANT`` set ⇒ the env
    value is forwarded — the single-tenant deployment ergonomics
    PRD §6.5.1 calls out (operator doesn't have to inject the
    header on every cURL)."""
    monkeypatch.setenv("ARANGO_SPARQL_DEFAULT_TENANT", "env-default-protocol")
    set_aql_rows(tenant_session_token, [])
    resp = client.get(
        f"/sparql?query={quote(_TENANT_SELECT)}",
        headers={"X-Arango-Session": tenant_session_token},
    )
    assert resp.status_code == 200, resp.text
    _aql, bind_vars = _last_call(tenant_session_token)
    assert "env-default-protocol" in bind_vars.values()


def test_sparql_without_tenant_for_scoped_class_returns_422(
    client: TestClient,
    tenant_session_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tenant-scoped class + no ``X-Tenant-Id`` and no env fallback
    ⇒ 422 ``E_TRANSLATE_CROSS_TENANT_JOIN`` rather than silently
    leaking rows across tenants. The driver must NOT receive an
    AQL execute call (translation failed before DB)."""
    monkeypatch.delenv("ARANGO_SPARQL_DEFAULT_TENANT", raising=False)
    set_aql_rows(tenant_session_token, [])
    resp = client.get(
        f"/sparql?query={quote(_TENANT_SELECT)}",
        headers={"X-Arango-Session": tenant_session_token},
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["code"] == "E_TRANSLATE_CROSS_TENANT_JOIN"
    session = _sessions[tenant_session_token]
    assert session.db.aql.calls == []


def test_sparql_cross_tenant_join_returns_422(client: TestClient, tenant_session_token: str) -> None:
    """Joining two classes with different ``phys:tenantEntity`` roots
    is structurally forbidden — no tenant header makes it valid."""
    set_aql_rows(tenant_session_token, [])
    resp = client.get(
        f"/sparql?query={quote(_CROSS_TENANT_SELECT)}",
        headers={
            "X-Arango-Session": tenant_session_token,
            "X-Tenant-Id": "tenant-alpha",
        },
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["code"] == "E_TRANSLATE_CROSS_TENANT_JOIN"
    session = _sessions[tenant_session_token]
    assert session.db.aql.calls == []
