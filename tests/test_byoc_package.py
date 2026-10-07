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


def _build(tmp_path: Path, *, root_path: str | None, include_env_file: Path | None = None) -> Path:
    out = tmp_path / "bundle.tar.gz"
    env = {"PACKAGE_NO_UI": "1", "PATH": __import__("os").environ.get("PATH", "")}
    if root_path is not None:
        env["SERVICE_ROOT_PATH"] = root_path
    if include_env_file is not None:
        env["PACKAGE_INCLUDE_ENV"] = "1"
        env["PACKAGE_ENV_FILE"] = str(include_env_file)
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


def test_arango_byoc_probes_survive_a_no_ui_deploy() -> None:
    """The deploy config must verify under --no-ui as well as the default UI.

    arango-byoc-deploy runs `probes` UNCONDITIONALLY and already checks the UI
    root itself (gated on has-ui). A bare "/" probe would therefore 404 a
    healthy --no-ui deploy (no root route). prefix-env-var/ready-path must also
    be set so the mount prefix is verified and readiness polls /health.
    """
    import tomllib

    cfg = tomllib.loads(_PYPROJECT_TEXT())["tool"]["arango-byoc"]
    assert cfg["prefix-env-var"] == "ROOT_PATH"
    assert cfg["ready-path"] == "/health"
    paths = [p["path"] for p in cfg["probes"]]
    assert "/" not in paths, f"a bare '/' probe breaks --no-ui verification: {paths}"
    assert "/health" in paths and "/openapi.json" in paths


def _PYPROJECT_TEXT() -> str:
    return (_REPO / "pyproject.toml").read_text(encoding="utf-8")


def test_sanitized_env_bake_keeps_service_keys_and_strips_the_rest(tmp_path: Path) -> None:
    """PACKAGE_INCLUDE_ENV bakes ONLY an allowlist — publish tokens / test flags
    in a developer .env must never ship in the deployment artifact (the deploy
    preflight only catches *_API_KEY, so this packager allowlist is the guard)."""
    src = tmp_path / "src.env"
    src.write_text(
        "# dev env\n"
        "ARANGO_URL=https://h:8529\n"
        "ARANGO_PASSWORD=supersecret\n"
        "ARANGO_SPARQL_PUBLIC_MODE=true\n"
        "OPENAI_API_KEY=sk-abc\n"
        "export ARANGO_DB=IAM\n"
        "PYPI_TOKEN=pypi-xxx\n"
        "PYPI_PASSWORD=zzz\n"
        "RUN_EVAL=1\n"
        "GITHUB_TOKEN=ghp_yyy\n",
        encoding="utf-8",
    )
    tar = _build(tmp_path, root_path="/_service/uds/_db/IAM/arango-sparql-py", include_env_file=src)
    baked = _member(tar, ".env")
    assert baked is not None
    # service keys kept (incl. the needed ARANGO_PASSWORD, an allowed LLM key, and
    # an `export `-prefixed line)
    for keep in (
        "ARANGO_URL=",
        "ARANGO_PASSWORD=",
        "ARANGO_SPARQL_PUBLIC_MODE=",
        "OPENAI_API_KEY=",
        "ARANGO_DB=IAM",
    ):
        assert keep in baked, f"{keep} should be baked"
    # non-service secrets / flags stripped
    for leaked in ("PYPI_TOKEN", "PYPI_PASSWORD", "RUN_EVAL", "GITHUB_TOKEN"):
        assert leaked not in baked, f"{leaked} must not ship in the bundle"
    # ROOT_PATH still appended alongside the sanitized keys
    assert "ROOT_PATH=/_service/uds/_db/IAM/arango-sparql-py" in baked
