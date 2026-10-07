"""SPA static mount (standalone / BYOC "microservice + UI").

Tests ``arango_sparql.service.static`` in isolation on a throwaway FastAPI app so
the module-level service singleton and every other test are untouched: the mount
appears only when ``ARANGO_SPARQL_UI_DIR`` names a built bundle, and it never
shadows an API route registered before it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from arango_sparql.service.static import mount_spa, resolve_ui_dir

_UI_ENV = "ARANGO_SPARQL_UI_DIR"


def _build_ui(root: Path) -> Path:
    (root / "index.html").write_text("<!doctype html><title>SPARQL Workbench</title>", encoding="utf-8")
    assets = root / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log('spa')", encoding="utf-8")
    return root


# --- resolve_ui_dir --------------------------------------------------------


def test_resolve_ui_dir_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_UI_ENV, raising=False)
    assert resolve_ui_dir() is None


def test_resolve_ui_dir_none_when_no_index(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(_UI_ENV, str(tmp_path))  # dir exists but has no index.html
    assert resolve_ui_dir() is None


def test_resolve_ui_dir_returns_dir_with_index(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _build_ui(tmp_path)
    monkeypatch.setenv(_UI_ENV, str(tmp_path))
    assert resolve_ui_dir() == tmp_path


# --- mount_spa -------------------------------------------------------------


def test_mount_spa_serves_ui_but_api_route_still_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _build_ui(tmp_path)
    monkeypatch.setenv(_UI_ENV, str(tmp_path))

    app = FastAPI()

    @app.get("/health")
    def _health() -> dict[str, str]:
        return {"status": "ok"}

    assert mount_spa(app) is True
    client = TestClient(app)

    # API route registered before the mount is NOT shadowed by the catch-all SPA.
    assert client.get("/health").json() == {"status": "ok"}
    # SPA served at the root and its assets.
    root = client.get("/")
    assert root.status_code == 200 and "SPARQL Workbench" in root.text
    assert client.get("/assets/app.js").status_code == 200


def test_mount_spa_is_noop_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_UI_ENV, raising=False)
    app = FastAPI()
    assert mount_spa(app) is False
    assert TestClient(app).get("/").status_code == 404  # no SPA, bare API


def test_mount_spa_idempotent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _build_ui(tmp_path)
    monkeypatch.setenv(_UI_ENV, str(tmp_path))
    app = FastAPI()
    assert mount_spa(app) is True
    assert mount_spa(app) is True  # second call is a no-op, not a duplicate mount
    assert sum(getattr(r, "name", None) == "spa" for r in app.router.routes) == 1


# --- cache headers (ported from arango-cypher-py a4716c7) -------------------


def test_index_html_is_never_cached_so_a_redeploy_takes_effect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The shell names the hashed bundle; a cached shell keeps running the
    # previous release's UI after a redeploy.
    _build_ui(tmp_path)
    monkeypatch.setenv(_UI_ENV, str(tmp_path))
    app = FastAPI()
    assert mount_spa(app)
    client = TestClient(app)
    for path in ("/", "/index.html"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert resp.headers["cache-control"] == "no-cache, no-store, must-revalidate", path


def test_hashed_assets_are_immutable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _build_ui(tmp_path)
    monkeypatch.setenv(_UI_ENV, str(tmp_path))
    app = FastAPI()
    assert mount_spa(app)
    resp = TestClient(app).get("/assets/app.js")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_other_root_files_keep_the_default_policy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _build_ui(tmp_path)
    (tmp_path / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    monkeypatch.setenv(_UI_ENV, str(tmp_path))
    app = FastAPI()
    assert mount_spa(app)
    resp = TestClient(app).get("/favicon.svg")
    assert resp.status_code == 200
    assert "cache-control" not in resp.headers
