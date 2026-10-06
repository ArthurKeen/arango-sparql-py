"""`scripts/package_arango_manual.sh` bakes ROOT_PATH for a prefixed mount.

The Container Manager drops app env at deploy time, so the service's mount
prefix must be baked into the bundle's `.env` (the FastAPI app reads it via
load_dotenv → root_path). Without it, /openapi.json, /docs and the API resolve
at the cluster root. This guards that the packager bakes ROOT_PATH when
SERVICE_ROOT_PATH is set — and does not, otherwise — using PACKAGE_NO_UI=1 so
the test needs no npm.
"""

from __future__ import annotations

import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO / "scripts" / "package_arango_manual.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or not _SCRIPT.is_file(),
    reason="needs bash + the packager script",
)


def _build(tmp_path: Path, *, root_path: str | None) -> Path:
    out = tmp_path / "bundle.tar.gz"
    env = {"PACKAGE_NO_UI": "1", "PATH": __import__("os").environ.get("PATH", "")}
    if root_path is not None:
        env["SERVICE_ROOT_PATH"] = root_path
    subprocess.run(
        ["bash", str(_SCRIPT), str(out)],
        cwd=str(_REPO),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return out


def _member(tar: Path, name: str) -> str | None:
    with tarfile.open(tar, "r:gz") as archive:
        for candidate in (name, f"./{name}"):
            try:
                handle = archive.extractfile(candidate)
            except KeyError:
                handle = None
            if handle:
                return handle.read().decode("utf-8")
    return None


def test_bakes_root_path_when_service_root_path_set(tmp_path: Path) -> None:
    mount = "/_service/uds/_db/IAM/arango-sparql-py"
    tar = _build(tmp_path, root_path=mount)
    baked = _member(tar, ".env")
    assert baked is not None, "no .env was baked into the bundle"
    assert f"ROOT_PATH={mount}" in baked
    # The ROOT_PATH-only bundle must not carry app secrets.
    assert "ARANGO_PASSWORD" not in baked and "OPENAI_API_KEY" not in baked


def test_no_root_path_without_service_root_path(tmp_path: Path) -> None:
    tar = _build(tmp_path, root_path=None)
    baked = _member(tar, ".env")
    # Either no .env at all, or one without ROOT_PATH — never a bare prefix.
    assert baked is None or "ROOT_PATH=" not in baked


def test_bundle_is_flat_with_entrypoint_at_root(tmp_path: Path) -> None:
    tar = _build(tmp_path, root_path="/_service/uds/_db/IAM/arango-sparql-py")
    with tarfile.open(tar, "r:gz") as archive:
        names = {n.lstrip("./") for n in archive.getnames()}
    assert {"entrypoint", "pyproject.toml", "arango_sparql/__init__.py"} <= names
