"""CI-gated p95 latency gate for ``POST /translate`` — cold vs warm rows.

Delivers two of the three D-08 CI-blocking perf rows (PRD §9.4):

* ``translate_cold_p95_ms`` — a distinct, never-before-seen ontology +
  query is built for every iteration (fresh Turtle parse, fresh
  resolver, fresh SPARQL parse each time — "cold mapping" per §9.4).
* ``translate_warm_p95_ms`` — the exact same ontology + query is reused
  across every iteration (steady-state / repeated-request shape).

Fully in-process: ``/translate`` never touches a database (it works
without a session — see ``translate_endpoint``'s optional session
dependency), so no ``_FakeArangoClient`` double is needed here. Zero
Docker, zero real I/O.

Percentiles use the stdlib ``statistics.quantiles`` helper re-exported
from :mod:`tests.perf.conftest` (``p95``) — no ``pytest-benchmark``, no
``numpy``/``scipy``.

**Gating** is shared with the ``/execute`` row in :mod:`tests.perf.gate`:
best-of-3 p95 enforced against the PRD §9.4 SLO * 1.25 in every
environment, plus an advisory, hardware-independent regression tripwire
(median cost relative to an interleaved ``GET /health``).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import arango_sparql.service as svc
from arango_sparql.service import app
from arango_sparql.service.security import _TokenBucket
from tests.perf.gate import gate, measure

pytestmark = pytest.mark.perf

# ---------------------------------------------------------------------------
# WARM row fixture data — identical payload reused every iteration.
# ---------------------------------------------------------------------------
_WARM_ONTOLOGY_TTL = """
@prefix : <http://ex.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

:Person a owl:Class ;
    phys:collectionName "Person" .

:name a owl:DatatypeProperty ;
    rdfs:domain :Person ;
    rdfs:range <http://www.w3.org/2001/XMLSchema#string> .
"""

_WARM_QUERY = """
PREFIX : <http://ex.org/>
SELECT ?s ?n WHERE {
  ?s a :Person ;
     :name ?n .
}
LIMIT 5
"""


def _cold_ttl(i: int) -> str:
    """Build a never-before-seen ontology for iteration *i*.

    A distinct class/property local name each time forces a genuinely
    fresh Turtle parse + resolver build every call — the "cold mapping"
    row measures a schema the process has never resolved before,
    as opposed to the warm row's steady-state repeat.
    """
    return f"""
@prefix : <http://ex.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix phys: <https://arango.solutions/phys#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

:Person{i} a owl:Class ;
    phys:collectionName "Person" .

:name{i} a owl:DatatypeProperty ;
    rdfs:domain :Person{i} ;
    rdfs:range <http://www.w3.org/2001/XMLSchema#string> .
"""


def _cold_query(i: int) -> str:
    return f"""
PREFIX : <http://ex.org/>
SELECT ?s ?n WHERE {{
  ?s a :Person{i} ;
     :name{i} ?n .
}}
LIMIT 5
"""


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # A high-capacity bucket keeps the loop deterministic regardless of the
    # default COMPUTE_RATE_LIMIT_PER_MINUTE=100 (the /health calibration
    # requests count too).
    monkeypatch.setattr(svc, "_compute_bucket", _TokenBucket(100_000))
    return TestClient(app)


def _health(client: TestClient) -> None:
    assert client.get("/health").status_code == 200


def test_translate_cold_p95(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)

    def request(i: int) -> None:
        resp = client.post("/translate", json={"sparql": _cold_query(i), "ontology_ttl": _cold_ttl(i)})
        assert resp.status_code == 200, resp.text

    gate("translate_cold_p95_ms", measure(request, lambda: _health(client)))


def test_translate_warm_p95(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    body = {"sparql": _WARM_QUERY, "ontology_ttl": _WARM_ONTOLOGY_TTL}

    def request(_i: int) -> None:
        resp = client.post("/translate", json=body)
        assert resp.status_code == 200, resp.text

    gate("translate_warm_p95_ms", measure(request, lambda: _health(client)))
