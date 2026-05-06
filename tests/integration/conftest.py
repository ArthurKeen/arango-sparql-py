"""Shared infrastructure for tests that need a live ArangoDB.

Hosts a tiny set of helper functions that boot ArangoDB on demand via
``docker compose up -d arangodb``. Both the existing
``tests/integration/test_execute_endpoint.py`` integration suite and
the W3C live-execution harness (``tests/w3c/test_w3c_live_execution.py``)
depend on the same ``docker compose`` workflow, so the helpers live in
one place to keep the boot policy consistent.

The helpers are intentionally exposed as plain functions rather than
pytest fixtures: pytest auto-loads ``conftest.py`` only for tests under
the same directory, but Python imports the module just fine from
anywhere — so callers under ``tests/w3c/`` simply ``from
tests.integration.conftest import ...`` and reuse the same boot logic.

No state is owned here. All bootstrap is best-effort and ``False`` is
returned (rather than raising) when Docker is unavailable so callers
can ``pytest.skip(...)`` cleanly.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path

DEFAULT_ARANGO_HOST = os.getenv("ARANGO_HOST", "localhost")
DEFAULT_ARANGO_PORT = int(os.getenv("ARANGO_PORT", "8529"))
DEFAULT_ARANGO_URL = os.getenv("ARANGO_URL", f"http://{DEFAULT_ARANGO_HOST}:{DEFAULT_ARANGO_PORT}")
DEFAULT_ARANGO_USER = os.getenv("ARANGO_USER", "root")
DEFAULT_ARANGO_PASSWORD = os.getenv("ARANGO_PASSWORD", "rootpw")
DEFAULT_ARANGO_DB = os.getenv("ARANGO_TEST_DB", "_system")


def integration_enabled() -> bool:
    """Return ``True`` iff ``RUN_INTEGRATION`` is set to a truthy value.

    Exposed as a function (not a constant) so tests can import it after
    setting the env var inside a single test session, e.g. via
    ``monkeypatch``. Mirrors the gate that
    ``tests/integration/test_execute_endpoint.py`` already uses; lifting
    it here keeps both suites in lockstep on what "integration mode"
    means.
    """
    return os.getenv("RUN_INTEGRATION", "").lower() in ("1", "true", "yes")


def arangodb_reachable(
    host: str = DEFAULT_ARANGO_HOST,
    port: int = DEFAULT_ARANGO_PORT,
    *,
    timeout_s: float = 1.0,
) -> bool:
    """Cheap TCP probe — returns ``True`` iff a TCP connect to
    ``host:port`` succeeds within ``timeout_s`` seconds.

    Used as a pre-flight before paying the ``python-arango`` client
    cost: if nothing is listening, the caller can fall back to
    :func:`try_boot_arangodb_via_compose` instead of hitting an
    auth-handshake timeout.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def _repo_root() -> Path:
    """Resolve the repo root from this file's location.

    ``tests/integration/conftest.py`` → up two levels. We don't rely on
    ``cwd`` because pytest may be invoked from anywhere.
    """
    return Path(__file__).resolve().parents[2]


def try_boot_arangodb_via_compose(
    *,
    timeout_s: float = 60.0,
    compose_file: Path | None = None,
) -> bool:
    """Best-effort ``docker compose up -d arangodb`` then poll the
    container until TCP becomes reachable.

    Returns ``False`` (rather than raising) when:

    * ``docker-compose.yml`` is missing,
    * the ``docker`` binary isn't on ``PATH``,
    * ``docker compose`` fails (no daemon, image pull errors, …),
    * the boot exceeds ``timeout_s``.

    Callers translate a ``False`` return into a ``pytest.skip(...)`` so
    a developer without Docker still gets a clean test run.
    """
    repo_root = _repo_root()
    compose_yml = compose_file or (repo_root / "docker-compose.yml")
    if not compose_yml.is_file():
        return False
    try:
        subprocess.run(
            ["docker", "compose", "-f", str(compose_yml), "up", "-d", "arangodb"],
            check=True,
            capture_output=True,
            timeout=30.0,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if arangodb_reachable():
            # ArangoDB accepts TCP before it accepts authenticated
            # requests; a brief pause lets the auth subsystem warm up
            # so the very first request after boot doesn't 401.
            time.sleep(2.0)
            return True
        time.sleep(1.0)
    return False
