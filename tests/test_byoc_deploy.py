"""Unit tests for the BYOC deploy tooling (`scripts/byoc_deploy.py` + `entrypoint`).

These are the pure, network-free pieces — version parsing, build-suffix
selection, mount paths, relative/absolute asset resolution, dotenv parsing, the
loopback guard — plus the two "fail closed" behaviors the deploy verifier relies
on (an unreadable openapi.json when a version was demanded, and a UI deploy whose
page carries no assets). The platform I/O itself is exercised against fakes; no
real ArangoDB / Container Manager is contacted.
"""

from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

pytest.importorskip("requests", reason="byoc_deploy.py imports requests")

_REPO = Path(__file__).resolve().parent.parent


def _load(path: Path, name: str):
    # SourceFileLoader (not spec_from_file_location) so the extensionless
    # `entrypoint` file loads too — importlib can't infer a loader from its name.
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


bd = _load(_REPO / "scripts" / "byoc_deploy.py", "byoc_deploy_under_test")
ep = _load(_REPO / "entrypoint", "byoc_entrypoint_under_test")


# --- pure helpers ----------------------------------------------------------


def test_read_app_version_matches_package() -> None:
    import arango_sparql

    assert bd.read_app_version() == arango_sparql.__version__


def test_mount_path_scopes() -> None:
    assert bd.mount_path("svc", "mydb") == "/_service/uds/_db/mydb/svc"
    assert bd.mount_path("svc", None) == "/_service/uds/_global/svc"


def test_service_id_of_reads_nested_and_flat() -> None:
    assert bd._service_id_of({"serviceInfo": {"serviceId": "abc", "status": "DEPLOYED"}}) == (
        "abc",
        "DEPLOYED",
    )
    assert bd._service_id_of({"serviceId": "xyz", "status": "OK"}) == ("xyz", "OK")
    assert bd._service_id_of({}) == (None, None)


def test_load_env_strips_quotes_and_comments(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# a comment\nARANGO_URL=\"https://h:8529\"\nARANGO_USER='root'\n\nARANGO_DB=demo\n",
        encoding="utf-8",
    )
    env = bd.load_env(env_file)
    assert env == {"ARANGO_URL": "https://h:8529", "ARANGO_USER": "root", "ARANGO_DB": "demo"}


def test_asset_urls_resolves_relative_absolute_and_skips_external() -> None:
    html = (
        '<script src="./assets/index-a.js"></script>'
        '<link href="assets/index-b.css">'
        '<script src="/_service/uds/_global/svc/assets/c.js"></script>'
        '<script src="https://cdn.example.com/d.js"></script>'  # external → skipped
    )
    url = "https://host:8529/_service/uds/_global/svc/"
    got = bd._asset_urls(html, url, "https://host:8529")
    assert got == [
        "https://host:8529/_service/uds/_global/svc/assets/index-a.js",
        "https://host:8529/_service/uds/_global/svc/assets/index-b.css",
        "https://host:8529/_service/uds/_global/svc/assets/c.js",
    ]


def test_version_sort_key_is_natural() -> None:
    versions = ["0.2.0-2", "0.2.0-10", "0.2.0-1", "0.2.0-9"]
    assert sorted(versions, key=bd._version_sort_key) == ["0.2.0-1", "0.2.0-2", "0.2.0-9", "0.2.0-10"]


def test_next_build_version_skips_taken() -> None:
    class _P:
        def list_packages(self):
            return [
                {"name": "arango-sparql-py", "version": "0.2.0-1"},
                {"name": "arango-sparql-py", "version": "0.2.0-2"},
                {"name": "other", "version": "0.2.0-3"},
                {"name": "arango-sparql-py"},  # missing version → must not KeyError
            ]

    assert bd.next_build_version(_P(), "arango-sparql-py", "0.2.0") == "0.2.0-3"


# --- entrypoint loopback guard --------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://localhost:8529", True),
        ("http://127.0.0.1:8529", True),
        ("https://0.0.0.0:8529", True),
        ("https://prod.demo.pilot.arango.ai:8529", False),
        ("https://localhost-proxy.internal.arango.ai:8529", False),  # contains 'localhost' but not loopback
        ("https://db-localhost.example.com:8529", False),
    ],
)
def test_entrypoint_is_loopback(url: str, expected: bool) -> None:
    assert ep._is_loopback(url) is expected


# --- deep_verify fail-closed behaviors ------------------------------------


