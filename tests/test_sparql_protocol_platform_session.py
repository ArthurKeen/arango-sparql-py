"""``/sparql`` resolves its session apart from the other routes. A platform
session there must follow the caller's forwarded login like every other route:
a request without it is refused, never served on the stored login (anyone with
the session token could otherwise query as the user who opened it), and a
rotated login re-binds the session. See ``security._follow_platform_identity``.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from arango_sparql.service import _sessions, app
from tests.test_platform_session import ENDPOINT, MOUNT, _patch_client, _platform_jwt, _Recorder

JWT_ALICE = _platform_jwt("alice")


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch):
    _sessions.clear()
    for env in ("INTEGRATION_HTTP_ADDRESS_FULL", "INTEGRATION_HTTP_ADDRESS"):
        monkeypatch.delenv(env, raising=False)
    yield
    _sessions.clear()


@pytest.fixture
def platform_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARANGO_DEPLOYMENT_ENDPOINT", ENDPOINT + "/")
    monkeypatch.setenv("ROOT_PATH", MOUNT)
    monkeypatch.delenv("ARANGO_SPARQL_PLATFORM_AUTH", raising=False)


def _protocol_request(headers: dict[str, str], query: str = "") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/sparql",
            "query_string": query.encode(),
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        }
    )


class TestSparqlProtocolSession:
    """``/sparql`` resolves its session separately; a platform session there
    must follow the caller's login like every other route."""

    def _session(self, monkeypatch) -> str:
        rec = _Recorder()
        _patch_client(monkeypatch, rec)
        r = TestClient(app).post(
            "/connect/platform", json={}, headers={"Authorization": f"Bearer {JWT_ALICE}"}
        )
        assert r.status_code == 200, r.text
        self.rec = rec
        return r.json()["token"]

    def test_a_request_without_the_platform_login_is_refused(self, platform_env: None, monkeypatch) -> None:
        from arango_sparql.service.routes.protocol import _resolve_protocol_session

        token = self._session(monkeypatch)
        with pytest.raises(HTTPException) as exc:
            _resolve_protocol_session(_protocol_request({"X-Arango-Session": token}))
        assert exc.value.status_code == 401

    def test_the_session_follows_a_rotated_login(self, platform_env: None, monkeypatch) -> None:
        from arango_sparql.service.routes.protocol import _resolve_protocol_session

        token = self._session(monkeypatch)
        rotated = _platform_jwt("alice-rotated")
        session = _resolve_protocol_session(
            _protocol_request({"X-Arango-Session": token, "Authorization": f"bearer {rotated}"})
        )
        assert session.platform_token == rotated
        assert self.rec.opened[-1]["user_token"] == rotated

    def test_a_query_parameter_session_still_works_with_the_login(
        self, platform_env: None, monkeypatch
    ) -> None:
        from arango_sparql.service.routes.protocol import _resolve_protocol_session

        token = self._session(monkeypatch)
        session = _resolve_protocol_session(
            _protocol_request({"Authorization": f"bearer {JWT_ALICE}"}, query=f"session={token}")
        )
        assert session.token == token
