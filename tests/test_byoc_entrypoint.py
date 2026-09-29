"""The BYOC `entrypoint` (repo-specific; the deploy logic lives in arango-byoc-deploy).

The entrypoint refuses to boot against a loopback ARANGO_URL, which cannot be
reached from inside the platform. The deploy tool's pre-flight must refuse
exactly the same URLs, so a bundle it passes is one this entrypoint will boot.
"""

from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent


def _load(path: Path, name: str):
    # SourceFileLoader so the extensionless `entrypoint` file loads.
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


ep = _load(_REPO / "entrypoint", "byoc_entrypoint_under_test")

_CASES = [
    ("https://localhost:8529", True),
    ("http://127.0.0.1:8529", True),
    ("https://0.0.0.0:8529", True),
    ("http://[::1]:8529", True),
    ("https://prod.demo.pilot.arango.ai:8529", False),
    ("https://localhost-proxy.internal.arango.ai:8529", False),  # contains 'localhost' but not loopback
    ("https://db-localhost.example.com:8529", False),
]


@pytest.mark.parametrize(("url", "expected"), _CASES)
def test_entrypoint_is_loopback(url: str, expected: bool) -> None:
    assert ep._is_loopback(url) is expected


@pytest.mark.parametrize(("url", "expected"), _CASES)
def test_deploy_preflight_rejects_exactly_what_the_entrypoint_does(url: str, expected: bool) -> None:
    """Runs when the `deploy` dependency group is installed (uv sync --group deploy)."""
    env = pytest.importorskip("arango_byoc_deploy.env", reason="deploy dependency group not installed")

    assert env.is_loopback(url) is expected


def test_entrypoint_line_one_is_the_platform_token() -> None:
    """The platform runs `python /project/<first word of entrypoint>`."""
    first = (_REPO / "entrypoint").read_text(encoding="utf-8").splitlines()[0]

    assert first.split()[0] == "entrypoint"
