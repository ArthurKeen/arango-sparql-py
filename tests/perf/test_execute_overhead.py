"""CI-gated p95 latency gate for ``POST /execute`` overhead.

Delivers the third D-08 CI-blocking perf row (PRD §9.4): translate +
dispatch overhead, EXPLICITLY EXCLUDING AQL execution time. The only
real I/O ``/execute`` performs is ``session.db.aql.execute(...)``
(``arango_sparql/service/routes/sparql.py``) — this file monkeypatches
``arango_sparql.service.ArangoClient`` to the proven
``_FakeArangoClient``/``_FakeDb``/``_FakeCursor`` double (re-exported
from :mod:`tests.perf.conftest`, single source of truth in
``tests.test_service_sparql_routes``) so that call returns an instant
fake cursor. Without this stub the row would silently become
Docker-dependent (04-RESEARCH.md Pitfall 2) — stubbing it is correct
because the row is *defined* as translate+dispatch excluding AQL exec,
not a workaround.

Percentiles use the stdlib ``statistics.quantiles`` helper (``p95``,
re-exported from :mod:`tests.perf.conftest`) — no ``pytest-benchmark``,
no ``numpy``/``scipy``.

**Gating** is shared with the ``/translate`` rows in
:mod:`tests.perf.gate`: best-of-3 p95 enforced against the PRD §9.4 SLO
* 1.25 in every environment, plus an advisory, hardware-independent
regression tripwire (median cost relative to an interleaved ``GET /health``).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import arango_sparql.service as svc
from arango_sparql.service import app
from arango_sparql.service.security import _TokenBucket
from tests.perf.conftest import _connect_session
from tests.perf.gate import gate, measure

pytestmark = pytest.mark.perf

# Minimal ontology mapping :Thing to a collection so the resolver can
# translate the ASK query below without a W_SCHEMA_DEFAULT_COLLECTION
# fallback warning muddying the row.
_ONTOLOGY_TTL = """
@prefix : <http://ex.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .

:Thing a owl:Class ;
    phys:collectionName "Thing" .
"""

# AQL for the execute row is pinned to a trivial, always-cheap query
# ("ASK is essentially SELECT LIMIT 1" per tests/translate/ask.yml) so
# the fake cursor's instant return dominates — the row measures
# translate+dispatch overhead, not AQL execution time.
_ASK_QUERY = "PREFIX : <http://ex.org/> ASK { ?s a :Thing }"


def test_execute_overhead_p95(monkeypatch: pytest.MonkeyPatch, fake_client_factory: type) -> None:
    # fake_client_factory (session-scoped fixture from tests.perf.conftest,
    # itself re-exported from tests.test_service_sparql_routes) already
    # monkeypatches svc.ArangoClient -- /connect never touches a real DB.
    monkeypatch.setattr(svc, "_compute_bucket", _TokenBucket(100_000))
    # /execute's analyzer-enrichment path (_analyzer_bundle_for_session ->
    # _get_or_acquire, strategy="auto") resolves an LLM provider from
    # OPENAI_API_KEY/ANTHROPIC_API_KEY/OPENROUTER_API_KEY/LLM_PROVIDER/
    # SCHEMA_ANALYZER_PROVIDER when arangodb-schema-analyzer is installed
    # (verified this session: a repo-local .env sets OPENAI_API_KEY, which
    # this route layer picks up via _resolve_analyzer_provider). It fails
    # fast against our _FakeDb (no .collections()) today and degrades
    # silently, but a CI-gated, Docker/network-free perf row must not
    # depend on that failure ordering never changing -- explicitly force
    # the deterministic-baseline path so this row never risks a live LLM
    # call regardless of the host's ambient env/.env configuration.
    for _var in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
        "LLM_PROVIDER",
        "SCHEMA_ANALYZER_PROVIDER",
    ):
        monkeypatch.delenv(_var, raising=False)
    client = TestClient(app)
    token = _connect_session(client)

    headers = {"Authorization": f"Bearer {token}"}
    body = {"sparql": _ASK_QUERY, "ontology_ttl": _ONTOLOGY_TTL}

    def request(_i: int) -> None:
        resp = client.post("/execute", headers=headers, json=body)
        assert resp.status_code == 200, resp.text

    def health() -> None:
        assert client.get("/health").status_code == 200

    gate("execute_overhead_p95_ms", measure(request, health))
