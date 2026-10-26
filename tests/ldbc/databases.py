"""The LDBC harness's ArangoDB: one database per scale factor and layout.

The harness runs against its own server (``docker-compose.ldbc.yml``, port
8533), not the integration suite's: that server is memory-capped and told
its cap, so ArangoDB sizes its caches to fit (a shared Docker VM got it
OOM-killed under the LDBC load), and benchmark timings do not compete with
test traffic. ``LDBC_ARANGO_URL`` / ``LDBC_ARANGO_USER`` /
``LDBC_ARANGO_PASSWORD`` point it elsewhere.

Each layout gets its own database (``ldbc_sf0_1_rpt`` …) so a load never
touches another layout.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from tests.ldbc.loader import Layout

ARANGO_URL = os.getenv("LDBC_ARANGO_URL", "http://localhost:8533")
ARANGO_USER = os.getenv("LDBC_ARANGO_USER", "root")
ARANGO_PASSWORD = os.getenv("LDBC_ARANGO_PASSWORD", os.getenv("ARANGO_ROOT_PASSWORD", "rootpw"))
COMPOSE_FILE = Path(__file__).resolve().parents[2] / "docker-compose.ldbc.yml"


def database_name(sf: str, layout: Layout) -> str:
    return f"ldbc_sf{sf.replace('.', '_')}_{layout}"


def reachable(timeout_s: float = 1.0) -> bool:
    parsed = urlparse(ARANGO_URL)
    try:
        with socket.create_connection(
            (parsed.hostname or "localhost", parsed.port or 8529), timeout=timeout_s
        ):
            return True
    except OSError:
        return False


def boot_via_compose(timeout_s: float = 90.0) -> bool:
    """Best-effort ``docker compose -f docker-compose.ldbc.yml up -d``; False if it cannot."""
    try:
        subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d"],
            check=True,
            capture_output=True,
            timeout=60.0,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if reachable():
            time.sleep(2.0)  # TCP comes up before authentication does
            return True
        time.sleep(1.0)
    return False


def open_database(sf: str, layout: Layout) -> Any:
    """Return a handle to the layout's database, creating it if missing."""
    from arango import ArangoClient

    from arango_sparql.arango_admin import ensure_database

    name = database_name(sf, layout)
    # Long request timeout: bulk imports and index builds on millions of
    # documents take minutes, not seconds.
    client = ArangoClient(hosts=ARANGO_URL.rstrip("/"), request_timeout=600)
    ensure_database(client, name, username=ARANGO_USER, password=ARANGO_PASSWORD)
    return client.db(name, username=ARANGO_USER, password=ARANGO_PASSWORD)