class _Resp:
    def __init__(self, status: int = 200, json_data=None, text: str = "") -> None:
        self.status_code = status
        self._json = json_data
        self.text = text
        self.headers: dict[str, str] = {}

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json


class _Session:
    def __init__(self, by_suffix: dict) -> None:
        self._by_suffix = by_suffix

    def get(self, url: str, **_kw):
        for suffix, resp in self._by_suffix.items():
            if url.endswith(suffix):
                return resp
        return _Resp(404, text="not found")


class _Platform:
    def __init__(self, session: _Session) -> None:
        self.session = session
        self.base = "https://host:8529"

    def _headers(self):
        return {}


_URL = "https://host:8529/_service/uds/_global/svc/"


def test_deep_verify_fails_closed_when_openapi_unreadable_and_version_expected() -> None:
    # openapi.json raises on .json(); a version was demanded -> must FAIL, not pass.
    session = _Session(
        {
            "openapi.json": _Resp(200, json_data=None),  # .json() raises
            "svc/": _Resp(200, text='<script src="./assets/a.js"></script>'),
            "assets/a.js": _Resp(200),
            "health": _Resp(200, text="ok"),
        }
    )
    assert bd.deep_verify(_Platform(session), _URL, "0.2.0", expect_ui=True) is False


def test_deep_verify_flags_ui_deploy_with_no_assets() -> None:
    # openapi fine, but a UI deploy whose root references no assets is blank -> FAIL.
    session = _Session(
        {
            "openapi.json": _Resp(200, json_data={"info": {"version": "0.2.0"}, "paths": {}}),
            "svc/": _Resp(200, text="<html><body>no assets here</body></html>"),
            "health": _Resp(200, text="ok"),
        }
    )
    assert bd.deep_verify(_Platform(session), _URL, None, expect_ui=True) is False


def test_deep_verify_passes_healthy_ui_deploy() -> None:
    session = _Session(
        {
            "openapi.json": _Resp(200, json_data={"info": {"version": "0.2.0"}, "paths": {"/health": {}}}),
            "svc/": _Resp(200, text='<script src="./assets/a.js"></script>'),
            "assets/a.js": _Resp(200),
            "health": _Resp(200, text='{"status":"ok"}'),
        }
    )
    assert bd.deep_verify(_Platform(session), _URL, "0.2.0", expect_ui=True) is True


def test_deep_verify_passes_bare_api_with_no_ui() -> None:
    # --no-ui deploy: the root has no "/" route (404), but expect_ui=False so the
    # missing assets are fine; openapi + /health still confirm the right code.
    session = _Session(
        {
            "openapi.json": _Resp(200, json_data={"info": {"version": "0.2.0"}, "paths": {"/sparql": {}}}),
            "svc/": _Resp(404, text="Not Found"),
            "health": _Resp(200, text='{"status":"ok"}'),
        }
    )
    assert bd.deep_verify(_Platform(session), _URL, "0.2.0", expect_ui=False) is True


# --- review round 2: edge-case robustness ---------------------------------


def test_version_sort_key_tolerates_non_numeric_segment() -> None:
    # A stray tag like 0.2.0rc1 must not make sorted() compare int vs str.
    versions = ["0.2.0-10", "0.2.0-2", "0.2.0rc1", "0.2.0-1"]
    assert sorted(versions, key=bd._version_sort_key)  # does not raise


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://localhost:8529", True),
        ("http://127.0.0.1:8529", True),
        ("https://0.0.0.0:8529", True),
        ("http://[::1]:8529", True),
        ("https://prod.demo.pilot.arango.ai:8529", False),
        ("https://localhost-proxy.internal.arango.ai:8529", False),
    ],
)
def test_url_is_loopback_matches_entrypoint(url: str, expected: bool) -> None:
    # byoc_deploy's preflight guard must reject exactly what the entrypoint does.
    assert bd._url_is_loopback(url) is expected
    assert ep._is_loopback(url) is expected


def test_load_env_handles_export_prefix(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("export ARANGO_URL=https://h:8529\nARANGO_USER=root\n", encoding="utf-8")
    env = bd.load_env(env_file)
    assert env["ARANGO_URL"] == "https://h:8529" and env["ARANGO_USER"] == "root"


def test_verify_parser_accepts_no_ui() -> None:
    args = bd.build_parser().parse_args(["verify", "--no-ui", "--expect-version", "0.2.0"])
    assert args.no_ui is True
